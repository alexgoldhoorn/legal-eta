# JSON contract

Planning aid, not legal advice.

Three operations take one JSON object and return one JSON object: `plan`, `validate`, `project`. The
same contract is used by the Python functions in `legaleta.api` (`plan_json`, `validate_json`,
`project_json`), the CLI (`legaleta plan|validate|project`, JSON on stdin, JSON on stdout) and the MCP
tools (`plan_route`, `validate_timeline`, `project_driver_state`). The plan request schema is also
served as the MCP resource `legaleta://schema/plan`. All durations are integer minutes; all datetimes
are ISO 8601 with an offset. Unknown fields are rejected.

## Common fields

| Field | Type | Meaning |
|---|---|---|
| `start` | datetime with offset | Time zero of the plan, project or timeline. |
| `week_timezone` | IANA zone, optional | Zone in which weeks (Monday 00:00 to Sunday 24:00) are counted. Default: the offset of `start`. |
| `driver` | object, optional | `{raw}` or `{remaining}` (below). Omitted: a driver who has just finished a weekly rest, fresh counters. |
| `rules` | `{preset, overrides}` | Rule constants; preset `eu561`. See `policy.md`. |

### Driver state, raw form

`driving_since_break`, `split_break_first_part_taken`, `driving_today`, `last_daily_rest_end`
(datetime), `split_daily_rest_first_part_taken`, `reduced_daily_rests_used`, `driving_this_week`,
`driving_last_week`, `extended_days_used_this_week`, `last_weekly_rest_end` (datetime),
`last_weekly_rest_reduced`, `compensation_owed_minutes`, `compensation_deadline` (datetime or null).
All optional, defaulting to zero or false.

### Driver state, remaining form (tachograph style)

`remaining_drive_before_break`, `remaining_drive_today`, `remaining_drive_this_week`,
`remaining_drive_fortnight`, `daily_rest_due_in`, `weekly_rest_due_in` (all required), plus the optional
`split_break_first_part_taken`, `split_daily_rest_first_part_taken`, `reduced_daily_rests_used`,
`extended_days_used_this_week`, `last_weekly_rest_reduced`, `compensation_owed_minutes`,
`compensation_deadline_in`. If both forms are given, fields present in both must agree. A plan from
a `remaining` driver carries the `STATE_FROM_REMAINING` warning.

All values are minutes from `start`. Telematics feeds do not all mean the same thing by these names, so
check each one against this table. Where a feed may differ, the conservative fill is given: it can only
make the plan earlier-resting, never illegal.

| Field | Exact meaning here | Limit it is read against | Converted to (raw) | Conservative fill if your feed differs |
|---|---|---|---|---|
| `remaining_drive_before_break` | Driving left before the 45 min break is due. | 4.5 h (Art 7) | `driving_since_break = 270 - value` | As reported. If a 15 min first part was taken, also set `split_break_first_part_taken`. |
| `remaining_drive_today` | Driving left today **against the 9 h cap**, also on a day that may still be extended; negative once the day is already extended beyond 9 h. The plan may still extend the day to 10 h if `extended_days_used_this_week` allows. | 9 h (Art 6(1)) | `driving_today = 540 - value` | A feed that reports against 10 h on an extension day is 60 min optimistic: subtract 60, or pass the raw form. |
| `remaining_drive_this_week` | Driving left in the fixed week (Monday 00:00 to Sunday 24:00 in `week_timezone`). | 56 h (Art 6(2)) | `driving_this_week = 3360 - value` | A rolling-7-day figure: use the smaller of the two. |
| `remaining_drive_fortnight` | Driving left over this week and last week together. | 90 h (Art 6(3)) | `driving_last_week = max(0, 5400 - value - driving_this_week)` | If your feed has none: 5400 minus the driving of this week and last week. |
| `daily_rest_due_in` | When the current 24 h period ends: the next daily rest must be **complete** by then (complete-by). | 24 h period (Art 8(2)) | `last_daily_rest_end = start - (1440 - value)` | A feed that reports when the rest must **start**: add 540 (the shortest rest; adding 660 would be optimistic when a regular rest is needed). |
| `weekly_rest_due_in` | When the next weekly rest must **start** (start-by): six 24 h periods after the end of the previous weekly rest. | Art 8(6) | `last_weekly_rest_end = start - (8640 - value)` | A feed that reports complete-by: subtract 2700 (the regular weekly rest; subtracting 1440 would be optimistic whenever a regular one is needed). |
| `compensation_deadline_in` | When the compensation must be **complete**: the rest carrying it must end by then (Art 8(6b)). | Art 8(6b), 8(7) | `compensation_deadline = start + value` | Omitted while `compensation_owed_minutes > 0`: there is no deadline within the plan; the debt is attached to the next weekly rest. Give it whenever you know it. |
| other fields | The same as in the raw form. | | | |

`from_remaining` also assumes that the last daily and weekly rests ended exactly when the due-in values
imply, and that no break or rest is in progress at `start`. The `STATE_FROM_REMAINING` warning repeats the
assumptions that applied to the request (the 9 h reading of `remaining_drive_today`, a negative value, a
compensation debt without a deadline). Prefer the raw form when you know when the last rests ended.

## plan

Request: common fields plus

- `crew`: `1` (default). Any other value is rejected; multi-manning is not modelled.
- `route`: non-empty list of items, `kind` selects the type:
  - `{kind: "drive", minutes}`
  - `{kind: "stop", id, service_minutes?, driver_works? (true), window? {earliest, latest}, rest_allowed? (true), counts_as_rest? (null), tags? []}`. A stop whose `tags` contain `"rest"` is a rest place for `policy.rest_places`; other tags are carried but not read. `rest_allowed: false` is honoured unless no other placement is legal (then `REST_AT_STOP_NOT_ALLOWED`).
  - Stop and ferry ids must be unique, and a route of only zero-minute drives is empty: both are invalid requests.
  - `{kind: "ferry", id, minutes, sleeper_cabin? (false), boarding_minutes? (0)}`
- `policy`: `{preset: fastest|driver_friendly|strict, overrides}`; see `policy.md`.

Response:

| Field | Meaning |
|---|---|
| `arrival` | Arrival time at the end of the route. |
| `blocks` | The schedule. Each block: `kind` (`drive`, `break`, `daily_rest`, `weekly_rest`, `work`, `wait`, `ferry`), `start`, `end`, `minutes`, `at` (stop or ferry id, or null on the road), `subtype` (`regular`, `reduced`, `split_part_1`, `split_part_2`, `extended` for a drive, or null), `reason`, `article`, `counts_as_rest`, `interruptions` (Art 9: minutes of a rest interrupted by a ferry or train, non-empty means interrupted), `compensation_minutes` (compensation taken within this rest). |
| `stops` | Per stop: `id`, `arrival`, `departure`, `lateness_minutes`, `window_met`, `rested_here`. Lateness is measured when the **service starts**, after any wait, break or rest placed before it (a rest that covers the wait for a window does not hide a missed slot): `lateness_minutes = max(0, service start - window latest)`, and `window_met` is `lateness_minutes == 0`. `arrival` is when the vehicle reaches the stop. |
| `summary` | Minutes per kind plus `total`. |
| `driver_state_at_end` | `{raw, remaining}`: the state after the last block, in both forms. |
| `warnings` | `[{code, message, article}]`; see `warnings.md`. |
| `violations` | Always `[]` for a plan: a plan that does not validate is never returned. |
| `rules` | `{preset, overrides, effective}`; `effective` is every constant actually used. |
| `policy` | `{preset, overrides}` as used. |
| `disclaimer` | `Planning aid, not legal advice.` |

An Art 9 interrupted rest is a block with a non-empty `interruptions` list, not a separate subtype. Only a
regular daily rest or a weekly rest may carry interruptions (Art 9(1)); the scheduler never interrupts a
reduced daily rest or a part of a split one.

## validate

Request: common fields plus `blocks`: a list of `{kind, start, minutes, subtype?, at?, counts_as_rest?, interruptions?, compensation_minutes?, in_vehicle?}`. `in_vehicle: true` marks a rest taken in the vehicle; Art 8(8) forbids that for a regular weekly rest and for a compensation rest of more than 45 h. `counts_as_rest` defaults to false: a `wait` or `ferry` block counts as a break only when it is true.

The checker judges a rest by what it counts as, not by its `subtype`: a block's subtype is checked as a claim
(a rest shorter than its subtype claims is reported), but Art 8(4), Art 8(6) (no two consecutive reduced weekly
rests), Art 8(8) and Art 9(1) use the length, the part inside the 24 h period and the driver state. Compensation
is paid only by a rest that ends on or before the compensation deadline; a later one leaves the debt owed and
it is reported at the deadline (Art 8(6b)). A deadline that passed before `start`, and a 24 h period that ends
too soon after `start` for any daily rest to fit in it, belong to the driver state: they are restarted, not
reported (Guidance Note 7).
Response: `violations` (`[{article, block_index, message, excess_minutes}]`), `driver_state_at_end`, `disclaimer`.

## project

Request: common fields plus `activity` (`driving`, `resting`, `other_work`) and exactly one of `minutes` or `until` (datetime).
Response: `state` (`{raw, remaining}` at the horizon), `envelope` (what is still allowed: `drive_before_break`, `drive_today`, `drive_this_week`, `drive_fortnight`, `daily_window_ends_in`, `weekly_rest_due_in`, `extension_available`, the allowed daily and weekly rest subtypes, `compensation_due_in`), `first_limit` (`{minutes_from_now, at, limit}` for the first limit hit, or null), `disclaimer`.

## Errors

- **Invalid request** (`RequestError`): malformed JSON, unknown field, wrong type, `crew` other than 1,
  unknown preset or knob, a window whose `latest` precedes `earliest`, driver forms that disagree, and so on.
  The message names the offending field. CLI: message on stderr, exit code 2.
- **No legal plan** (`SchedulingError`): the request is well formed but no legal schedule exists. CLI:
  `cannot plan: ...` on stderr, exit code 1. Causes:
  1. `the driver state admits no legal plan: ...` (followed by the checker's violations). The initial
     state cannot be made legal, for example 900 min of compensation are owed with a deadline 100 min
     after `start`: no rest of 9 h plus 900 min can end by then. (A driver with three reduced daily rests
     used and no room left for a regular one is not such a case: the plan opens with a weekly rest.)
  2. `no legal plan through stop X` / `through the ferry`: `its waiting, service or crossing time leaves no
     room for a daily or weekly rest in time`. This applies to stops as well as ferries: a long wait for a
     window, or a service time, that overruns the rest deadline. For a ferry without a sleeper cabin the
     crossing counts as a break, not a rest, so the daily rest must be taken at the port before boarding
     (the scheduler offers that) and the crossing must then fit inside the 24 h period that follows. In
     practice crossings up to 15 h without a cabin planned in every case tried; longer ones raise. The
     pinned example is `test_crossing_too_long_for_any_rest_raises`.
  3. `the driver cannot legally drive again within this plan; check the driver state`: at a point where
     driving must go on, no legal choice exists and even waiting for the next week to begin does not let the
     driver drive again.
  4. `no progress after 100000 steps; check the driver state and rule overrides`: an internal guard that
     should not occur.
- Any other exception is an internal error (CLI exit code 1, prefixed `internal error:`).

The MCP tools return `{"error": "...", "disclaimer": "..."}` for both a bad request and a scheduling error.
