"""Forward simulation: drive to the legal limit, choose a block at each decision point, repeat."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from legaleta.checker import validate
from legaleta.clock import Clock
from legaleta.policy import Policy
from legaleta.rules import RuleSet
from legaleta.state import DriverState, Envelope, apply, envelope
from legaleta.timeline import REST_KINDS, Block, Drive, Ferry, RouteItem, Stop, merge_drives


class SchedulingError(Exception):
    """The simulation cannot make progress (contradictory driver state or rules)."""


@dataclass(frozen=True)
class Warning:  # noqa: A001 - domain term; shadows the builtin only inside this module
    code: str
    message: str
    article: str = ""


@dataclass(frozen=True)
class StopResult:
    id: str
    arrival: int
    departure: int
    lateness_minutes: int
    window_met: bool
    rested_here: bool


@dataclass(frozen=True)
class PlanResult:
    blocks: list[Block]
    stops: list[StopResult]
    warnings: list[Warning]
    end_state: DriverState
    arrival: int
    rules: RuleSet
    policy: Policy


STANDING_WARNINGS: tuple[Warning, ...] = (
    Warning(
        "ART12_NOT_MODELLED",
        "Art 12 departures (up to +1 h / +2 h to reach base or home for a weekly rest) "
        "are not planned; they are the driver's decision on the road.",
        "Art 12",
    ),
    Warning(
        "RETURN_HOME_NOT_CHECKED",
        "The obligation to let the driver return home or to base every four weeks is not checked.",
        "Art 8(8a)",
    ),
    Warning(
        "WTD_NOT_CHECKED",
        "Working-time limits of Directive 2002/15/EC are not checked.",
        "Directive 2002/15/EC",
    ),
)


@dataclass(frozen=True)
class _Ctx:
    route: list[RouteItem]
    rules: RuleSet
    policy: Policy
    clock: Clock


@dataclass
class _Cursor:
    t: int
    state: DriverState
    idx: int = 0
    drive_left: int = 0
    blocks: list[Block] = field(default_factory=list)
    stops: list[StopResult] = field(default_factory=list)
    warnings: dict[str, Warning] = field(default_factory=dict)
    stops_done: int = 0
    lateness: int = 0
    penalty: float = 0.0
    extend_today: bool = False
    reduced_today: bool = False

    def copy(self) -> _Cursor:
        return _Cursor(
            self.t,
            self.state,
            self.idx,
            self.drive_left,
            list(self.blocks),
            list(self.stops),
            dict(self.warnings),
            self.stops_done,
            self.lateness,
            self.penalty,
            self.extend_today,
            self.reduced_today,
        )


@dataclass(frozen=True)
class Choice:
    name: str
    blocks: tuple[Block, ...]
    where: str  # "road" | "stop" | "ferry"
    penalty: float = 0.0
    needed_only: bool = False  # offered only when the plain option is late (policy "when_needed")
    sets_extend: bool = False
    sets_reduced: bool = False


Chooser = Callable[[_Ctx, _Cursor, list[Choice]], Choice]

_PLAIN_ORDER = (
    "continue",
    "extend_day",
    "accept_reduced_rest",
    "break_30",
    "break_45",
    "daily_rest_split_part_2",
    "daily_rest_reduced",
    "daily_rest_regular",
    "daily_rest_split_part_1",
    "weekly_rest_reduced",
    "weekly_rest_regular",
    "wait_next_week",
)
_RANK = {n: i for i, n in enumerate(_PLAIN_ORDER)}


def _base_name(name: str) -> str:
    return name.removesuffix("_here").removesuffix("_ferry").removesuffix("_before")


def plain_chooser(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> Choice:
    """Latest-legal: continue when possible, else the smallest block that unblocks."""
    primary = [c for c in choices if not c.needed_only] or choices
    return min(primary, key=lambda c: _RANK.get(_base_name(c.name), 99))


@dataclass(frozen=True)
class _Scored:
    choice: Choice
    score: float
    lateness: int
    slack: int
    order: int


def _score(ctx: _Ctx, cur: _Cursor, choice: Choice, order: int) -> _Scored:
    p = ctx.policy
    sim = cur.copy()
    _apply_choice(ctx, sim, choice)
    e = envelope(sim.state, ctx.rules, sim.t)
    # the nearest binding limit; a sum would let a daily rest (which resets two of the three)
    # win every tie with a break on the road
    slack = min(e.drive_before_break, e.drive_today, e.daily_window_ends_in)
    sim = _run(ctx, sim, plain_chooser, stop_budget=cur.stops_done + p.lookahead_stops)
    added_lateness = sim.lateness - cur.lateness
    score = (
        p.lateness_weight * added_lateness
        + p.arrival_weight * (sim.t - cur.t)
        + (sim.penalty - cur.penalty)
    )
    return _Scored(choice, score, added_lateness, slack, order)


def _best(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> _Scored:
    scored = [_score(ctx, cur, c, i) for i, c in enumerate(choices)]
    return min(scored, key=lambda s: (s.score, -s.slack, s.order))


def lookahead_chooser(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> Choice:
    """Score each choice by simulating the next policy.lookahead_stops stops (spec 5.2).

    Stage 1 scores the choices that are not needed_only; stage 2 (all choices) runs only
    when stage 1's best is late in the horizon (beyond the lateness at the stop being
    decided), and wins only with a strictly lower score and strictly less lateness.
    """
    if len(choices) == 1:
        return choices[0]
    stage1 = [c for c in choices if not c.needed_only] or choices
    best = _best(ctx, cur, stage1)
    if best.lateness > _unavoidable_lateness(ctx, cur) and len(stage1) < len(choices):
        best2 = _best(ctx, cur, choices)
        # a needed_only choice wins only by being less late, never on arrival time alone
        if best2.score < best.score and best2.lateness < best.lateness:
            best = best2
    return best.choice


def _unavoidable_lateness(ctx: _Ctx, cur: _Cursor) -> int:
    """Lateness at the stop being decided: the driver is already there, no choice changes it."""
    item = ctx.route[cur.idx] if cur.idx < len(ctx.route) else None
    if isinstance(item, Stop) and item.latest is not None:
        return max(0, cur.t - item.latest)
    return 0


# ---------------------------------------------------------------- public entry point


def plan(
    route: list[RouteItem], state: DriverState, rules: RuleSet, policy: Policy, clock: Clock
) -> PlanResult:
    items = merge_drives(list(route))
    ctx = _Ctx(items, rules, policy, clock)
    cur = _run(ctx, _Cursor(t=0, state=state), lookahead_chooser)
    violations = validate(cur.blocks, state, rules, clock)
    if violations:
        # late, never illegal: a plan that does not validate is never returned
        raise SchedulingError(
            "the driver state admits no legal plan: "
            + "\n".join(f"{v.article}: {v.message}" for v in violations)
        )
    warnings = list(STANDING_WARNINGS) + list(cur.warnings.values())
    return PlanResult(cur.blocks, cur.stops, warnings, cur.state, cur.t, rules, policy)


# ---------------------------------------------------------------- simulation loop


def _run(ctx: _Ctx, cur: _Cursor, chooser: Chooser, stop_budget: int | None = None) -> _Cursor:
    guard = 0
    while cur.idx < len(ctx.route):
        if stop_budget is not None and cur.stops_done >= stop_budget:
            break
        guard += 1
        if guard > 100_000:
            raise SchedulingError(
                "no progress after 100000 steps; check the driver state and rule overrides"
            )
        item = ctx.route[cur.idx]
        if isinstance(item, Drive):
            _step_drive(ctx, cur, chooser)
        elif isinstance(item, Stop):
            choice = chooser(ctx, cur, _stop_choices(ctx, cur, item))
            _apply_choice(ctx, cur, choice)
        else:
            choice = chooser(ctx, cur, _ferry_choices(ctx, cur, item))
            _apply_choice(ctx, cur, choice)
    return cur


def _warn(cur: _Cursor, code: str, message: str, article: str = "") -> None:
    cur.warnings.setdefault(code, Warning(code, message, article))


def _emit(ctx: _Ctx, cur: _Cursor, block: Block) -> None:
    if block.minutes == 0:
        return
    block = replace(block, start=cur.t)
    p = ctx.policy
    wanted_regular = p.daily_rest_type == "regular" or (
        p.daily_rest_type == "reduced_when_needed" and not cur.reduced_today
    )
    base = block.rest_minutes - block.compensation_minutes
    inside = _inside_window(ctx, cur, block.start, block.minutes)
    if (
        block.kind == "daily_rest"
        and block.subtype == "reduced"
        and base >= ctx.rules.daily_rest_regular
        and ctx.rules.daily_rest_reduced <= inside < ctx.rules.daily_rest_regular
        and wanted_regular
    ):
        _warn(
            cur,
            "DAILY_REST_REDUCED_BY_WINDOW",
            "A daily rest of 11 h or more started too late in its 24 h period: fewer than 11 h "
            "of it fall inside the period, so it counts as a reduced daily rest although the "
            "policy asked for regular ones.",
            "Art 8(2)",
        )
    cur.state = apply(cur.state, block, ctx.rules, ctx.clock)
    cur.t = block.end
    cur.blocks.append(block)
    if block.kind in REST_KINDS and cur.state.last_daily_rest_end == block.end:
        cur.extend_today = False
        cur.reduced_today = False
    if block.kind in ("wait", "break") and block.counts_as_rest and block.at is not None:
        _warn(
            cur,
            "WAIT_ASSUMED_REST",
            "Idle time at a stop (waiting for a window, or service the driver does not work) "
            "was counted as break or rest. Directive 2002/15/EC treats waiting of unknown "
            "duration as availability; set policy.wait_counts_as_rest or the stop's "
            "counts_as_rest to false to change this.",
            "Art 4(d); Art 4(f)",
        )
    if block.kind == "weekly_rest" and block.subtype == "regular" and block.at is None:
        _warn(
            cur,
            "REGULAR_WEEKLY_REST_ON_ROAD",
            "A regular weekly rest was placed between stops. It must not be taken in the "
            "vehicle: the driver needs accommodation there.",
            "Art 8(8)",
        )


def _apply_choice(ctx: _Ctx, cur: _Cursor, choice: Choice) -> None:
    if choice.where == "stop":
        stop = ctx.route[cur.idx]
        assert isinstance(stop, Stop)
        arrival = cur.t
        _commit(ctx, cur, choice)
        lateness = max(0, arrival - stop.latest) if stop.latest is not None else 0
        rested = any(b.kind in REST_KINDS and b.at == stop.id for b in choice.blocks)
        cur.stops.append(StopResult(stop.id, arrival, cur.t, lateness, lateness == 0, rested))
        cur.lateness += lateness
        cur.stops_done += 1
        cur.idx += 1
    elif choice.where == "ferry":
        _commit(ctx, cur, choice)
        cur.idx += 1
    else:
        _commit(ctx, cur, choice)


def _commit(ctx: _Ctx, cur: _Cursor, choice: Choice) -> None:
    for b in choice.blocks:
        _emit(ctx, cur, b)
    if choice.sets_extend:
        cur.extend_today = True
    if choice.sets_reduced:
        cur.reduced_today = True
    cur.penalty += choice.penalty


# ---------------------------------------------------------------- driving


def _shortest_daily_rest(ctx: _Ctx, cur: _Cursor, env: Envelope, honour_policy: bool = True) -> int:
    """The shortest daily rest that will be emitted next (Art 8(2), Art 8(4)).

    With honour_policy=False: the shortest the rules allow, whatever the policy prefers.
    """
    r, p = ctx.rules, ctx.policy
    if "split_part_2" in env.allowed_daily_rests:
        return r.daily_rest_split_second
    if "reduced" in env.allowed_daily_rests and (
        not honour_policy or p.daily_rest_type == "reduced_eager" or cur.reduced_today
    ):
        return r.daily_rest_reduced
    return r.daily_rest_regular


def _caps(ctx: _Ctx, cur: _Cursor, env: Envelope) -> list[tuple[int, str]]:
    p = ctx.policy
    allow_ext = p.extended_driving == "eager" or cur.extend_today
    today = (
        env.drive_today if (allow_ext and env.extension_available) else env.drive_today_unextended
    )
    return [
        (env.drive_before_break, "the 4.5 h break is due (Art 7)"),
        (today, "the daily driving limit (Art 6(1))"),
        (env.drive_this_week, "the weekly driving limit (Art 6(2))"),
        (env.drive_fortnight, "the two-week driving limit (Art 6(3))"),
        (
            env.daily_window_ends_in - _shortest_daily_rest(ctx, cur, env),
            "the daily rest must start (Art 8(2))",
        ),
        (env.weekly_rest_due_in, "the weekly rest must start (Art 8(6))"),
    ]


def _drive_limit(ctx: _Ctx, cur: _Cursor, env: Envelope) -> int:
    return max(0, min(m for m, _ in _caps(ctx, cur, env)))


def _binding(ctx: _Ctx, cur: _Cursor, env: Envelope) -> str:
    return min(_caps(ctx, cur, env), key=lambda c: c[0])[1]


def _drive_block(ctx: _Ctx, cur: _Cursor, minutes: int, reason: str) -> Block:
    extended = cur.state.driving_today + minutes > ctx.rules.daily_driving
    return Block(
        "drive",
        0,
        minutes,
        subtype="extended" if extended else None,
        reason=reason,
        article="Art 6; Art 7",
    )


def _step_drive(ctx: _Ctx, cur: _Cursor, chooser: Chooser) -> None:
    item = ctx.route[cur.idx]
    assert isinstance(item, Drive)
    if cur.drive_left == 0:
        cur.drive_left = item.minutes
    env = envelope(cur.state, ctx.rules, cur.t)
    limit = _drive_limit(ctx, cur, env)
    if limit >= cur.drive_left:
        _emit(ctx, cur, _drive_block(ctx, cur, cur.drive_left, "drive to the next route item"))
        cur.drive_left = 0
        cur.idx += 1
        return
    if limit > 0:
        _emit(ctx, cur, _drive_block(ctx, cur, limit, f"drive until {_binding(ctx, cur, env)}"))
        cur.drive_left -= limit
    choice = chooser(ctx, cur, _road_choices(ctx, cur))
    _apply_choice(ctx, cur, choice)


# ---------------------------------------------------------------- blocks for choices

_REST_TEXT: dict[tuple[str, str], tuple[str, str]] = {
    ("daily_rest", "regular"): ("regular daily rest of 11 h", "Art 8(2); Art 4(g)"),
    ("daily_rest", "reduced"): ("reduced daily rest of 9 h", "Art 8(2); Art 8(4)"),
    ("daily_rest", "split_part_1"): ("first part (3 h) of a split daily rest", "Art 4(g)"),
    ("daily_rest", "split_part_2"): (
        "second part (9 h) of a split daily rest",
        "Art 4(g); Art 8(2)",
    ),
    ("weekly_rest", "regular"): ("regular weekly rest of 45 h", "Art 8(6); Art 4(h)"),
    ("weekly_rest", "reduced"): ("reduced weekly rest of 24 h", "Art 8(6); Art 8(6b)"),
}


def _rest_minutes(ctx: _Ctx, kind: str, sub: str) -> int:
    r = ctx.rules
    if kind == "weekly_rest":
        return r.weekly_rest_regular if sub == "regular" else r.weekly_rest_reduced
    return {
        "regular": r.daily_rest_regular,
        "reduced": r.daily_rest_reduced,
        "split_part_1": r.daily_rest_split_first,
        "split_part_2": r.daily_rest_split_second,
    }[sub]


def _compensation_now(ctx: _Ctx, cur: _Cursor, kind: str, sub: str, start_t: int) -> int:
    s = cur.state
    if s.compensation_owed_minutes == 0 or sub == "split_part_1":
        return 0
    env = envelope(s, ctx.rules, start_t)
    if kind == "weekly_rest":
        return s.compensation_owed_minutes
    if env.compensation_due_in is not None and env.compensation_due_in <= env.weekly_rest_due_in:
        return s.compensation_owed_minutes
    return 0


def _rest_block(
    ctx: _Ctx,
    cur: _Cursor,
    kind: str,
    sub: str,
    at: str | None,
    start_t: int,
    minutes: int | None = None,
    interruptions: tuple[int, ...] = (),
    place: str = "",
) -> Block:
    base = minutes if minutes is not None else _rest_minutes(ctx, kind, sub)
    comp = _compensation_now(ctx, cur, kind, sub, start_t)
    total = base + comp + sum(interruptions)
    total = _extend_rest(ctx, cur, kind, start_t, total)
    text, article = _REST_TEXT[(kind, sub)]
    inside = _inside_window(ctx, cur, start_t, total)
    by_window = (
        kind == "daily_rest"
        and sub == "regular"
        and ctx.rules.daily_rest_reduced <= inside < ctx.rules.daily_rest_regular
    )
    if by_window:
        # Art 8(2): apply() counts it as reduced; the label says so too (minutes unchanged)
        sub, text, article = "reduced", "daily rest", "Art 8(2); Art 8(4)"
    reason = text + (place or (" at the roadside" if at is None else f" at {at}"))
    if sub == "reduced" and kind == "daily_rest":
        used = cur.state.reduced_daily_rests_used + 1
        allowed = ctx.rules.reduced_daily_rests_between_weekly
        reason += f" ({used} of {allowed} between weekly rests)"
    if by_window:
        reason += (
            f"; only {inside} min of this rest fall inside the 24 h period, "
            "so it counts as reduced (Art 8(2))"
        )
    if comp:
        reason += f", plus {comp} min compensation for an earlier reduced weekly rest (Art 8(6b))"
    if interruptions:
        reason += f", interrupted by boarding for {sum(interruptions)} min (Art 9(1))"
        article += "; Art 9(1)"
    return Block(
        kind,
        0,
        total,
        subtype=sub,
        at=at,
        reason=reason,
        article=article,
        interruptions=interruptions,
        compensation_minutes=comp,
    )


def _inside_window(ctx: _Ctx, cur: _Cursor, start_t: int, minutes: int) -> int:
    """Minutes of a rest starting at start_t that fall inside the current 24 h period."""
    window_end = cur.state.last_daily_rest_end + ctx.rules.daily_window
    return min(start_t + minutes, window_end) - start_t


def _break_block(ctx: _Ctx, minutes: int, at: str | None, reason: str) -> Block:
    return Block("break", 0, minutes, subtype="regular", at=at, reason=reason, article="Art 7")


def _extend_rest(ctx: _Ctx, cur: _Cursor, kind: str, start_t: int, total: int) -> int:
    return total  # Task 11: policy.extend_rest_to


def _apply_preferences(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> list[Choice]:
    return choices  # Task 11: rest_places, rest_start_window


def _policy_allows(ctx: _Ctx, env: Envelope, kind: str, sub: str) -> bool:
    p = ctx.policy
    if kind == "daily_rest":
        if sub == "reduced":
            return p.daily_rest_type != "regular"
        if sub == "regular":
            return not (
                p.daily_rest_type == "reduced_eager" and "reduced" in env.allowed_daily_rests
            )
        if sub == "split_part_1":
            return p.split_daily_rest != "never"
        return True
    if sub == "reduced":
        return p.weekly_rest_type != "regular"
    return not (p.weekly_rest_type == "reduced_eager" and "reduced" in env.allowed_weekly_rests)


def _needed_only(ctx: _Ctx, kind: str, sub: str) -> bool:
    p = ctx.policy
    if kind == "daily_rest":
        return sub == "reduced" and p.daily_rest_type == "reduced_when_needed"
    return sub == "reduced" and p.weekly_rest_type == "reduced_when_needed"


def _weekly_relevant(ctx: _Ctx, env: Envelope) -> bool:
    return (
        env.weekly_rest_due_in <= 2 * ctx.rules.daily_window
        or env.drive_this_week <= 0
        or env.drive_fortnight <= 0
    )


def _unblocks(ctx: _Ctx, cur: _Cursor, choice: Choice) -> bool:
    sim = cur.copy()
    _commit(ctx, sim, choice)
    return _drive_limit(ctx, sim, envelope(sim.state, ctx.rules, sim.t)) > 0


def _wait_next_week_choice(ctx: _Ctx, cur: _Cursor) -> Choice:
    nxt = ctx.clock.next_week_start(cur.t)
    minutes = max(nxt - cur.t, ctx.rules.weekly_rest_regular)
    block = _rest_block(
        ctx,
        cur,
        "weekly_rest",
        "regular",
        None,
        cur.t,
        minutes=minutes,
        place=" at the roadside until the next week starts (weekly driving limits reached)",
    )
    return Choice("wait_next_week", (block,), "road")


# ---------------------------------------------------------------- decision points


def _road_choices(ctx: _Ctx, cur: _Cursor) -> list[Choice]:
    r, p = ctx.rules, ctx.policy
    env = envelope(cur.state, r, cur.t)
    out: list[Choice] = []
    # a flag-only choice must still clear the break counter when the break limit binds
    # at the same moment
    bundled: tuple[Block, ...] = ()
    if env.drive_before_break <= 0:
        if cur.state.split_break_first_part_taken and p.split_breaks != "never":
            bundled = (
                _break_block(
                    ctx,
                    r.break_split_second,
                    None,
                    "30 min second part of a split break (15 min already taken)",
                ),
            )
        else:
            bundled = (
                _break_block(ctx, r.break_full, None, "45 min break after 4.5 h of driving"),
            )
    if (
        not cur.extend_today
        and p.extended_driving == "when_needed"
        and env.extension_available
        and env.drive_today_unextended < env.drive_today
        and env.drive_today > 0
        and (
            env.drive_today_unextended <= 0
            # the break binds and the 9 h cap would bind before the next break is due:
            # the extension is decided now, taken with this break
            or (bundled and env.drive_today_unextended < r.break_after_driving)
        )
    ):
        out.append(Choice("extend_day", bundled, "road", needed_only=True, sets_extend=True))
    if (
        not cur.reduced_today
        and p.daily_rest_type == "reduced_when_needed"
        and "reduced" in env.allowed_daily_rests
        and "split_part_2" not in env.allowed_daily_rests
        and env.daily_window_ends_in - r.daily_rest_regular
        <= 0
        < env.daily_window_ends_in - r.daily_rest_reduced
    ):
        out.append(
            Choice("accept_reduced_rest", bundled, "road", needed_only=True, sets_reduced=True)
        )
    if cur.state.split_break_first_part_taken and p.split_breaks != "never":
        out.append(
            Choice(
                "break_30",
                (
                    _break_block(
                        ctx,
                        r.break_split_second,
                        None,
                        "30 min second part of a split break (15 min already taken)",
                    ),
                ),
                "road",
            )
        )
    out.append(
        Choice(
            "break_45",
            (_break_block(ctx, r.break_full, None, "45 min break after 4.5 h of driving"),),
            "road",
        )
    )
    for sub in env.allowed_daily_rests:
        if _policy_allows(ctx, env, "daily_rest", sub):
            out.append(
                Choice(
                    f"daily_rest_{sub}",
                    (_rest_block(ctx, cur, "daily_rest", sub, None, cur.t),),
                    "road",
                    needed_only=_needed_only(ctx, "daily_rest", sub),
                )
            )
    if _weekly_relevant(ctx, env):
        for sub in env.allowed_weekly_rests:
            if _policy_allows(ctx, env, "weekly_rest", sub):
                out.append(
                    Choice(
                        f"weekly_rest_{sub}",
                        (_rest_block(ctx, cur, "weekly_rest", sub, None, cur.t),),
                        "road",
                        needed_only=_needed_only(ctx, "weekly_rest", sub),
                    )
                )
        if "reduced" not in env.allowed_weekly_rests:
            _warn(
                cur,
                "INTERNATIONAL_EXCEPTION_NOT_USED",
                "The previous weekly rest was reduced, so a regular one is required next. "
                "The international-transport exception (two consecutive reduced weekly rests "
                "outside the Member State of establishment) is not modelled.",
                "Art 8(6)",
            )
    out = [c for c in out if _unblocks(ctx, cur, c)]
    out = _apply_preferences(ctx, cur, out)
    if not out:
        fallback = _wait_next_week_choice(ctx, cur)
        if not _unblocks(ctx, cur, fallback):
            raise SchedulingError(
                "the driver cannot legally drive again within this plan; check the driver state"
            )
        out = [fallback]
    return out


def _next_drive_minutes(ctx: _Ctx, idx: int) -> int:
    nxt = ctx.route[idx + 1] if idx + 1 < len(ctx.route) else None
    return nxt.minutes if isinstance(nxt, Drive) else 0


def _service_blocks(ctx: _Ctx, stop: Stop) -> tuple[Block, ...]:
    if stop.service_minutes == 0:
        return ()
    if stop.driver_works and not stop.idle_counts:
        return (
            Block(
                "work",
                0,
                stop.service_minutes,
                at=stop.id,
                reason=f"service at {stop.id} (driver works)",
                article="Art 4(e)",
            ),
        )
    counts = stop.idle_counts  # driver idle, or counts_as_rest set explicitly
    if counts and stop.service_minutes >= ctx.rules.break_full:
        return (
            Block(
                "break",
                0,
                stop.service_minutes,
                subtype="regular",
                at=stop.id,
                counts_as_rest=True,
                reason=f"break during idle service at {stop.id} (driver does not work)",
                article="Art 7; Art 4(d)",
            ),
        )
    return (
        Block(
            "wait",
            0,
            stop.service_minutes,
            at=stop.id,
            counts_as_rest=counts,
            reason=f"idle service at {stop.id} (driver does not work)"
            + ("" if counts else ", not counted as rest"),
            article="Art 4(d)",
        ),
    )


def _extra_stop_break_choices(
    ctx: _Ctx,
    cur: _Cursor,
    stop: Stop,
    env: Envelope,
    idle: int,
    base: tuple[Block, ...],
    service: tuple[Block, ...],
) -> tuple[list[Choice], float]:
    return [], 0.0  # Task 11: break_timing and split_breaks=prefer


def _break_at_stop(
    ctx: _Ctx,
    cur: _Cursor,
    stop: Stop,
    minutes: int,
    name: str,
    wait: Block | None,
    service: tuple[Block, ...],
) -> Choice:
    p = ctx.policy
    reason = (
        f"{minutes} min break at {stop.id} (break due within "
        f"{p.break_at_stop_lookahead_minutes} min of driving after this stop)"
    )
    if wait is not None and wait.minutes > 0:
        brk = _break_block(
            ctx, max(minutes, wait.minutes), stop.id, reason + ", covering the wait for the window"
        )
        return Choice(name, (brk, *service), "stop")
    brk = _break_block(ctx, minutes, stop.id, reason)
    blocks = (
        (brk, *service)
        if p.break_at_working_stop == "before" or not stop.driver_works
        else (*service, brk)
    )
    return Choice(name, blocks, "stop")


def _stop_choices(ctx: _Ctx, cur: _Cursor, stop: Stop) -> list[Choice]:
    r, p = ctx.rules, ctx.policy
    env = envelope(cur.state, r, cur.t)
    wait_minutes = max(0, stop.earliest - cur.t) if stop.earliest is not None else 0
    wait_counts = p.wait_counts_as_rest and stop.counts_as_rest is not False
    wait = Block(
        "wait",
        0,
        wait_minutes,
        at=stop.id,
        counts_as_rest=wait_counts,
        reason=f"wait for the window at {stop.id}"
        + ("" if wait_counts else " (not counted as rest)"),
        article="Art 4(d)",
    )
    service = _service_blocks(ctx, stop)
    base: tuple[Block, ...] = (wait, *service) if wait_minutes else service
    out = [Choice("continue", base, "stop")]
    next_drive = _next_drive_minutes(ctx, cur.idx)
    if next_drive == 0:
        return _legal_or_rest_first(
            ctx, cur, out, stop.id, (wait,) if wait_minutes else (), service
        )
    idle = (wait_minutes if wait_counts else 0) + (stop.service_minutes if stop.idle_counts else 0)
    due_soon = env.drive_before_break - next_drive <= p.break_at_stop_lookahead_minutes
    if due_soon and idle < r.break_full:
        out.append(_break_at_stop(ctx, cur, stop, r.break_full, "break_45_here", wait, service))
        if p.split_breaks != "never":
            if cur.state.split_break_first_part_taken and idle < r.break_split_second:
                out.append(
                    _break_at_stop(
                        ctx, cur, stop, r.break_split_second, "break_30_here", wait, service
                    )
                )
            elif not cur.state.split_break_first_part_taken and idle < r.break_split_first:
                out.append(
                    _break_at_stop(
                        ctx, cur, stop, r.break_split_first, "break_15_here", wait, service
                    )
                )
    extra, continue_penalty = _extra_stop_break_choices(ctx, cur, stop, env, idle, base, service)
    out.extend(extra)
    if continue_penalty:
        out[0] = replace(out[0], penalty=continue_penalty)
    if stop.rest_allowed:
        rest_due_soon = (
            env.daily_window_ends_in - _shortest_daily_rest(ctx, cur, env) - next_drive
            <= p.rest_place_lookahead_minutes
        )
        if rest_due_soon or p.rest_places != "anywhere":
            for sub in env.allowed_daily_rests:
                if _policy_allows(ctx, env, "daily_rest", sub):
                    out.append(_rest_at_stop(ctx, cur, stop, "daily_rest", sub, wait, service))
        if env.weekly_rest_due_in - next_drive <= r.daily_window:
            for sub in env.allowed_weekly_rests:
                if _policy_allows(ctx, env, "weekly_rest", sub):
                    out.append(_rest_at_stop(ctx, cur, stop, "weekly_rest", sub, wait, service))
    out = _apply_preferences(ctx, cur, out)
    return _legal_or_rest_first(ctx, cur, out, stop.id, (wait,) if wait_minutes else (), service)


def _rest_at_stop(
    ctx: _Ctx,
    cur: _Cursor,
    stop: Stop,
    kind: str,
    sub: str,
    wait: Block,
    service: tuple[Block, ...],
) -> Choice:
    needed = _needed_only(ctx, kind, sub)
    if wait.minutes > 0:
        rest = _rest_block(
            ctx,
            cur,
            kind,
            sub,
            stop.id,
            cur.t,
            place=f" at {stop.id}, covering the wait for the window",
        )
        if rest.minutes < wait.minutes:
            rest = replace(rest, minutes=wait.minutes)
        return Choice(f"{kind}_{sub}_here", (rest, *service), "stop", needed_only=needed)
    start_t = cur.t + sum(b.minutes for b in service)
    rest = _rest_block(ctx, cur, kind, sub, stop.id, start_t)
    return Choice(f"{kind}_{sub}_here", (*service, rest), "stop", needed_only=needed)


def _ferry_choices(ctx: _Ctx, cur: _Cursor, ferry: Ferry) -> list[Choice]:
    r = ctx.rules
    env = envelope(cur.state, r, cur.t)
    boarding: tuple[Block, ...] = ()
    if ferry.boarding_minutes:
        boarding = (
            Block(
                "work",
                0,
                ferry.boarding_minutes,
                at=ferry.id,
                reason=f"boarding ferry {ferry.id}",
                article="Art 4(e)",
            ),
        )
    crossing = Block(
        "ferry",
        0,
        ferry.minutes,
        at=ferry.id,
        counts_as_rest=True,
        reason=f"ferry crossing {ferry.id}; time on board counts as a break",
        article="Art 7; Art 9(2)",
    )
    out = [Choice("continue", (*boarding, crossing), "ferry")]
    if not ferry.sleeper_cabin:
        return _legal_or_rest_first(ctx, cur, out, None, (), (*boarding, crossing))
    for kind, subs in (
        ("daily_rest", env.allowed_daily_rests),
        ("weekly_rest", env.allowed_weekly_rests),
    ):
        for sub in subs:
            if sub.startswith("split") or not _policy_allows(ctx, env, kind, sub):
                continue
            if (
                kind == "weekly_rest"
                and sub == "regular"
                and ferry.minutes < r.ferry_weekly_rest_min_crossing
            ):
                continue
            start_t = cur.t + ferry.boarding_minutes
            probe = _rest_block(
                ctx, cur, kind, sub, ferry.id, start_t, place=f" on ferry {ferry.id}"
            )
            if ferry.minutes >= probe.minutes:
                rest = replace(probe, minutes=ferry.minutes)
                blocks: tuple[Block, ...] = (*boarding, rest)
            elif ferry.boarding_minutes > r.ferry_interruption_total:
                continue  # Art 9(1) limits only a rest interrupted by boarding
            else:
                interruptions = (ferry.boarding_minutes,) if ferry.boarding_minutes else ()
                rest = _rest_block(
                    ctx,
                    cur,
                    kind,
                    sub,
                    ferry.id,
                    cur.t,
                    interruptions=interruptions,
                    place=f" starting at the port and continuing on ferry {ferry.id}",
                )
                blocks = (rest,)
            out.append(
                Choice(
                    f"{kind}_{sub}_ferry", blocks, "ferry", needed_only=_needed_only(ctx, kind, sub)
                )
            )
    # when crossing as a break leaves the driver unable to drive on, only rests on board remain
    if len(out) > 1:
        unblocking = [c for c in out if _unblocks(ctx, cur, c)]
        out = unblocking or out
    out = _apply_preferences(ctx, cur, out)
    return _legal_or_rest_first(ctx, cur, out, None, (), (*boarding, crossing))


# ---------------------------------------------------------------- legality at stops and ferries


def _legal(ctx: _Ctx, cur: _Cursor, choice: Choice, honour_policy: bool = True) -> bool:
    """The choice validates clean, and a daily and a weekly rest can still start in time after it.

    Time at a stop or on a crossing is not bounded by the drive limit, so a choice there can
    push the next rest past its deadline (Art 8(2), Art 8(6)) or into a reduced one (Art 8(4)).
    """
    sim = cur.copy()
    n = len(sim.blocks)
    _commit(ctx, sim, choice)
    if validate(sim.blocks[n:], cur.state, ctx.rules, ctx.clock, start=cur.t):
        return False
    if cur.idx + 1 >= len(ctx.route):
        return True  # the plan ends here; nothing after it is checked
    env = envelope(sim.state, ctx.rules, sim.t)
    min_rest = _shortest_daily_rest(ctx, sim, env, honour_policy)
    return env.daily_window_ends_in >= min_rest and env.weekly_rest_due_in >= 0


def _legal_or_rest_first(
    ctx: _Ctx,
    cur: _Cursor,
    choices: list[Choice],
    at: str | None,
    wait: tuple[Block, ...],
    after: tuple[Block, ...],
) -> list[Choice]:
    """Keep the legal choices; when none is legal, rest first (covering any wait), then go on.

    Legality outranks preferences: the rest is taken at the stop when it is the only legal
    option, whatever the stop or policy says about resting there. When even that fails under
    a regular-rest policy, a reduced rest after the stop is accepted rather than no plan.
    """
    for honour_policy in (True, False):
        legal = [c for c in choices if _legal(ctx, cur, c, honour_policy)]
        if legal:
            return legal
        fallback = _rest_first_choices(ctx, cur, choices[0].where, at, wait, after, honour_policy)
        if fallback:
            return fallback
    what = f"stop {at}" if at is not None else "the ferry"
    raise SchedulingError(
        f"no legal plan through {what}: its waiting, service or crossing time leaves no room "
        "for a daily or weekly rest in time"
    )


def _rest_first_choices(
    ctx: _Ctx,
    cur: _Cursor,
    where: str,
    at: str | None,
    wait: tuple[Block, ...],
    after: tuple[Block, ...],
    honour_policy: bool,
) -> list[Choice]:
    env = envelope(cur.state, ctx.rules, cur.t)
    wait_minutes = sum(b.minutes for b in wait)
    fallback: list[Choice] = []
    for kind, subs in (
        ("daily_rest", env.allowed_daily_rests),
        ("weekly_rest", env.allowed_weekly_rests),
    ):
        for sub in subs:
            if not _policy_allows(ctx, env, kind, sub):
                continue
            place = (
                f" at {at} before the service" if at is not None else " at the port before boarding"
            ) + " (no later start keeps the next rest legal)"
            rest = _rest_block(ctx, cur, kind, sub, at, cur.t, place=place)
            if rest.minutes < wait_minutes:
                rest = replace(rest, minutes=wait_minutes)
            choice = Choice(
                f"{kind}_{sub}_before",
                (rest, *after),
                where,
                needed_only=_needed_only(ctx, kind, sub),
            )
            if _legal(ctx, cur, choice, honour_policy):
                fallback.append(choice)
    return fallback
