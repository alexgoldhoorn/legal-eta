from replay.regs import Regs, regs_from_effective


def test_defaults_match_regulation() -> None:
    r = Regs()
    assert (r.break_after_driving, r.break_full) == (270, 45), "Art 7: 4.5 h, 45 min"
    assert (r.break_split_first, r.break_split_second) == (15, 30), "Art 7: 15 then 30"
    assert (r.daily_driving, r.daily_driving_extended) == (540, 600), "Art 6(1): 9 h, 10 h"
    assert r.extended_days_per_week == 2, "Art 6(1): twice a week"
    assert (r.weekly_driving, r.fortnight_driving) == (3360, 5400), "Art 6(2),(3): 56 h, 90 h"
    assert (r.daily_rest_regular, r.daily_rest_reduced) == (660, 540), "Art 4(g), 8(2)"
    assert (r.daily_rest_split_first, r.daily_rest_split_second) == (180, 540), "Art 8(2)"
    assert r.reduced_daily_rests_between_weekly == 3, "Art 8(4)"
    assert r.daily_window == 1440, "Art 4(i)"
    assert (r.weekly_rest_regular, r.weekly_rest_reduced) == (2700, 1440), "Art 4(h), 8(6)"
    assert r.weekly_rest_deadline == 6 * 1440, "Art 8(6)"
    assert r.compensation_deadline_weeks == 3, "Art 8(6b)"
    assert (r.ferry_interruptions_max, r.ferry_interruption_total) == (2, 60), "Art 9(1)"


def test_effective_override_reported() -> None:
    regs, findings = regs_from_effective({"weekly_rest_reduced": 1500, "break_full": 45})
    assert regs.weekly_rest_reduced == 1500
    assert [f.cls for f in findings] == ["NON_STANDARD_RULES"]
    assert "weekly_rest_reduced" in findings[0].message


def test_defaults_only_gives_no_finding() -> None:
    regs, findings = regs_from_effective({"break_full": 45, "unknown_key": 3})
    assert regs == Regs() and findings == []
