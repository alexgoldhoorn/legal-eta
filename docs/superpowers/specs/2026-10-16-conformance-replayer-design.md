# Conformance replayer: design

Date: 2026-10-16. Status: draft for approval. Planning aid, not legal advice.

## Intent and why

legaleta inserts breaks and rests into a route so that it is legal under Regulation (EC) 561/2006. Today
its correctness is established by its own checker (`checker.py`, built on `state.apply()`), which is the
same state machine the scheduler steers by, and by tests written by the same author from the same reading
of the regulation. Six review rounds have each found real bugs in Art 8/9 edge cases, which says the
self-consistency net has holes.

The replayer is an independent check. It takes a plan produced by legaleta, **executes it as a driver
would** (minute by minute, as a log of activities), keeps its own tachograph record, and compares what
happened with what the plan claimed. Any disagreement is a finding: a bug in legaleta, a bug in the
replayer, or a documented interpretation difference. The point is that the first two are told apart
by a second, differently built implementation of the rules.

It is also the checker for the later fleet simulator (a separate project): that simulator will reuse
the replayer as its "did the real day stay legal" oracle.

## Goals

- Replay any `plan` response (and, optionally, any `validate` request) and report **violations** of
  Art 6, 7, 8 and 9 found in the executed log.
- Check the plan's **claims** against the replay: arrival time, per-stop arrival, departure and
  lateness, block contiguity and times, `summary` totals, `driver_state_at_end` (raw and remaining),
  and the warnings that must be present (for example `WAIT_ASSUMED_REST` whenever a wait was counted as
  rest).
- Be independent: share no code with `src/legaleta`, and use a different algorithm (below).
- Be able to fail: a mutation suite shows that tampering with a plan is caught. A replayer that never
  reports anything proves nothing.
- Run over the 19 simulator fixtures, the examples, and randomized requests, in all three policies, and
  in CI.

## Non-goals

- Not a fleet simulator: no orders, calendars, noise, re-planning or dispatch. That is project two.
- Not a tachograph file parser, and not a replacement for `legaleta validate`.
- Not legal advice, and no new rules. Out-of-scope items stay out of scope: Art 12, return home, the
  Working Time Directive, the international exception, multi-manning.
- Anti-goal: a replayer that quietly reuses `state.py`, `rules.py` or `checker.py`. That would make
  every pass meaningless, even though it would be shorter to write.

## Design

### Placement

A top-level package `conformance/` in the legal-eta repo, **not shipped** in the wheel (the wheel packages
only `src/legaleta`). Run with `uv run python -m conformance`. It talks to legaleta only through the
public JSON API (`legaleta.api.plan_json`, or the plan JSON read from a file or stdin), never through
internals. A test fails if anything in `conformance/` imports another `legaleta` module.

### Two-stage replay

1. **Execute.** Turn the plan's blocks into a flat activity log at one-minute resolution on its own clock:
   `DRIVE`, `WORK`, `AVAILABILITY`, `REST` (with the originating block's `kind`, `subtype` and
   interruption windows kept as labels, not trusted). Gaps and overlaps between blocks are findings.
   The stop table is replayed the same way: arrival, service start, departure, lateness, recomputed
   from the log, not copied from the plan.
2. **Judge.** Evaluate the rules over the finished log **retrospectively**, the way an enforcement
   officer reads a tachograph: look back from each point, never carry a state machine forward. This is
   deliberately a different algorithm from `state.apply()`:
   - daily rest periods are *recognised* from the log (any rest of at least 9 h, a 3 h + 9 h pair) and
     then 24 h periods are counted back to back from each recognised rest end, so a "regular" or
     "reduced" label on a block is a claim to check, not an input;
   - break compliance is "in every window of continuous driving of 4.5 h, a qualifying break or a
     15 + 30 split exists", computed over the log;
   - weeks are fixed calendar weeks in `week_timezone`; fortnight is this week plus last, and the
     driver's starting state is converted by an independent, documented reading of the contract's
     `raw` and `remaining` fields;
   - compensation is recomputed from the sequence of reduced weekly rests and checked against the
     rests that pay it, including the three-week deadline;
   - Art 9 interruptions are checked on the executed log (which rest, how many, how long).

### Rules data

The replayer holds **its own copy** of the regulation's numbers, written from the regulation text, not
imported from `rules.py`. If a plan echoes `rules.effective` that differs from the regulation defaults,
the replayer applies the plan's values and reports `NON_STANDARD_RULES` so that a passing run on
overridden rules is never mistaken for a passing run on the law.

### Interpretation switches

The places where legaleta documents an assumption are switches in the replayer, with the same defaults
as legaleta's documented policy: waiting and non-working stops count as rest (`WAIT_ASSUMED_REST`),
Guidance Note 7 handling of an overdue daily rest at start, and a rest that is regular by length but has
under 11 h inside its 24 h period counts as reduced (Art 8(2), second sentence). A finding that
disappears when a switch is flipped is reported as an **interpretation difference**, not a violation.

### Findings

Every finding carries a class, so triage is fast:

| Class | Meaning |
|---|---|
| `VIOLATION` | The executed log breaks an article. A bug in legaleta or in the replayer. |
| `CLAIM_MISMATCH` | The plan says something the replay does not reproduce (times, totals, labels, end state). |
| `MISSING_WARNING` / `SPURIOUS_WARNING` | A warning the replay says must (or must not) be there. |
| `INTERPRETATION` | Disagreement that disappears under a documented switch. |
| `NON_STANDARD_RULES` | Replay ran on overridden constants. |

Output: a verdict (`PASS` / `FAIL`, exit code 0 / 1) and a block-by-block diff report (text and JSON)
with plan time, replay time, article, and minutes of excess. A batch run prints one summary row per
route and policy.

### Proving the replayer itself

- **Golden scenarios** for the replayer's rules module, taken from the Commission guidance notes and the
  regulation's own examples, with the expected verdict stated in each test's docstring and the source named.
  Without these two implementations could agree and both be wrong.
- **Mutation suite**: take legal plans and tamper with them (shorten a rest by one minute, move a drive
  into a rest, drop a break, push a weekly rest past its deadline, change a stop's lateness), and assert
  each mutant is flagged and each untouched plan passes.
- **Differential check** against `legaleta validate` on the same blocks: they must agree on pass/fail.
  Disagreements are listed, not asserted, because they are the interesting output.

### Inputs for the first runs

The 19 fixtures in `tests/fixtures/sim/` and `examples/`, each in `fastest`, `driver_friendly` and
`strict`; Hypothesis-generated requests (multi-week routes, ferries with and without cabin, windows,
awkward initial driver states, compensation debts); `remaining`-form drivers.

## Constraints

- Python 3.12, standard library only for the replayer itself (plus pytest and Hypothesis in tests, as now).
- Runs from the repo checkout, no network. A full fixture sweep should finish in under a minute.
- Never imports `legaleta` internals. Never edits `src/`. A bug it finds in legaleta is reported and
  fixed in a separate, reviewed change.
- Replayer code follows the repo's ruff and mypy settings; the coverage gate does not apply to it, but its
  rules module is held to the same bar by the golden and mutation tests.

## Calls I made (change any of them)

- Python inside the repo, not Julia in the freight simulator: it runs in-process in CI and is easy to keep
  honest. Independence comes from a separate rules copy and a retrospective algorithm, not from a language.
- Retrospective judging rather than a second forward state machine, because a second forward machine
  written by the same person tends to inherit the same blind spots.
- Both a verdict and a diff report.
- Waiting counts as rest by default, matching legaleta's documented default, with the switch above.
- Package name `conformance/`, run as `python -m conformance`.
- Replays `plan` output first; `validate` requests are accepted as a second input form but are not the
  focus of version one.

## Left to the builder

Internal module layout, the activity-log data structure, report formatting, how the randomized generator
is parameterised, and the exact list of mutations beyond the ones named.

## Open questions for Alex

None blocking. Two things worth a look when reading: the interpretation switches (does the default list
match how you would defend a plan to an auditor?) and whether you want the fleet-simulator hook
(a `replay(plan, world_events)` entry point) specified now or left to project two. I assumed the latter.
