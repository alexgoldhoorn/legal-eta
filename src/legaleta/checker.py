"""Replays a timeline through apply() and reports every infringement. The scheduler's oracle."""

from __future__ import annotations

from dataclasses import dataclass

from legaleta.clock import Clock
from legaleta.rules import RuleSet
from legaleta.state import DriverState, apply, envelope
from legaleta.timeline import REST_KINDS, Block, check_contiguous


@dataclass(frozen=True)
class Violation:
    article: str
    block_index: int
    message: str
    excess_minutes: int = 0


def _claimed_minimum(block: Block, rules: RuleSet) -> int | None:
    if block.kind == "daily_rest":
        return {
            "regular": rules.daily_rest_regular,
            "reduced": rules.daily_rest_reduced,
            "split_part_1": rules.daily_rest_split_first,
            "split_part_2": rules.daily_rest_split_second,
        }.get(block.subtype or "")
    if block.kind == "weekly_rest":
        return {
            "regular": rules.weekly_rest_regular,
            "reduced": rules.weekly_rest_reduced,
        }.get(block.subtype or "")
    return None


def _qualifying_daily_rest_minutes(block: Block, window_end: int) -> int:
    """Rest minutes of the block that fall inside the current 24 h window."""
    if block.kind not in REST_KINDS:
        return 0
    inside = min(block.end, window_end) - block.start
    return max(0, inside - sum(block.interruptions))


def _rest_article(block: Block) -> str:
    return "Art 4(h)" if block.kind == "weekly_rest" else "Art 4(g)"


def validate(
    blocks: list[Block],
    initial_state: DriverState,
    rules: RuleSet,
    clock: Clock,
    start: int = 0,
) -> list[Violation]:
    check_contiguous(blocks, start)
    out: list[Violation] = []
    state = initial_state
    window_end = state.last_daily_rest_end + rules.daily_window
    weekly_deadline = state.last_weekly_rest_end + rules.weekly_rest_deadline
    # deadlines that passed before the timeline starts are the past: restart them (GN 7)
    while window_end < start:
        window_end += rules.daily_window
    while weekly_deadline < start:
        weekly_deadline += rules.weekly_rest_deadline
    compensation_reported = False

    for i, b in enumerate(blocks):
        before = state
        base = b.rest_minutes - b.compensation_minutes

        # claims and allowances, checked against the state before the block
        minimum = _claimed_minimum(b, rules)
        if minimum is not None and base < minimum:
            out.append(
                Violation(
                    _rest_article(b),
                    i,
                    f"rest shorter than its subtype claims: {b.kind} {b.subtype} has {base} min "
                    f"(minimum {minimum})",
                    minimum - base,
                )
            )
        if (
            b.kind == "daily_rest"
            and b.subtype == "split_part_2"
            and not before.split_daily_rest_first_part_taken
        ):
            out.append(
                Violation("Art 4(g)", i, "split_part_2 without a first part of 3 h on record")
            )
        if b.kind == "weekly_rest" and b.subtype == "reduced" and before.last_weekly_rest_reduced:
            out.append(
                Violation(
                    "Art 8(6)",
                    i,
                    "two consecutive reduced weekly rests (international exception not modelled)",
                )
            )
        if (
            b.kind in REST_KINDS
            and b.interruptions
            and (
                len(b.interruptions) > rules.ferry_interruptions_max
                or sum(b.interruptions) > rules.ferry_interruption_total
            )
        ):
            out.append(
                Violation(
                    "Art 9(1)",
                    i,
                    f"rest interrupted {len(b.interruptions)} times for "
                    f"{sum(b.interruptions)} min (max {rules.ferry_interruptions_max} times, "
                    f"{rules.ferry_interruption_total} min)",
                )
            )
        if (
            b.kind == "weekly_rest"
            and b.subtype == "regular"
            and b.in_vehicle
            and not rules.regular_weekly_rest_in_vehicle
        ):
            out.append(Violation("Art 8(8)", i, "regular weekly rest taken in the vehicle"))
        if b.compensation_minutes > 0 and before.compensation_owed_minutes > 0:
            if b.compensation_minutes < before.compensation_owed_minutes:
                out.append(
                    Violation(
                        "Art 8(6b)",
                        i,
                        f"compensation of {b.compensation_minutes} min is not en bloc "
                        f"({before.compensation_owed_minutes} min owed)",
                    )
                )
            elif base < rules.compensation_attach_min_rest:
                out.append(
                    Violation(
                        "Art 8(7)",
                        i,
                        f"compensation attached to a rest of {base} min "
                        f"(minimum {rules.compensation_attach_min_rest})",
                    )
                )

        state = apply(state, b, rules, clock)
        e = envelope(state, rules, b.end)

        # Art 8(4): counted by apply(), so a rest that Art 8(2) makes reduced counts too
        if (
            b.kind == "daily_rest"
            and (
                b.subtype == "reduced"
                or state.reduced_daily_rests_used > before.reduced_daily_rests_used
            )
            and before.reduced_daily_rests_used >= rules.reduced_daily_rests_between_weekly
        ):
            out.append(
                Violation(
                    "Art 8(4)",
                    i,
                    f"reduced daily rest number {before.reduced_daily_rests_used + 1} between "
                    f"two weekly rests (max {rules.reduced_daily_rests_between_weekly})",
                )
            )

        if b.kind == "drive":
            if e.drive_before_break < 0:
                out.append(
                    Violation(
                        "Art 7",
                        i,
                        "driving beyond 4.5 h without a qualifying break",
                        -e.drive_before_break,
                    )
                )
            if e.drive_today < 0:
                out.append(Violation("Art 6(1)", i, "daily driving beyond 10 h", -e.drive_today))
            elif (
                e.drive_today_unextended < 0
                and state.extended_days_used_this_week > rules.extended_days_per_week
            ):
                out.append(
                    Violation(
                        "Art 6(1)",
                        i,
                        f"daily driving beyond 9 h on extended day "
                        f"{state.extended_days_used_this_week} this week "
                        f"(max {rules.extended_days_per_week})",
                        -e.drive_today_unextended,
                    )
                )
            if e.drive_this_week < 0:
                out.append(
                    Violation("Art 6(2)", i, "weekly driving beyond 56 h", -e.drive_this_week)
                )
            if e.drive_fortnight < 0:
                out.append(
                    Violation(
                        "Art 6(3)",
                        i,
                        "driving in two consecutive weeks beyond 90 h",
                        -e.drive_fortnight,
                    )
                )

        # Art 8(2): did a qualifying daily rest end inside the window?
        need = (
            rules.daily_rest_split_second
            if before.split_daily_rest_first_part_taken
            else rules.daily_rest_reduced
        )
        inside = _qualifying_daily_rest_minutes(b, window_end)
        is_daily_rest = state.last_daily_rest_end == b.end and b.kind in REST_KINDS
        if is_daily_rest and inside >= need:
            window_end = b.end + rules.daily_window
        elif b.end > window_end:
            if is_daily_rest:
                out.append(
                    Violation(
                        "Art 8(2)",
                        i,
                        f"only {inside} min of rest inside the 24 h window (minimum {need})",
                        need - inside,
                    )
                )
                window_end = b.end + rules.daily_window  # the next window starts at its end (GN 7)
            else:
                while b.end > window_end:  # one report per lapsed period (GN 7, repeatedly)
                    out.append(
                        Violation(
                            "Art 8(2)",
                            i,
                            "no daily rest completed within 24 h of the previous rest",
                            b.end - window_end,
                        )
                    )
                    window_end += rules.daily_window

        # Art 8(6): the weekly rest must start by the deadline
        if state.last_weekly_rest_end == b.end and b.kind == "weekly_rest":
            # the deadlines were advanced past every earlier block, so it started in time
            weekly_deadline = b.end + rules.weekly_rest_deadline
        else:
            # a weekly_rest block under 24 h that starts in time is not a weekly rest: reported
            while b.end > weekly_deadline:  # one report per lapsed period
                out.append(
                    Violation(
                        "Art 8(6)",
                        i,
                        "no weekly rest started within six 24 h periods of the previous one",
                        b.end - weekly_deadline,
                    )
                )
                weekly_deadline += rules.weekly_rest_deadline

        # Art 8(6b): the deadline passed with debt still owed (reported once per debt)
        if state.compensation_owed_minutes == 0:
            compensation_reported = False
        if (
            not compensation_reported
            and state.compensation_owed_minutes > 0
            and state.compensation_deadline is not None
            and b.end > state.compensation_deadline
        ):
            out.append(
                Violation(
                    "Art 8(6b)",
                    i,
                    f"{state.compensation_owed_minutes} min of weekly-rest compensation "
                    "not taken before its deadline",
                )
            )
            compensation_reported = True
    return out
