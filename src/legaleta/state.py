"""Driver state (tachograph counters), the envelope of what is allowed, and transitions."""

from __future__ import annotations

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
    raise NotImplementedError  # Task 5


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
