# Replay

Planning aid, not legal advice. Design: `docs/superpowers/specs/2026-10-16-replay-design.md`.

`replay/` is an independent check of legaleta's plans. It is a test tool: not in the wheel, not part of
the public API.

## What it does

1. **Executes** a plan's blocks as a one-minute activity log (drive, work, availability, rest). The start
   state is laid out as a prior log; counts that cannot be laid out (earlier days of the week, reduced
   rests used, compensation) are counters.
2. **Judges** the finished log by looking back over it, the way an enforcement officer reads a tachograph:
   Art 6 (daily, weekly, fortnight), Art 7 (breaks, 15 + 30 splits), Art 8 (daily and weekly rests,
   reduced rests, compensation, the six-period deadline), Art 9 (ferry interruptions). A rest is recognised
   by what it is in the log, not by its label, except for rests of 24 h or more, where the plan's label
   decides between a daily and a weekly rest.
3. **Checks the plan's claims**: arrival, every stop's arrival, departure and lateness, the summary
   minutes, `driver_state_at_end` (raw and remaining) and the warnings that must (or must not) be present.

The core imports nothing from legaleta. Only `replay/compare.py` and `replay/__main__.py` import
`legaleta.api`, to obtain plans and to run `legaleta validate` on the same blocks as a differential
check. A test enforces this.

## Reading a result

| Class | Meaning |
|---|---|
| `VIOLATION` | The executed log breaks an article: a bug in legaleta or in the replay. |
| `CLAIM_MISMATCH` | The plan says something the replay does not reproduce. |
| `MISSING_WARNING`, `SPURIOUS_WARNING` | A documented warning that must (or must not) be there. |
| `INTERPRETATION` | Goes away under another documented reading (`WAIT_ASSUMED_REST`, the Guidance Note 7 restart, the Art 8(2) window rule). Does not fail the run. |
| `NON_STANDARD_RULES` | The plan used overridden rule constants. Does not fail the run. |

`PASS` means no finding other than the last two. A PASS can carry `INTERPRETATION` notes (`STRICTER_READING`): the plan would fail under the other reading of a documented assumption. A row is `SKIP` when legaleta rejects the request or the
start state cannot be laid out as a prior log.

## Commands

    python -m replay run PATH... [--policy fastest,driver_friendly,strict] [--rules JSON] [--json] [--no-switches]
    python -m replay fuzz [--seed N] [--count M] [--policy ...] [--corpus DIR]

`run` takes request files, directories of them, or `{"request": ..., "response": ...}` pairs (a pair is
replayed as given, not planned again). `fuzz` plans one seeded random request per seed and policy and
writes every failure to `replay/corpus/` with its seed. Exit code 0 when every row passes, 1 when any
fails, 2 on bad arguments.

## Interpretations the replay makes

Written down in `replay/FINDINGS.md` and the ledger of the build: extended days count in the calendar
week in which the 10th hour starts; a rest of 24 h or more is daily or weekly as the plan says; a
reduced daily rest may carry compensation (9 h plus the debt); a first part of a split break only counts
once driving has begun; Art 9(1) lets a regular weekly rest be interrupted only on a ferry or train
journey of 8 h or more with a cabin. The rules come from the UK revised text on legislation.gov.uk;
check the EU consolidated text before relying on a finding.
