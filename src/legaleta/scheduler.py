"""Forward simulation: drive to the legal limit, choose a block at each decision point, repeat."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from legaleta.checker import validate
from legaleta.clock import Clock
from legaleta.policy import PREFERENCE_WEIGHTS, REST_PLACE_TAG, Policy
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
    extension_minutes: int = 0  # rest added by policy.extend_rest_to; not an arrival cost

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
            self.extension_minutes,
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
    # at a stop: the service starts when block service_index starts, plus service_delay
    # minutes (a break that covers the wait and idle service holds it inside)
    service_index: int = 0
    service_delay: int = 0


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


# a break at a stop that covers idle time and lengthens the stop by at most this much is
# "nearly free"
_NEARLY_FREE_MINUTES = 15


def _total(choice: Choice) -> int:
    return sum(b.minutes for b in choice.blocks)


def _break_minutes(choice: Choice) -> int:
    return sum(b.minutes for b in choice.blocks if b.kind == "break")


def _rest_still_needed(ctx: _Ctx, cur: _Cursor) -> bool:
    """The driving left on the route exceeds what today still allows (Art 6(1), Art 8(2))."""
    # called at a stop, so no drive is under way (drive_left == 0)
    left = sum(it.minutes for it in ctx.route[cur.idx :] if isinstance(it, Drive))
    caps = _caps(ctx, cur, envelope(cur.state, ctx.rules, cur.t))
    today = min(caps[1][0], caps[4][0])
    return left > today


def plain_chooser(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> Choice:
    """Latest-legal: continue when possible, else the smallest block that unblocks.

    A break at a stop that covers idle time there and costs at most _NEARLY_FREE_MINUTES more
    than continuing is taken ahead of continue. A break that covers nothing is never nearly
    free: it costs its whole length.
    """
    primary = [c for c in choices if not c.needed_only] or choices
    cont = next((c for c in primary if c.name == "continue"), None)
    if (
        cont is not None
        and ctx.policy.rest_start_window is not None
        and _rest_still_needed(ctx, cur)
    ):
        # a rest at this stop that breaks no preference (inside the window, at an acceptable
        # place) is taken ahead of continuing, when the day cannot end without one anyway
        in_window = [
            c
            for c in primary
            if c.name.startswith("daily_rest_")
            and c.name.endswith("_here")
            and "split_part_1" not in c.name
            and c.penalty == 0
        ]
        if in_window:
            return min(in_window, key=lambda c: _RANK.get(_base_name(c.name), 99))
    if cont is not None:
        nearly_free = [
            c
            for c in primary
            if c.name.startswith("break_")
            and c.name.endswith("_here")
            and _total(c) - _total(cont) <= _NEARLY_FREE_MINUTES
            and _total(c) - _total(cont) < _break_minutes(c)
        ]
        if nearly_free:
            return min(
                nearly_free,
                key=lambda c: (_total(c), _RANK.get(_base_name(c.name), 99)),
            )
    return min(primary, key=lambda c: _RANK.get(_base_name(c.name), 99))


@dataclass(frozen=True)
class _Scored:
    choice: Choice
    score: float
    lateness: int
    slack: int
    order: int
    feasible: bool = True  # False: the horizon simulation dead-ended (SchedulingError)


def _score(ctx: _Ctx, cur: _Cursor, choice: Choice, order: int) -> _Scored:
    p = ctx.policy
    sim = cur.copy()
    _apply_choice(ctx, sim, choice)
    e = envelope(sim.state, ctx.rules, sim.t)
    # the nearest binding limit; a sum would let a daily rest (which resets two of the three)
    # win every tie with a break on the road
    slack = min(e.drive_before_break, e.drive_today, e.daily_window_ends_in)
    try:
        sim = _run(ctx, sim, plain_chooser, stop_budget=cur.stops_done + p.lookahead_stops)
    except SchedulingError:
        # the plain rule found no way through the horizon after this choice: the candidate is
        # unusable, but the plan is not lost (another candidate, or the real run, may get through)
        return _Scored(choice, math.inf, 0, slack, order, feasible=False)
    added_lateness = sim.lateness - cur.lateness
    score = (
        p.lateness_weight * added_lateness
        # time the driver asked to sleep (extend_rest_to) is not an arrival cost
        + p.arrival_weight * ((sim.t - cur.t) - (sim.extension_minutes - cur.extension_minutes))
        + (sim.penalty - cur.penalty)
    )
    return _Scored(choice, score, added_lateness, slack, order)


def _best(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> _Scored | None:
    """The best feasible choice, or None when every horizon simulation dead-ended."""
    scored = [s for s in (_score(ctx, cur, c, i) for i, c in enumerate(choices)) if s.feasible]
    if not scored:
        return None
    return min(scored, key=lambda s: (s.score, -s.slack, s.order))


def lookahead_chooser(ctx: _Ctx, cur: _Cursor, choices: list[Choice]) -> Choice:
    """Score each choice by simulating the next policy.lookahead_stops stops (spec 5.2).

    Stage 1 scores the choices that are not needed_only; stage 2 (all choices) runs only
    when stage 1's best is late in the horizon (beyond the lateness at the stop being
    decided), and wins only with a strictly lower score and strictly less lateness.

    A choice whose horizon simulation dead-ends is infeasible and never ranked. Stage 2 also
    runs when every stage 1 choice is infeasible. When no choice is feasible, the plain rule
    picks over all of them: it never simulates ahead, and a later real decision point may
    still get through. Only a dead end in the real run fails the plan.
    """
    if len(choices) == 1:
        return choices[0]
    stage1 = [c for c in choices if not c.needed_only] or choices
    best = _best(ctx, cur, stage1)
    if (best is None or best.lateness > _unavoidable_lateness(ctx, cur)) and len(stage1) < len(
        choices
    ):
        best2 = _best(ctx, cur, choices)
        # a needed_only choice wins only by being less late, never on arrival time alone
        if best2 is not None and (
            best is None or (best2.score < best.score and best2.lateness < best.lateness)
        ):
            best = best2
    if best is None:
        return plain_chooser(ctx, cur, choices)
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
    warnings = list(STANDING_WARNINGS) + _start_warnings(state, rules) + list(cur.warnings.values())
    return PlanResult(cur.blocks, cur.stops, warnings, cur.state, cur.t, rules, policy)


def _start_warnings(state: DriverState, rules: RuleSet) -> list[Warning]:
    """Infringements the driver state brings with it; the plan is judged from the start on."""
    need = (
        rules.daily_rest_split_second
        if state.split_daily_rest_first_part_taken
        else rules.daily_rest_reduced
    )
    if envelope(state, rules, 0).daily_window_ends_in >= need:
        return []
    return [
        Warning(
            "DAILY_REST_OVERDUE_AT_START",
            "The 24 h period after the driver's last daily rest ends, or has ended, too soon "
            "after the start for a daily rest to fit in it. The plan opens with a rest; the "
            "shortfall in that period comes from the driver state, not from the plan, and the "
            "next period is counted from its end (Guidance Note 7).",
            "Art 8(2)",
        )
    ]


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
    if block.kind in REST_KINDS and not _on_ferry(ctx, block):
        stretched = _extend_rest(ctx, cur, block.kind, block.start, block.minutes)
        deadline = cur.state.compensation_deadline
        if (
            block.compensation_minutes
            and deadline is not None
            and block.start + stretched > deadline >= block.end
        ):
            stretched = block.minutes  # never stretch a compensating rest past its deadline
        if block.kind == "daily_rest":
            weekly_by = cur.t + envelope(cur.state, ctx.rules, cur.t).weekly_rest_due_in
            if block.start + stretched > weekly_by >= block.end:
                stretched = block.minutes  # nor a daily rest past the weekly start-by (Art 8(6))
        if stretched > block.minutes:
            extra = stretched - block.minutes
            cur.extension_minutes += extra
            block = replace(
                block,
                minutes=stretched,
                reason=block.reason + f", extended by {extra} min (policy extend_rest_to)",
            )
    block = _relabel_daily_rest(ctx, cur, block)
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


def _on_ferry(ctx: _Ctx, block: Block) -> bool:
    """A rest taken on board lasts as long as the crossing; it is never extended."""
    return block.at is not None and any(
        isinstance(it, Ferry) and it.id == block.at for it in ctx.route
    )


_COUNT_TEXT = re.compile(r" \(\d+ of \d+ between weekly rests\)")
_WINDOW_TEXT = re.compile(
    r"; only \d+ min of this rest fall inside the 24 h period, [^;]*?\(Art 8\(2\)\)"
)


def _relabel_daily_rest(ctx: _Ctx, cur: _Cursor, block: Block) -> Block:
    """Label a stretched or extended daily rest as apply() will count it (Art 8(2), Art 8(4))."""
    r = ctx.rules
    if block.kind != "daily_rest" or block.subtype not in ("regular", "reduced"):
        return block
    base = block.rest_minutes - block.compensation_minutes
    if base < r.daily_rest_reduced:
        return block
    inside = _inside_window(ctx, cur, block.start, block.minutes)
    classified = min(base, inside) if inside >= r.daily_rest_reduced else base
    sub = "reduced" if classified < r.daily_rest_regular else "regular"
    if sub == block.subtype:
        return block
    reason = _WINDOW_TEXT.sub("", _COUNT_TEXT.sub("", block.reason))
    for head in ("regular daily rest of 11 h", "reduced daily rest of 9 h"):
        reason = reason.replace(head, "daily rest", 1)
    if sub == "regular":
        reason += f"; {classified} min, so it counts as a regular daily rest (Art 8(2))"
        article = block.article.replace("Art 8(2); Art 8(4)", "Art 8(2); Art 4(g)")
    else:
        used = cur.state.reduced_daily_rests_used + 1
        reason += (
            f" ({used} of {r.reduced_daily_rests_between_weekly} between weekly rests); "
            f"only {classified} min count, so it is a reduced daily rest (Art 8(2))"
        )
        article = block.article.replace("Art 8(2); Art 4(g)", "Art 8(2); Art 8(4)")
    return replace(block, subtype=sub, reason=reason, article=article)


def _apply_choice(ctx: _Ctx, cur: _Cursor, choice: Choice) -> None:
    if choice.where == "stop":
        stop = ctx.route[cur.idx]
        assert isinstance(stop, Stop)
        arrival = cur.t
        starts = _commit(ctx, cur, choice)
        # lateness is judged when the service starts, after any wait or rest before it
        service_start = starts[choice.service_index] + choice.service_delay
        lateness = max(0, service_start - stop.latest) if stop.latest is not None else 0
        rested = any(b.kind in REST_KINDS and b.at == stop.id for b in choice.blocks)
        if rested and not stop.rest_allowed:
            _warn(
                cur,
                "REST_AT_STOP_NOT_ALLOWED",
                f"A rest was planned at stop {stop.id}, which the route marks rest_allowed: "
                "false: no other placement kept the plan legal, and legality outranks the "
                "stop's setting.",
                "",
            )
        cur.stops.append(StopResult(stop.id, arrival, cur.t, lateness, lateness == 0, rested))
        cur.lateness += lateness
        cur.stops_done += 1
        cur.idx += 1
    elif choice.where == "ferry":
        _commit(ctx, cur, choice)
        cur.idx += 1
    else:
        _commit(ctx, cur, choice)


def _commit(ctx: _Ctx, cur: _Cursor, choice: Choice) -> list[int]:
    """Emit the choice's blocks; return the time each one started, then the end time."""
    starts: list[int] = []
    for b in choice.blocks:
        starts.append(cur.t)
        _emit(ctx, cur, b)
    starts.append(cur.t)
    if choice.sets_extend:
        cur.extend_today = True
    if choice.sets_reduced:
        cur.reduced_today = True
    cur.penalty += choice.penalty
    return starts


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
        (_compensation_cap(ctx, cur, env), "compensation must be attached (Art 8(6b))"),
    ]


_UNBOUNDED = 10**9  # a cap that never binds


def _rides_on_daily_rest(ctx: _Ctx, state: DriverState, env: Envelope, now: int) -> bool:
    """The debt goes on a daily rest: the next weekly rest, started at its deadline, could end
    after the compensation deadline (Art 8(6b)).

    When the weekly or two-week driving limit may run out before that weekly rest is due, the
    weekly rest may have to last until the next week starts, and that longer rest is assumed.
    """
    due = env.compensation_due_in
    if due is None:
        return False
    weekly_due = env.weekly_rest_due_in
    weekly_len = ctx.rules.weekly_rest_regular
    if min(env.drive_this_week, env.drive_fortnight) < weekly_due:
        monday = ctx.clock.next_week_start(now + max(0, weekly_due))
        weekly_len = max(weekly_len, monday - (now + weekly_due))
    return due < weekly_due + weekly_len + state.compensation_owed_minutes


def _compensation_end_by(ctx: _Ctx, state: DriverState, env: Envelope, now: int) -> int | None:
    """Minutes until the rest carrying the debt must end, or None when nothing binds.

    A daily rest carrying it must also end before the weekly rest has to start. Once the
    deadline has passed (only possible when the driver state starts too close to it) there is
    nothing left to protect, and None is returned.
    """
    due = env.compensation_due_in
    if due is None or due <= 0:
        return None
    if _rides_on_daily_rest(ctx, state, env, now):
        return min(due, env.weekly_rest_due_in)
    return due


def _compensation_cap(ctx: _Ctx, cur: _Cursor, env: Envelope) -> int:
    """Art 8(6b): drive no longer than lets a rest carrying the debt end in time."""
    end_by = _compensation_end_by(ctx, cur.state, env, cur.t)
    if end_by is None:
        return _UNBOUNDED
    return end_by - (_shortest_daily_rest(ctx, cur, env) + cur.state.compensation_owed_minutes)


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
    return s.compensation_owed_minutes if _rides_on_daily_rest(ctx, s, env, start_t) else 0


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
    compensation: int | None = None,
) -> Block:
    base = minutes if minutes is not None else _rest_minutes(ctx, kind, sub)
    comp = _compensation_now(ctx, cur, kind, sub, start_t) if compensation is None else compensation
    total = base + comp + sum(interruptions)  # extend_rest_to is applied in _emit
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
    """policy.extend_rest_to: lengthen a rest to end at the next given local time (bounded)."""
    p = ctx.policy
    if p.extend_rest_to is None:
        return total
    end = start_t + total
    target = ctx.clock.next_local_minute(end, p.extend_rest_to)
    extra = target - end
    return total + extra if 0 < extra <= p.extend_rest_max_minutes else total


def _rest_blocks_with_start(choice: Choice, cur: _Cursor) -> list[tuple[Block, int]]:
    out: list[tuple[Block, int]] = []
    t = cur.t
    for b in choice.blocks:
        if b.kind in REST_KINDS:
            out.append((b, t))
        t += b.minutes
    return out


def _apply_preferences(
    ctx: _Ctx, cur: _Cursor, choices: list[Choice], restorable: bool | None = None
) -> list[Choice]:
    """policy.rest_places and policy.rest_start_window: drop (hard) or penalise (soft) rests.

    On the road and at a ferry (and for the rest-first fallback anywhere), a knob that would
    remove every choice, or every daily rest and leave only weekly rests, is dropped instead
    and warned about.
    """
    p = ctx.policy
    # at a stop "continue" always survives; on the road and at a ferry nothing does
    if restorable is None:
        restorable = bool(choices) and choices[0].where in ("road", "ferry")

    def is_daily_rest(c: Choice) -> bool:
        return any(b.kind == "daily_rest" for b in c.blocks)

    def pass_(knob: str, cs: list[Choice]) -> list[Choice]:
        kept: list[Choice] = []
        # every choice with the cost of what it breaks: used when the knob must be dropped,
        # so that the lookahead still prefers honouring a hard knob where it can
        restored: list[Choice] = []
        for c in cs:
            penalty, drop = 0.0, False
            for b, t0 in _rest_blocks_with_start(c, cur):
                if (
                    knob == "rest_places"
                    and not _tagged_place(ctx, b.at)
                    and b.kind in REST_KINDS
                    and c.name != "wait_next_week"
                    and p.rest_places != "anywhere"
                ):
                    drop = drop or p.rest_places == "tagged_only"
                    penalty += PREFERENCE_WEIGHTS["rest_places"]
                if (
                    knob == "rest_start_window"
                    and p.rest_start_window is not None
                    and b.kind == "daily_rest"
                    and not p.rest_start_window.contains(ctx.clock.minute_of_day(t0))
                ):
                    drop = drop or p.rest_start_window.hard
                    penalty += p.rest_start_window.weight * 60.0
            priced = replace(c, penalty=c.penalty + penalty) if penalty else c
            restored.append(priced)
            if not drop:
                kept.append(priced)
        # every choice dropped, or only weekly rests left where daily rests were offered
        only_weekly_left = (
            any(is_daily_rest(c) for c in cs)
            and not any(is_daily_rest(c) for c in kept)
            and all(any(b.kind in REST_KINDS for b in c.blocks) for c in kept)
        )
        if restorable and (not kept or only_weekly_left):
            _warn(
                cur,
                f"PREFERENCE_UNMET:{knob}",
                f"policy.{knob} could not be honoured without breaking the rules; "
                "latest-legal placement used",
                "",
            )
            return restored
        return kept

    return pass_("rest_start_window", pass_("rest_places", choices))


def _tagged_place(ctx: _Ctx, at: str | None) -> bool:
    """policy.rest_places: a stop tagged REST_PLACE_TAG, or a ferry (a rest on board is in
    its cabin). The roadside and untagged stops are not tagged places."""
    for it in ctx.route:
        if isinstance(it, Stop) and it.id == at:
            return REST_PLACE_TAG in it.tags
        if isinstance(it, Ferry) and it.id == at:
            return True
    return False


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


def _validates(ctx: _Ctx, cur: _Cursor, choice: Choice) -> bool:
    sim = cur.copy()
    n = len(sim.blocks)
    _commit(ctx, sim, choice)
    return not validate(sim.blocks[n:], cur.state, ctx.rules, ctx.clock, start=cur.t)


def _debt_first(
    ctx: _Ctx, cur: _Cursor, weekly_minutes: int, at: str | None = None, place: str = ""
) -> tuple[Block, ...]:
    """A daily rest carrying the compensation debt, to take right before a weekly rest of
    weekly_minutes that would itself end after the compensation deadline (Art 8(6b)); or ()."""
    r, s = ctx.rules, cur.state
    owed, deadline = s.compensation_owed_minutes, s.compensation_deadline
    if not owed or deadline is None or cur.t + weekly_minutes + owed <= deadline:
        return ()
    env = envelope(s, r, cur.t)
    shortest = _shortest_daily_rest(ctx, cur, env)
    if shortest + owed > env.weekly_rest_due_in:
        # legality outranks the policy: the shortest rest the rules allow, so that the weekly
        # rest can still start in time
        shortest = _shortest_daily_rest(ctx, cur, env, honour_policy=False)
    sub = (
        "split_part_2"
        if "split_part_2" in env.allowed_daily_rests
        else ("reduced" if shortest == r.daily_rest_reduced else "regular")
    )
    return (_rest_block(ctx, cur, "daily_rest", sub, at, cur.t, place=place, compensation=owed),)


def _road_weekly_choice(
    ctx: _Ctx, cur: _Cursor, name: str, sub: str, weekly_minutes: int, place: str = ""
) -> Choice:
    first = _debt_first(ctx, cur, weekly_minutes)
    t = cur.t + sum(b.minutes for b in first)
    block = _rest_block(
        ctx,
        cur,
        "weekly_rest",
        sub,
        None,
        t,
        minutes=weekly_minutes,
        place=place,
        compensation=0 if first else None,
    )
    return Choice(name, (*first, block), "road", needed_only=_needed_only(ctx, "weekly_rest", sub))


def _wait_next_week_choice(ctx: _Ctx, cur: _Cursor) -> Choice:
    nxt = ctx.clock.next_week_start(cur.t)
    minutes = max(nxt - cur.t, ctx.rules.weekly_rest_regular)
    place = " at the roadside until the next week starts (weekly driving limits reached)"
    choice = _road_weekly_choice(ctx, cur, "wait_next_week", "regular", minutes, place)
    if len(choice.blocks) == 1:
        return choice
    # the daily rest paying the debt comes first: the wait still ends when the week starts
    first = choice.blocks[0]
    weekly_start = cur.t + first.minutes
    rest = replace(choice.blocks[1], minutes=max(nxt - weekly_start, ctx.rules.weekly_rest_regular))
    return replace(choice, blocks=(first, rest), needed_only=False)


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

    def weekly_choices() -> list[Choice]:
        return [
            _road_weekly_choice(
                ctx, cur, f"weekly_rest_{sub}", sub, _rest_minutes(ctx, "weekly_rest", sub)
            )
            for sub in env.allowed_weekly_rests
            if _policy_allows(ctx, env, "weekly_rest", sub)
        ]

    weekly_offered = _weekly_relevant(ctx, env)
    if weekly_offered:
        out.extend(weekly_choices())
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
    # a regular rest starting late in its 24 h period counts as reduced (Art 8(2)), illegal
    # once no reduced rest is left (Art 8(4)): drop such a choice while a valid one remains
    valid = [c for c in out if _validates(ctx, cur, c)]
    if not weekly_offered and not any(b.kind == "daily_rest" for c in valid for b in c.blocks):
        # no daily rest is legal here: a weekly rest is a daily rest too and resets the
        # reduced-rest count (Art 8(4)), so it is the way out
        valid += [c for c in weekly_choices() if _unblocks(ctx, cur, c) and _validates(ctx, cur, c)]
    out = valid or out
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
) -> tuple[list[Choice], dict[str, float]]:
    """policy.break_timing and split_breaks="prefer": extra break choices, and the penalties
    for the choices that do not honour them (by choice name)."""
    r, p = ctx.rules, ctx.policy
    extra: list[Choice] = []
    penalties: dict[str, float] = {}
    wait = base[0] if base and base[0].kind == "wait" else None
    convenient = (
        cur.state.driving_since_break >= p.earliest_convenient_after_minutes and idle < r.break_full
    )
    if convenient and (
        p.break_timing == "earliest_convenient"
        or (p.break_timing == "balanced" and idle >= r.break_full - _NEARLY_FREE_MINUTES)
    ):
        extra.append(_break_at_stop(ctx, cur, stop, r.break_full, "break_45_here", wait, service))
        # a 15 min first part does not take the break either, unless split breaks are preferred
        names = ("continue",) if p.split_breaks == "prefer" else ("continue", "break_15_here")
        for name in names:
            penalties[name] = penalties.get(name, 0.0) + PREFERENCE_WEIGHTS["break_timing"]
    if (
        p.split_breaks == "prefer"
        and not cur.state.split_break_first_part_taken
        and idle < r.break_split_first
    ):
        extra.append(
            _break_at_stop(ctx, cur, stop, r.break_split_first, "break_15_here", wait, service)
        )
        penalties["continue"] = penalties.get("continue", 0.0) + PREFERENCE_WEIGHTS["split_breaks"]
    return extra, penalties


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
    waited = wait.minutes if wait is not None else 0
    idle_service = tuple(b for b in service if b.kind == "wait" and b.counts_as_rest)
    other = tuple(b for b in service if not (b.kind == "wait" and b.counts_as_rest))
    covered = waited + sum(b.minutes for b in idle_service)
    if covered > 0:
        what = [
            *(["the wait for the window"] if waited else []),
            *([f"the idle time at {stop.id}"] if idle_service else []),
        ]
        reason += ", covering " + " and ".join(what)
    brk = _break_block(ctx, max(minutes, covered), stop.id, reason)
    if waited:
        # the wait comes before any service; idle service is held inside the break
        if other:
            return Choice(name, (brk, *other), "stop", service_index=1)
        return Choice(name, (brk,), "stop", service_delay=waited)
    if p.break_at_working_stop == "before" or not stop.driver_works:
        return Choice(name, (brk, *other), "stop", service_index=1 if other else 0)
    return Choice(name, (*other, brk), "stop")


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
    out = [Choice("continue", base, "stop", service_index=1 if wait_minutes else 0)]
    next_drive = _next_drive_minutes(ctx, cur.idx)
    if next_drive == 0:
        return _legal_or_rest_first(
            ctx, cur, out, "stop", stop.id, (wait,) if wait_minutes else (), service
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
    extra, penalties = _extra_stop_break_choices(ctx, cur, stop, env, idle, base, service)
    out.extend(extra)
    # a break offered both by the due-soon rule and by a preference is scored once
    seen: set[str] = set()
    deduped: list[Choice] = []
    for c in out:
        if c.name not in seen:
            seen.add(c.name)
            deduped.append(c)
    out = deduped
    out = [
        replace(c, penalty=c.penalty + penalties[c.name]) if c.name in penalties else c for c in out
    ]
    if (
        p.extended_driving == "when_needed"
        and env.extension_available
        and not cur.extend_today
        and env.drive_today_unextended < next_drive
    ):
        # the 9 h cap binds before the next stop: let stage 2 (and its horizon) extend from here
        out.extend(
            [
                replace(c, name=f"{c.name}+extend_day", needed_only=True, sets_extend=True)
                for c in out
            ]
        )
    if stop.rest_allowed:
        rest_due_soon = (
            env.daily_window_ends_in - _shortest_daily_rest(ctx, cur, env) - next_drive
            <= p.rest_place_lookahead_minutes
        )
        if rest_due_soon or p.rest_places != "anywhere" or p.rest_start_window is not None:
            for sub in env.allowed_daily_rests:
                if _policy_allows(ctx, env, "daily_rest", sub):
                    out.append(_rest_at_stop(ctx, cur, stop, "daily_rest", sub, wait, service))
        if env.weekly_rest_due_in - next_drive <= r.daily_window:
            for sub in env.allowed_weekly_rests:
                if _policy_allows(ctx, env, "weekly_rest", sub):
                    out.append(_rest_at_stop(ctx, cur, stop, "weekly_rest", sub, wait, service))
    out = _apply_preferences(ctx, cur, out)
    return _legal_or_rest_first(
        ctx, cur, out, "stop", stop.id, (wait,) if wait_minutes else (), service
    )


def _cover_wait(cur: _Cursor, rest: Block, wait: Block) -> tuple[Block, ...]:
    """A rest at a stop stretched to cover the wait for its window.

    A rest carrying compensation is stretched no further than the compensation deadline
    (Art 8(6b)); the rest of the wait follows it as a wait block.
    """
    if rest.minutes >= wait.minutes:
        return (rest,)
    deadline = cur.state.compensation_deadline
    if rest.compensation_minutes and deadline is not None and cur.t + wait.minutes > deadline:
        stretched = max(rest.minutes, deadline - cur.t)
        return (replace(rest, minutes=stretched), replace(wait, minutes=wait.minutes - stretched))
    return (replace(rest, minutes=wait.minutes),)


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
        covered = _cover_wait(cur, rest, wait)
        return Choice(
            f"{kind}_{sub}_here",
            (*covered, *service),
            "stop",
            needed_only=needed,
            service_index=len(covered),
        )
    start_t = cur.t + sum(b.minutes for b in service)
    rest = _rest_block(ctx, cur, kind, sub, stop.id, start_t)
    return Choice(f"{kind}_{sub}_here", (*service, rest), "stop", needed_only=needed)


def _ferry_choices(ctx: _Ctx, cur: _Cursor, ferry: Ferry) -> list[Choice]:
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
        return _legal_or_rest_first(ctx, cur, out, "ferry", None, (), (*boarding, crossing))
    out.extend(_rests_on_board(ctx, cur, ferry, boarding))
    # when crossing as a break leaves the driver unable to drive on, only rests on board remain
    if len(out) > 1:
        unblocking = [c for c in out if _unblocks(ctx, cur, c)]
        out = unblocking or out
    out = _apply_preferences(ctx, cur, out)
    return _legal_or_rest_first(
        ctx,
        cur,
        out,
        "ferry",
        None,
        (),
        (*boarding, crossing),
        then=lambda sim: _rests_on_board(ctx, sim, ferry, boarding),
    )


def _rests_on_board(
    ctx: _Ctx, cur: _Cursor, ferry: Ferry, boarding: tuple[Block, ...]
) -> list[Choice]:
    """Daily and weekly rests in the sleeper cabin, as the state at cur allows (Art 9)."""
    r = ctx.rules
    env = envelope(cur.state, r, cur.t)
    out: list[Choice] = []
    for kind, subs in (
        ("daily_rest", env.allowed_daily_rests),
        ("weekly_rest", env.allowed_weekly_rests),
    ):
        for sub in subs:
            # a 3 h first part is not offered on board; a second part is (below)
            if sub == "split_part_1" or not _policy_allows(ctx, env, kind, sub):
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
            elif kind == "daily_rest" and sub != "regular":
                # Art 9(1) admits an interrupted rest only for a regular daily rest or a
                # weekly rest: never a reduced daily rest or a part of a split one
                continue
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
    return out


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
    end_by = _compensation_end_by(ctx, sim.state, env, sim.t)
    # Art 8(6b): a rest carrying the debt can still end in time (once the deadline has
    # passed, end_by is None: validate() above has reported it, or it passed before the plan)
    compensation_ok = end_by is None or end_by >= min_rest + sim.state.compensation_owed_minutes
    return env.daily_window_ends_in >= min_rest and env.weekly_rest_due_in >= 0 and compensation_ok


def _legal_or_rest_first(
    ctx: _Ctx,
    cur: _Cursor,
    choices: list[Choice],
    where: str,
    at: str | None,
    wait: tuple[Block, ...],
    after: tuple[Block, ...],
    then: Callable[[_Cursor], list[Choice]] | None = None,
) -> list[Choice]:
    """Keep the legal choices; when none is legal, rest first (covering any wait), then go on.

    Legality outranks preferences: the rest is taken at the stop when it is the only legal
    option, whatever the stop or policy says about resting there. When even that fails under
    a regular-rest policy, a reduced rest after the stop is accepted rather than no plan.
    `then` offers alternatives to `after` from the state after that first rest (at a ferry
    with a sleeper cabin: a second rest on board instead of crossing as a break).
    """
    for honour_policy in (True, False):
        legal = [c for c in choices if _legal(ctx, cur, c, honour_policy)]
        if legal:
            return legal
        fallback = _rest_first_choices(ctx, cur, where, at, wait, after, honour_policy, then)
        if fallback:
            # preferences still rank the fallback rests; one that no rest can honour is
            # dropped with a warning, never at the cost of a legal plan
            return _apply_preferences(ctx, cur, fallback, restorable=True)
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
    then: Callable[[_Cursor], list[Choice]] | None = None,
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
            # a weekly rest that would end after the compensation deadline: the debt goes on a
            # daily rest taken first
            first = (
                _debt_first(ctx, cur, _rest_minutes(ctx, kind, sub), at, place)
                if kind == "weekly_rest"
                else ()
            )
            t0 = cur.t + sum(b.minutes for b in first)
            rest = _rest_block(
                ctx, cur, kind, sub, at, t0, place=place, compensation=0 if first else None
            )
            left = wait_minutes - (t0 - cur.t)
            covered = (
                *first,
                *(_cover_wait(cur, rest, replace(wait[0], minutes=left)) if left > 0 else (rest,)),
            )
            choice = Choice(
                f"{kind}_{sub}_before",
                (*covered, *after),
                where,
                needed_only=_needed_only(ctx, kind, sub),
                service_index=len(covered),
            )
            if _legal(ctx, cur, choice, honour_policy):
                fallback.append(choice)
            elif then is not None:
                sim = cur.copy()
                _commit(ctx, sim, Choice(choice.name, covered, where))
                for nxt in then(sim):
                    both = Choice(
                        f"{choice.name}+{nxt.name}",
                        (*covered, *nxt.blocks),
                        where,
                        needed_only=choice.needed_only or nxt.needed_only,
                    )
                    if _legal(ctx, cur, both, honour_policy):
                        fallback.append(both)
    return fallback
