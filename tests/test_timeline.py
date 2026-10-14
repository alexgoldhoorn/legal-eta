import pytest

from legaleta.timeline import Block, Drive, Ferry, Stop, check_contiguous, merge_drives, summarize


def test_block_end_and_rest_minutes() -> None:
    b = Block(
        "daily_rest", start=100, minutes=680, subtype="regular", at="ferry1", interruptions=(20,)
    )
    assert b.end == 780
    assert b.rest_minutes == 660


def test_block_rejects_bad_kind_and_negative_minutes() -> None:
    with pytest.raises(ValueError, match="kind"):
        Block("sleep", 0, 10)
    with pytest.raises(ValueError, match="minutes"):
        Block("drive", 0, -1)


def test_merge_drives_merges_consecutive_and_drops_zero() -> None:
    route = merge_drives([Drive(10), Drive(0), Drive(20), Stop("A"), Drive(0), Stop("B")])
    assert route == [Drive(30), Stop("A"), Stop("B")]


def test_merge_drives_rejects_empty_and_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="empty"):
        merge_drives([])
    with pytest.raises(ValueError, match="duplicate"):
        merge_drives([Stop("A"), Drive(5), Stop("A")])


def test_stop_idle_counts() -> None:
    assert Stop("A", service_minutes=30, driver_works=False).idle_counts is True
    assert Stop("A", service_minutes=30, driver_works=True).idle_counts is False
    assert Stop("A", driver_works=True, counts_as_rest=True).idle_counts is True


def test_ferry_validation() -> None:
    with pytest.raises(ValueError):
        Ferry("f", minutes=0)


def test_summarize_and_contiguity() -> None:
    blocks = [Block("drive", 0, 100), Block("break", 100, 45), Block("drive", 145, 30)]
    s = summarize(blocks)
    assert s == {
        "driving": 130,
        "break": 45,
        "daily_rest": 0,
        "weekly_rest": 0,
        "work": 0,
        "wait": 0,
        "ferry": 0,
        "total": 175,
    }
    check_contiguous(blocks)
    with pytest.raises(ValueError, match="gap"):
        check_contiguous([Block("drive", 0, 100), Block("drive", 101, 1)])
    with pytest.raises(ValueError, match="start"):
        check_contiguous([Block("drive", 5, 100)])
