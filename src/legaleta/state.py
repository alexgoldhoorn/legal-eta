"""Driver state (tachograph counters), the envelope of what is allowed, and transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from legaleta.clock import Clock
from legaleta.rules import RuleSet
from legaleta.timeline import Block


@dataclass(frozen=True)
class DriverState:
    driving_since_break: int = 0
    split_break_first_part_taken: bool = False
    driving_today: int = 0
    last_daily_rest_end: int = 0
    split_daily_rest_first_part_taken: bool = False
    reduced_daily_rests_used: int = 0
    driving_this_week: int = 0
    driving_last_week: int = 0
    extended_days_used_this_week: int = 0
    last_weekly_rest_end: int = 0
    last_weekly_rest_reduced: bool = False
    compensation_owed_minutes: int = 0
    compensation_deadline: int | None = None


@dataclass(frozen=True)
class Envelope:
    drive_before_break: int
    drive_today: int  # with the 10 h extension when it is available
    drive_today_unextended: int  # against the 9 h cap (negative once extended)
    drive_this_week: int
    drive_fortnight: int
    daily_window_ends_in: int  # Art 8(2): the rest must be complete by then
    weekly_rest_due_in: int  # Art 8(6): the weekly rest must start by then
    extension_available: bool
    allowed_daily_rests: tuple[str, ...]
    allowed_weekly_rests: tuple[str, ...]
    compensation_due_in: int | None

    def drive_limit(self, allow_extension: bool) -> int:
        today = (
            self.drive_today
            if (allow_extension and self.extension_available)
            else self.drive_today_unextended
        )
        return max(
            0, min(self.drive_before_break, today, self.drive_this_week, self.drive_fortnight)
        )


def envelope(state: DriverState, rules: RuleSet, now: int) -> Envelope:
    already_extended = state.driving_today > rules.daily_driving
    ext = already_extended or state.extended_days_used_this_week < rules.extended_days_per_week
    daily: list[str] = ["regular"]
    if state.reduced_daily_rests_used < rules.reduced_daily_rests_between_weekly:
        daily.append("reduced")
    daily.append("split_part_2" if state.split_daily_rest_first_part_taken else "split_part_1")
    weekly: list[str] = ["regular"]
    if not state.last_weekly_rest_reduced:
        weekly.append("reduced")
    return Envelope(
        drive_before_break=rules.break_after_driving - state.driving_since_break,
        drive_today=(rules.daily_driving_extended if ext else rules.daily_driving)
        - state.driving_today,
        drive_today_unextended=rules.daily_driving - state.driving_today,
        drive_this_week=rules.weekly_driving - state.driving_this_week,
        drive_fortnight=rules.fortnight_driving - state.driving_last_week - state.driving_this_week,
        daily_window_ends_in=state.last_daily_rest_end + rules.daily_window - now,
        weekly_rest_due_in=state.last_weekly_rest_end + rules.weekly_rest_deadline - now,
        extension_available=ext,
        allowed_daily_rests=tuple(daily),
        allowed_weekly_rests=tuple(weekly),
        compensation_due_in=(
            None
            if state.compensation_owed_minutes == 0 or state.compensation_deadline is None
            else state.compensation_deadline - now
        ),
    )


def _roll_weeks(state: DriverState, t_from: int, t_to: int, clock: Clock) -> DriverState:
    """Apply every Monday 00:00 rollover in (t_from, t_to]."""
    b = clock.next_week_start(t_from)
    while b <= t_to:
        state = replace(
            state,
            driving_last_week=state.driving_this_week,
            driving_this_week=0,
            extended_days_used_this_week=0,
        )
        b = clock.next_week_start(b)
    return state


def _apply_drive(state: DriverState, block: Block, rules: RuleSet, clock: Clock) -> DriverState:
    t = block.start
    end = block.end
    while t < end:
        boundary = clock.next_week_start(t)
        seg_end = min(end, boundary)
        minutes = seg_end - t
        was_extended = state.driving_today > rules.daily_driving
        new_today = state.driving_today + minutes
        extra = 1 if (not was_extended and new_today > rules.daily_driving) else 0
        state = replace(
            state,
            driving_since_break=state.driving_since_break + minutes,
            driving_today=new_today,
            driving_this_week=state.driving_this_week + minutes,
            extended_days_used_this_week=state.extended_days_used_this_week + extra,
        )
        if seg_end == boundary:
            state = _roll_weeks(state, t, boundary, clock)
        t = seg_end
    return state


def _apply_break(state: DriverState, minutes: int, rules: RuleSet) -> DriverState:
    """Each block is judged alone; adjacent break-type blocks are never aggregated.

    Deliberate and conservative: it never calls an illegal plan legal.
    """
    if minutes >= rules.break_full or (
        state.split_break_first_part_taken and minutes >= rules.break_split_second
    ):
        return replace(state, driving_since_break=0, split_break_first_part_taken=False)
    if minutes >= rules.break_split_first and not state.split_break_first_part_taken:
        return replace(state, split_break_first_part_taken=True)
    return state


def _apply_rest(state: DriverState, block: Block, rules: RuleSet, clock: Clock) -> DriverState:
    rest = block.rest_minutes
    base = rest - block.compensation_minutes
    if block.kind == "daily_rest" and block.subtype == "split_part_1":
        if rest >= rules.daily_rest_split_first:
            return replace(
                state,
                driving_since_break=0,
                split_break_first_part_taken=False,
                split_daily_rest_first_part_taken=True,
            )
        return _apply_break(state, rest, rules)
    completes_split = (
        block.kind == "daily_rest"
        and block.subtype == "split_part_2"
        and state.split_daily_rest_first_part_taken
    )
    if base < rules.daily_rest_reduced and not (
        completes_split and base >= rules.daily_rest_split_second
    ):
        return _apply_break(state, rest, rules)
    # a qualifying daily rest (every weekly rest is one too)
    reduced_daily = base < rules.daily_rest_regular and not completes_split
    state = replace(
        state,
        driving_since_break=0,
        split_break_first_part_taken=False,
        driving_today=0,
        last_daily_rest_end=block.end,
        split_daily_rest_first_part_taken=False,
        reduced_daily_rests_used=state.reduced_daily_rests_used + (1 if reduced_daily else 0),
    )
    state = _pay_compensation(state, block, rules, base)
    if block.kind == "weekly_rest" and base >= rules.weekly_rest_reduced:
        reduced_weekly = base < rules.weekly_rest_regular
        owed, deadline = state.compensation_owed_minutes, state.compensation_deadline
        if reduced_weekly:
            new_deadline = clock.week_start_after(block.end, rules.compensation_deadline_weeks + 1)
            owed += rules.weekly_rest_regular - base
            deadline = new_deadline if deadline is None else min(deadline, new_deadline)
        state = replace(
            state,
            reduced_daily_rests_used=0,
            last_weekly_rest_end=block.end,
            last_weekly_rest_reduced=reduced_weekly,
            compensation_owed_minutes=owed,
            compensation_deadline=deadline,
        )
    return state


def _pay_compensation(state: DriverState, block: Block, rules: RuleSet, base: int) -> DriverState:
    """Pay the compensation debt in full, or not at all.

    Debts are aggregated under the earliest deadline. This is conservative (a plan judged
    legal is legal) and sufficient for v0.1: the scheduler pays the debt at the next weekly
    rest and does not use consecutive reduced weekly rests.
    """
    comp = block.compensation_minutes
    if comp <= 0 or state.compensation_owed_minutes == 0:
        return state
    if base >= rules.compensation_attach_min_rest and comp >= state.compensation_owed_minutes:
        return replace(state, compensation_owed_minutes=0, compensation_deadline=None)
    return state


def apply(state: DriverState, block: Block, rules: RuleSet, clock: Clock) -> DriverState:
    """The single place counters change: what the regulation counts, not what a block claims."""
    if block.kind == "drive":
        return _apply_drive(state, block, rules, clock)
    state = _roll_weeks(state, block.start, block.end, clock)
    if (
        block.kind == "break"
        or block.kind == "ferry"
        or (block.kind == "wait" and block.counts_as_rest)
    ):
        return _apply_break(state, block.minutes, rules)
    if block.kind in ("daily_rest", "weekly_rest"):
        return _apply_rest(state, block, rules, clock)
    return state  # work, wait that does not count


def replay(state: DriverState, blocks: list[Block], rules: RuleSet, clock: Clock) -> DriverState:
    for b in blocks:
        state = apply(state, b, rules, clock)
    return state


REMAINING_KEYS = (
    "remaining_drive_before_break",
    "remaining_drive_today",
    "remaining_drive_this_week",
    "remaining_drive_fortnight",
    "daily_rest_due_in",
    "weekly_rest_due_in",
)


def to_remaining(state: DriverState, rules: RuleSet, now: int) -> dict[str, int | bool]:
    e = envelope(state, rules, now)
    out: dict[str, int | bool] = {
        "remaining_drive_before_break": e.drive_before_break,
        "remaining_drive_today": e.drive_today_unextended,
        "remaining_drive_this_week": e.drive_this_week,
        "remaining_drive_fortnight": e.drive_fortnight,
        "daily_rest_due_in": e.daily_window_ends_in,
        "weekly_rest_due_in": e.weekly_rest_due_in,
        "split_break_first_part_taken": state.split_break_first_part_taken,
        "split_daily_rest_first_part_taken": state.split_daily_rest_first_part_taken,
        "reduced_daily_rests_used": state.reduced_daily_rests_used,
        "extended_days_used_this_week": state.extended_days_used_this_week,
        "last_weekly_rest_reduced": state.last_weekly_rest_reduced,
        "compensation_owed_minutes": state.compensation_owed_minutes,
    }
    if state.compensation_deadline is not None:
        out["compensation_deadline_in"] = state.compensation_deadline - now
    return out


def from_remaining(
    remaining: Mapping[str, object], rules: RuleSet, now: int = 0
) -> tuple[DriverState, list[str]]:
    """Convert the tachograph-style 'remaining' form. Returns the state and the assumptions made."""
    missing = [k for k in REMAINING_KEYS if k not in remaining]
    if missing:
        raise ValueError(f"remaining form is missing {', '.join(missing)}")

    def _int(key: str, default: int = 0) -> int:
        v = remaining.get(key, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{key} must be an integer number of minutes")
        return v

    def _bool(key: str) -> bool:
        v = remaining.get(key, False)
        if not isinstance(v, bool):
            raise ValueError(f"{key} must be a boolean")
        return v

    notes: list[str] = []
    rem_today = _int("remaining_drive_today")
    # always read against the 9 h cap, so negative on an extended day
    driving_today = rules.daily_driving - rem_today
    if rem_today < 0:
        notes.append(
            "remaining_drive_today is negative: the day is already extended beyond the 9 h cap"
        )
    else:
        notes.append(
            "remaining_drive_today read against the 9 h cap; "
            "if the day is already extended to 10 h, pass the raw form"
        )
    driving_this_week = rules.weekly_driving - _int("remaining_drive_this_week")
    driving_last_week = max(
        0, rules.fortnight_driving - _int("remaining_drive_fortnight") - driving_this_week
    )
    owed = _int("compensation_owed_minutes")
    deadline = (
        now + _int("compensation_deadline_in") if "compensation_deadline_in" in remaining else None
    )
    if owed > 0 and deadline is None:
        notes.append("compensation owed without a deadline: treated as due at the end of the plan")
    state = DriverState(
        driving_since_break=rules.break_after_driving - _int("remaining_drive_before_break"),
        split_break_first_part_taken=_bool("split_break_first_part_taken"),
        driving_today=driving_today,
        last_daily_rest_end=now - (rules.daily_window - _int("daily_rest_due_in")),
        split_daily_rest_first_part_taken=_bool("split_daily_rest_first_part_taken"),
        reduced_daily_rests_used=_int("reduced_daily_rests_used"),
        driving_this_week=driving_this_week,
        driving_last_week=driving_last_week,
        extended_days_used_this_week=_int("extended_days_used_this_week"),
        last_weekly_rest_end=now - (rules.weekly_rest_deadline - _int("weekly_rest_due_in")),
        last_weekly_rest_reduced=_bool("last_weekly_rest_reduced"),
        compensation_owed_minutes=owed,
        compensation_deadline=deadline,
    )
    return state, notes


@dataclass(frozen=True)
class ProjectResult:
    state: DriverState
    envelope: Envelope
    first_limit: tuple[int, str] | None  # (minutes from now, limit name) or None


ACTIVITIES = ("driving", "resting", "other_work")


def project(
    state: DriverState,
    minutes: int,
    activity: str,
    rules: RuleSet,
    clock: Clock,
    now: int = 0,
) -> ProjectResult:
    """Simulate a constant activity for `minutes` and report the first limit hit."""
    if activity not in ACTIVITIES:
        raise ValueError(f"activity must be one of {ACTIVITIES}")
    if minutes < 0:
        raise ValueError("minutes must be >= 0")
    e0 = envelope(state, rules, now)
    first: tuple[int, str] | None = None
    if activity == "driving":
        limits = [
            (e0.drive_before_break, "break"),
            (e0.drive_today, "daily_driving"),
            (e0.drive_this_week, "weekly_driving"),
            (e0.drive_fortnight, "fortnight_driving"),
            (e0.daily_window_ends_in - rules.daily_rest_reduced, "daily_rest_window"),
            (e0.weekly_rest_due_in, "weekly_rest_deadline"),
        ]
        block = Block("drive", now, minutes)
    elif activity == "other_work":
        limits = [
            (e0.daily_window_ends_in, "daily_rest_window"),
            (e0.weekly_rest_due_in, "weekly_rest_deadline"),
        ]
        block = Block("work", now, minutes)
    else:
        limits = []
        kind = "weekly_rest" if minutes >= rules.weekly_rest_reduced else "daily_rest"
        regular = rules.weekly_rest_regular if kind == "weekly_rest" else rules.daily_rest_regular
        block = Block(kind, now, minutes, subtype="regular" if minutes >= regular else "reduced")
    hits = sorted((max(0, m), name) for m, name in limits if m < minutes)
    if hits:
        first = hits[0]
    new_state = apply(state, block, rules, clock)
    return ProjectResult(new_state, envelope(new_state, rules, now + minutes), first)
