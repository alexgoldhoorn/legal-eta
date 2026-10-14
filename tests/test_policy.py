import pytest

from legaleta.policy import PRESETS, Policy, RestWindow, policy_from


def test_defaults_are_fastest() -> None:
    p = Policy()
    assert (
        p.break_timing == "latest"
        and p.split_breaks == "allow"
        and p.break_at_working_stop == "after"
    )
    assert p.daily_rest_type == "reduced_eager" and p.split_daily_rest == "allow"
    assert p.extended_driving == "eager" and p.rest_places == "anywhere"
    assert p.weekly_rest_type == "reduced_when_needed" and p.wait_counts_as_rest is True
    assert (p.lateness_weight, p.arrival_weight, p.lookahead_stops) == (1.0, 0.1, 3)
    assert (p.break_at_stop_lookahead_minutes, p.rest_place_lookahead_minutes) == (60, 90)
    assert (
        p.rest_start_window is None
        and p.extend_rest_to is None
        and p.extend_rest_max_minutes == 360
    )


def test_presets() -> None:
    assert PRESETS["fastest"] == Policy(name="fastest")
    d = PRESETS["driver_friendly"]
    assert (
        d.break_timing == "balanced"
        and d.rest_places == "prefer_tagged"
        and d.daily_rest_type == "regular"
    )
    assert d.rest_start_window == RestWindow(19 * 60, 22 * 60, hard=False, weight=1.0)
    s = PRESETS["strict"]
    assert (
        s.split_breaks,
        s.daily_rest_type,
        s.extended_driving,
        s.weekly_rest_type,
        s.rest_places,
    ) == (
        "never",
        "regular",
        "never",
        "regular",
        "tagged_only",
    )


def test_overrides_parse_windows_and_times() -> None:
    p = policy_from(
        "fastest",
        {
            "rest_start_window": {"earliest": "18:00", "latest": "23:00", "hard": True},
            "extend_rest_to": "06:00",
        },
    )
    assert p.rest_start_window == RestWindow(1080, 1380, hard=True, weight=1.0)
    assert p.extend_rest_to == 360
    assert p.overrides["extend_rest_to"] == "06:00"


def test_invalid_values_rejected() -> None:
    with pytest.raises(ValueError, match="break_timing"):
        policy_from("fastest", {"break_timing": "whenever"})
    with pytest.raises(ValueError, match="unknown policy"):
        policy_from("fastest", {"nope": 1})
    with pytest.raises(ValueError, match="preset"):
        policy_from("lazy")
    with pytest.raises(ValueError, match="lookahead_stops"):
        policy_from("fastest", {"lookahead_stops": 0})
