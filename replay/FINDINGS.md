# Replay findings

Planning aid, not legal advice. What the replay found in legaleta, and how to reproduce it.
Replay version of 2026-10-16; legaleta at `feat/replay` branch point (`9ccbcac`).

Source of the rules: the UK revised text of Regulation (EC) 561/2006 on legislation.gov.uk
(Arts [6](https://www.legislation.gov.uk/eur/2006/561/article/6),
[7](https://www.legislation.gov.uk/eur/2006/561/article/7),
[8](https://www.legislation.gov.uk/eur/2006/561/article/8),
[9](https://www.legislation.gov.uk/eur/2006/561/article/9)). It reproduces the EU text with UK
edits (for example Art 8(6) names the UK in its international-transport derogation); check the
current EU consolidated text before relying on a finding.

## Open

### 1. A rest before any driving is counted as the first part of a 15 + 30 split break (probable bug)

Art 7: "After a driving period of four and a half hours a driver shall take an uninterrupted
break of not less than 45 minutes. This break may be replaced by a break of at least 15 minutes
followed by a break of at least 30 minutes each distributed over the period in such a way as to
comply with the provisions of the first paragraph."

legaleta takes a 15 min idle service at a stop **before the driver has driven at all** as the first
part, then plans a **30 min** break after 270 min of driving. Nothing in the log is a break taken
after driving began, so the replay reads 270 + 270 min of driving with only a 30 min break between.

- Minimal repro: `replay/corpus/minimal_split_break_first_part.json` (fresh driver, stop `s0` with a
  15 min non-working service, stop `s1` with 45 min of work, then 560 min of driving).
  Policies `fastest` and `driver_friendly` plan `drive 270, break 30, drive 270`; `strict` plans a
  45 min break and passes.
- Replay: `python -m replay run replay/corpus/minimal_split_break_first_part.json` gives
  `BREAK_MISSING Art 7 (+270 min)`.
- `legaleta validate` called on the very same blocks says the plan is **legal**, so the checker and the
  scheduler share the blind spot (the differential check reports `VALIDATE_DISAGREES`).
- Fuzz cases: `replay/corpus/seed12_*`, `seed24_*` (same cause).
- Related, same root: the end state after a long rest followed by a 15 min idle block reports
  `split_break_first_part_taken: true` (`replay/corpus/seed40_*`). The state machine treats
  contiguous rest blocks one at a time instead of as one rest.
- Confidence: high on the reading that the first part must come after driving began; not checked
  against a Commission guidance note (none was reachable in the session that wrote this).

## Closed during the build (replay bugs, found by the same runs)

Kept so the same mistakes are not made twice. Each now has a test.

- A ferry crossing without a cabin merged into the rest after it and counted as a 9 h daily rest.
  It is a break only (contract: "a `wait` or `ferry` block counts as a break only when it is true").
- The 10 h extension was counted between weekly rests; the regulation says "twice during the week"
  (a calendar week). A day belongs to the week in which its 10th hour starts.
- A rest of 24 h or more is a daily or a reduced weekly rest at the driver's choice; the plan's label decides.
- A start-state rest ending exactly at the start merged with the plan's first rest.
- `weekly_rest_due_in` was converted with 11 520 instead of 8 640.
- The 24 h window rule ran against a period that had already lapsed before the start (Guidance Note 7).
- Compensation attaches to a rest of at least 9 h (Art 8(7)), and a 9 h rest that carries it is a
  reduced daily rest (Art 8(4)).
- Art 9(1): a regular weekly rest can be interrupted on a ferry or train journey of 8 h or more with a
  cabin; legaleta already models this.

## Not findings, but read before trusting a PASS

- A PASS can contain `INTERPRETATION` findings: legal only under a documented assumption
  (`WAIT_ASSUMED_REST`, the Guidance Note 7 restart, the Art 8(2) window rule, en-bloc compensation).
  Run `python -m replay run ... --json` to see them.
- The replay lays the driver's start state out as a prior log. A state it cannot lay out is reported as
  a skipped row, not as a pass.
