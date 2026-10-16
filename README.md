# legaleta

**EU driving-time scheduler.** Give it a planned truck route (driving minutes, stops, ferries) and a driver
state; it inserts the breaks, daily rests and weekly rests that Regulation (EC) 561/2006 (as amended by
(EU) 2020/1054) requires, and returns the legal arrival time, the lateness at every stop, and the article
behind every block. It also validates a schedule somebody else made and projects a driver's tachograph
state into the future.

**Planning aid, not legal advice.** The output is an aid for dispatchers and planners. The driver and the
operator remain responsible for compliance. See *What is modelled* below for the limits.

## Why it matters

A route that ignores the driving-time rules arrives much earlier than the same route driven legally.
On these fixtures, built from a public freight-dispatch simulator (the author's own), 19 routes in four
datasets arrive 50 to 243 % later once breaks and rests are inserted:

| Dataset | Routes | Naive duration | Legal duration | Later by |
|---|---|---|---|---|
| benelux | 4 | 15 to 21 h | 24 to 32 h | 50 to 65 % |
| eu_longhaul | 4 | 59 to 98 h | 164 to 337 h | 159 to 243 % |
| iberia | 5 | 36 to 49 h | 67 to 92 h | 79 to 98 % |
| mixed | 6 | 162 to 264 h | 521 to 877 h | 219 to 243 % |

"Naive" is driving plus service plus waiting for delivery windows, with no breaks or rests. The gaps are
larger than a single-day route would show: multi-day routes add daily and weekly rests, and routes that need
more than 90 h of driving must also wait for the fortnight limit. The routes and the exact numbers are in
`tests/fixtures/sim/`. The simulator's freights carry tight delivery windows over long multi-stop chains,
which inflates lateness and the waiting-for-window component (and "naive" already includes window waits),
so these figures are specific to the fixtures, not typical.

## Install

    pip install legaleta            # library + CLI (pending publication: not on PyPI yet)
    pip install "legaleta[mcp]"     # + MCP server

## Quick start

    legaleta plan --pretty < examples/plan_request.json

The full request and response contract is in `docs/contract.md`; examples are in `examples/`
(`plan_request.json`, `validate_request.json`, `project_request.json`).

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from legaleta import POLICY_PRESETS, RULE_PRESETS, Clock, Drive, DriverState, Stop, plan

tz = ZoneInfo("Europe/Madrid")
clock = Clock(datetime(2026, 10, 19, 6, 0, tzinfo=tz), tz)
route = [
    Drive(310),
    Stop("A", service_minutes=45, driver_works=False),
    Drive(400),
    Stop("B", service_minutes=60),
]
result = plan(route, DriverState(), RULE_PRESETS["eu561"], POLICY_PRESETS["driver_friendly"], clock)
for b in result.blocks:
    print(
        clock.local(b.start).strftime("%a %H:%M"),
        b.kind,
        b.minutes,
        "·",
        b.reason,
        f"[{b.article}]",
    )
```

## What is modelled

- Art 4 definitions, Art 6 driving limits (9 h, 10 h twice a week, 56 h a week, 90 h a fortnight),
  Art 7 breaks (45 min, or 15 + 30), Art 8 daily rests (11 h, 9 h up to three times between weekly rests,
  3 h + 9 h split) and weekly rests (45 h; 24 h with compensation en bloc within three weeks), the
  weekly rest deadline of six 24 h periods, and Art 9 ferry interruptions (only of a regular daily rest
  or a weekly rest; a train crossing can be given as a ferry).
- Art 8(2) second sentence: a rest that is regular by length but has only 9 to 11 h inside its 24 h
  period counts as reduced (and raises `DAILY_REST_REDUCED_BY_WINDOW` when regular rests were asked for).
- Compensation for a reduced weekly rest is attached to the next weekly rest (or to a daily rest when
  that weekly rest could end after the deadline) and kept as one aggregate debt; driving is capped so the
  rest carrying it ends by the deadline, and a rest that ends later does not pay it.
- Lateness at a stop is measured when its service starts, after any wait or rest before it.
- A driver state whose 24 h period ends (or ended) too soon for a daily rest gets a plan that opens with a
  rest and the `DAILY_REST_OVERDUE_AT_START` warning; the shortfall is the state's, not the plan's.
- A plan is never illegal. `plan()` validates its own output with the checker and raises
  `SchedulingError` instead of returning a plan that breaks a rule. A plan may be late; it is never illegal.
- Single driver only: `crew` must be 1.
- **Not modelled** (reported as warnings, not planned): Art 12 departures from the rules, the four-week
  return home (Art 8(8a)), the Working Time Directive (2002/15/EC), and the international-transport
  exception (two consecutive reduced weekly rests).
- **Assumption on idle time at stops** (`WAIT_ASSUMED_REST`): a stop where the driver does not work, and
  waiting for a delivery window, count as break or rest. Directive 2002/15/EC treats waiting of unknown
  duration as *availability*, not rest. Set `policy.wait_counts_as_rest: false`, or the stop's
  `counts_as_rest: false`, to be strict.
- Rule constants are a preset you can override per request; the response echoes what was used.

## Policies

`fastest` (default: latest-legal placement, uses the 10 h days and 9 h rests), `driver_friendly` (breaks at
convenient stops, rests at rest places in the evening, regular 11 h rests), `strict` (no split breaks, no
reduced or extended anything, rests only at rest places). A rest place is a stop whose `tags` contain
`"rest"` (or a ferry cabin). Every knob is documented in `docs/policy.md`.

## Warnings and errors

Every warning code is listed in `docs/warnings.md`, including `DAILY_REST_OVERDUE_AT_START` and
`REST_AT_STOP_NOT_ALLOWED` (a rest at a stop marked `rest_allowed: false`, when nothing else was legal).

The driver state may be given in tachograph `remaining` form; `docs/contract.md` gives the exact meaning of
each field (for example, `remaining_drive_today` is read against the 9 h cap and `daily_rest_due_in` is when
the rest must be complete). Check them against your telematics feed.

Errors:

- Invalid input (malformed JSON, unknown field, `crew` other than 1, unknown preset or knob, an
  out-of-range rule override or rest window, duplicate stop ids, a route of only zero-minute drives): CLI
  exit code 2, message on stderr; the MCP tools return `{"error": ..., "disclaimer": ...}`.
- No legal plan exists (`SchedulingError`): CLI exit code 1, message on stderr; the MCP tools return the same
  `error` object. Causes: the initial driver state cannot be made legal (for example a compensation debt due
  before any rest carrying it can end); a stop or ferry whose waiting, service or
  crossing time leaves no room for a daily or weekly rest in time (a ferry crossing without a sleeper cabin
  counts as a break, so the rest is taken at the port and the crossing must fit in the 24 h after it; crossings
  up to 15 h planned in every case tried, longer ones raise); a driver who cannot legally drive again within
  the plan; and an internal no-progress guard that should not occur. Details in `docs/contract.md`.

## MCP server

    legaleta-mcp     # stdio; tools: plan_route, validate_timeline, project_driver_state

Tested with `mcp` 2.x.

## Development

    uv sync --all-extras
    uv run pytest -q
    uv run ruff check . && uv run ruff format --check . && uv run mypy src

CI also requires 100 % line coverage on `rules.py`, `state.py` and `checker.py`
(`scripts/check_coverage.py`).

## Replay: an independent check of the plans

`replay/` executes a plan as a driver would, minute by minute, keeps its own record against its own
copy of the regulation, and reports every place where plan and replay disagree (a rule broken, a time
or total the plan claims that the replay does not reproduce, a warning that is missing). It shares no
code with the scheduler or the checker. It is a test tool and is not shipped in the wheel.

    uv run python -m replay run tests/fixtures/sim examples      # plan and replay request files
    uv run python -m replay run pair.json                        # replay a given plan: {"request", "response"}
    uv run python -m replay fuzz --seed 1 --count 100            # seeded random requests

See `docs/replay.md`. What it found so far: `replay/FINDINGS.md`.

## Licence

AGPL-3.0-or-later. Commercial licences: see `COMMERCIAL-LICENSE.md`. Provenance: `PROVENANCE.md`.
