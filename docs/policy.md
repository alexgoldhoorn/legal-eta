# Policy and rule knobs

Planning aid, not legal advice.

Two things configure a plan:

- **Rules** (`rules.preset`, `rules.overrides`): the numeric constants of Regulation (EC) 561/2006.
  One preset, `eu561`. Overrides are integer minutes (or booleans); unknown names are rejected.
  The response echoes what was used under `rules.effective`.
- **Policy** (`policy.preset`, `policy.overrides`): preferences about *where* and *when* legal
  breaks and rests are placed. Legality is never traded for a preference, and never for lateness: a
  plan may be late, it is never illegal.

## Policy knobs

| Knob | Values (default first) | Meaning |
|---|---|---|
| `break_timing` | `latest`, `earliest_convenient`, `balanced` | `latest`: break when the 4.5 h limit is due. `earliest_convenient`: take the break at a stop once 180 min of driving have passed (`earliest_convenient_after_minutes`; penalty 30). `balanced`: use a stop's idle time as the break only when that time is within 15 min of covering it. |
| `earliest_convenient_after_minutes` | `180` | Driving minutes after which `earliest_convenient` accepts a stop. |
| `break_at_stop_lookahead_minutes` | `60` | How far ahead a stop is looked for as a break location. |
| `split_breaks` | `allow`, `never`, `prefer` | 15 + 30 min split breaks (Art 7). `prefer` carries a penalty of 15 when not used. |
| `break_at_working_stop` | `after`, `before` | For a stop where the driver works: take the break after or before the work. |
| `daily_rest_type` | `reduced_eager`, `regular`, `reduced_when_needed` | `regular`: 11 h rests. `reduced_when_needed`: 9 h only when a window or limit needs it. `reduced_eager`: 9 h whenever one of the three is left. |
| `split_daily_rest` | `allow`, `never` | 3 h + 9 h split daily rest. |
| `extended_driving` | `eager`, `never`, `when_needed` | 10 h driving days (twice a week). `when_needed`: only taken when a delivery window would otherwise be missed. |
| `rest_places` | `anywhere`, `prefer_tagged`, `tagged_only` | Rests at stops tagged in the route (`tags`). `prefer_tagged` carries a penalty of 60 for a rest elsewhere. |
| `rest_place_lookahead_minutes` | `90` | How far ahead a tagged place is looked for. |
| `rest_start_window` | `null` or `{earliest, latest, hard?, weight?}` | Daily window (local `HH:MM`, may cross midnight) in which a daily rest should start. `hard: true` removes choices outside it; otherwise it is a soft preference: a rest started outside it costs `weight` x 60. |
| `extend_rest_to` | `null` or `HH:MM` | Stretch a rest so it ends at this local time. Bounded by `extend_rest_max_minutes`; never applied to a rest on a ferry. |
| `extend_rest_max_minutes` | `360` | Most that `extend_rest_to` may add to one rest. |
| `weekly_rest_type` | `reduced_when_needed`, `regular`, `reduced_eager` | 45 h or 24 h weekly rests. A reduced weekly rest carries compensation. |
| `wait_counts_as_rest` | `true` | Waiting and idle service time count as break or rest (see `WAIT_ASSUMED_REST`). |
| `lookahead_stops` | `3` | Number of stops the planner simulates ahead when choosing between legal options (at least 1). |
| `lateness_weight` | `1.0` | Cost per minute of lateness at a stop window. |
| `arrival_weight` | `0.1` | Cost per minute of final arrival time. |

## Semantics worth knowing

- **Art 8(2) second sentence is enforced.** A rest labelled regular by length that has only 9 to 11 h
  inside its 24 h period counts as reduced; the subtype `reduced` can therefore appear on an 11 h
  block. When the policy asked for regular rests this raises `DAILY_REST_REDUCED_BY_WINDOW`.
- **Hard windows fall back.** If a hard `rest_start_window` (or `tagged_only`) would leave only weekly
  rests or nothing at all, the choices are restored and `PREFERENCE_UNMET:<knob>` is raised.
- **Compensation.** The compensation for a reduced weekly rest is attached to the next weekly rest
  (or to an earlier daily rest if the deadline is nearer) and kept as one aggregate debt, shown as
  `compensation_minutes` on the rest block.
- **Defaults are eager.** `extended_driving: eager` and `daily_rest_type: reduced_eager` are the
  defaults, so the `fastest` preset (which is the default policy) is the fastest.
  `extend_rest_max_minutes` bounds `extend_rest_to`, so a 06:00 target never adds 23 h.

## Presets

| Knob | `fastest` | `driver_friendly` | `strict` |
|---|---|---|---|
| `break_timing` | latest | balanced | latest |
| `split_breaks` | allow | allow | never |
| `daily_rest_type` | reduced_eager | regular | regular |
| `extended_driving` | eager | when_needed | never |
| `rest_places` | anywhere | prefer_tagged | tagged_only |
| `rest_start_window` | none | 19:00 to 22:00, soft | none |
| `weekly_rest_type` | reduced_when_needed | reduced_when_needed | regular |

All other knobs keep the defaults above.

## Rule constants

`rules.overrides` accepts any field of the rule set, in minutes unless stated: `break_after_driving`
(270), `break_full` (45), `break_split_first` (15), `break_split_second` (30), `daily_driving` (540),
`daily_driving_extended` (600), `extended_days_per_week` (2), `weekly_driving` (3360),
`fortnight_driving` (5400), `daily_rest_regular` (660), `daily_rest_reduced` (540),
`daily_rest_split_first` (180), `daily_rest_split_second` (540), `reduced_daily_rests_between_weekly` (3),
`daily_window` (1440), `weekly_rest_regular` (2700), `weekly_rest_reduced` (1440),
`weekly_rest_deadline` (8640), `compensation_deadline_weeks` (3), `compensation_attach_min_rest` (540),
`regular_weekly_rest_in_vehicle` (false), `ferry_interruptions_max` (2), `ferry_interruption_total` (60),
`ferry_weekly_rest_min_crossing` (480), `ferry_weekly_rest_needs_cabin` (true), `min_break_portion` (15).
