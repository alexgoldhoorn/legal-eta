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

## plan

Request: common fields plus

- `crew`: `1` (default). Any other value is rejected; multi-manning is not modelled.
- `route`: non-empty list of items, `kind` selects the type:
  - `{kind: "drive", minutes}`
  - `{kind: "stop", id, service_minutes?, driver_works? (true), window? {earliest, latest}, rest_allowed? (true), counts_as_rest? (null), tags? []}`
  - `{kind: "ferry", id, minutes, sleeper_cabin? (false), boarding_minutes? (0)}`
- `policy`: `{preset: fastest|driver_friendly|strict, overrides}`; see `policy.md`.

Response:

| Field | Meaning |
|---|---|
| `arrival` | Arrival time at the end of the route. |
| `blocks` | The schedule. Each block: `kind` (`drive`, `break`, `daily_rest`, `weekly_rest`, `work`, `wait`, `ferry`), `start`, `end`, `minutes`, `at` (stop or ferry id, or null on the road), `subtype` (`regular`, `reduced`, `split_part_1`, `split_part_2`, `extended` for a drive, or null), `reason`, `article`, `counts_as_rest`, `interruptions` (Art 9: minutes of a rest interrupted by a ferry or train, non-empty means interrupted), `compensation_minutes` (compensation taken within this rest). |
| `stops` | Per stop: `id`, `arrival`, `departure`, `lateness_minutes`, `window_met`, `rested_here`. |
| `summary` | Minutes per kind plus `total`. |
| `driver_state_at_end` | `{raw, remaining}`: the state after the last block, in both forms. |
| `warnings` | `[{code, message, article}]`; see `warnings.md`. |
| `violations` | Always `[]` for a plan: a plan that does not validate is never returned. |
| `rules` | `{preset, overrides, effective}`; `effective` is every constant actually used. |
| `policy` | `{preset, overrides}` as used. |
| `disclaimer` | `Planning aid, not legal advice.` |

An Art 9 interrupted rest is a block with a non-empty `interruptions` list, not a separate subtype.

## validate

Request: common fields plus `blocks`: a list of `{kind, start, minutes, subtype?, at?, counts_as_rest?, interruptions?, compensation_minutes?, in_vehicle?}`. `in_vehicle: true` marks a regular weekly rest taken in the vehicle, which Art 8(8) forbids.
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
     state cannot be made legal, for example three reduced daily rests are used and a regular rest can no
     longer fit inside the 24 h window.
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
