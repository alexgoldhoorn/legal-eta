import dataclasses

import pytest

from legaleta.rules import PRESETS, RuleSet, rules_from


def test_eu561_defaults_match_the_regulation() -> None:
    r = PRESETS["eu561"]
    assert (r.break_after_driving, r.break_full) == (270, 45)
    assert (r.break_split_first, r.break_split_second) == (15, 30)
    assert (r.daily_driving, r.daily_driving_extended, r.extended_days_per_week) == (540, 600, 2)
    assert (r.weekly_driving, r.fortnight_driving) == (3360, 5400)
    assert (r.daily_rest_regular, r.daily_rest_reduced) == (660, 540)
    assert (r.daily_rest_split_first, r.daily_rest_split_second) == (180, 540)
    assert (r.reduced_daily_rests_between_weekly, r.daily_window) == (3, 1440)
    assert (r.weekly_rest_regular, r.weekly_rest_reduced, r.weekly_rest_deadline) == (
        2700,
        1440,
        8640,
    )
    assert (r.compensation_deadline_weeks, r.compensation_attach_min_rest) == (3, 540)
    assert r.regular_weekly_rest_in_vehicle is False
    assert (r.ferry_interruptions_max, r.ferry_interruption_total) == (2, 60)
    assert (r.ferry_weekly_rest_min_crossing, r.ferry_weekly_rest_needs_cabin) == (480, True)
    assert r.min_break_portion == 15


def test_overrides_apply_and_are_echoed() -> None:
    r = rules_from("eu561", {"break_after_driving": 240})
    assert r.break_after_driving == 240
    assert r.name == "eu561"
    assert r.overrides == {"break_after_driving": 240}


def test_unknown_preset_and_field_rejected() -> None:
    with pytest.raises(ValueError, match="preset"):
        rules_from("aetr")
    with pytest.raises(ValueError, match="unknown rule"):
        rules_from("eu561", {"break_after_drivng": 1})
    with pytest.raises(ValueError, match="integer"):
        rules_from("eu561", {"break_full": "45"})


def test_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        PRESETS["eu561"].break_full = 1  # type: ignore[misc]


def test_bool_override_and_type_checks() -> None:
    r = rules_from("eu561", {"regular_weekly_rest_in_vehicle": True})
    assert r.regular_weekly_rest_in_vehicle is True
    with pytest.raises(ValueError, match="boolean"):
        rules_from("eu561", {"regular_weekly_rest_in_vehicle": 1})
    with pytest.raises(ValueError, match="integer"):
        rules_from("eu561", {"break_full": True})
    with pytest.raises(ValueError, match="unknown rule"):
        rules_from("eu561", {"name": "x"})


def test_default_ruleset_is_the_preset() -> None:
    assert RuleSet() == PRESETS["eu561"]


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("break_full", -5, "'break_full' must be > 0"),
        ("break_after_driving", 0, "'break_after_driving' must be > 0"),
        ("daily_window", 0, "'daily_window' must be > 0"),
        (
            "reduced_daily_rests_between_weekly",
            -1,
            "'reduced_daily_rests_between_weekly' must be >= 0",
        ),
        ("ferry_interruptions_max", -1, "'ferry_interruptions_max' must be >= 0"),
    ],
)
def test_override_ranges(key: str, value: int, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        rules_from("eu561", {key: value})


def test_count_overrides_may_be_zero() -> None:
    r = rules_from("eu561", {"reduced_daily_rests_between_weekly": 0, "extended_days_per_week": 0})
    assert r.reduced_daily_rests_between_weekly == 0 and r.extended_days_per_week == 0
