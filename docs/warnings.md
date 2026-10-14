# Warnings

Every plan response carries a `warnings` list of `{code, message, article}`. Warnings never mean the
plan is illegal: a plan is validated by the checker before it is returned, and an illegal plan is never
returned (see *Errors* in `contract.md`). Warnings tell you what the plan assumes or does not cover.

Planning aid, not legal advice.

| Code | When it appears | Meaning | Article | What to do |
|---|---|---|---|---|
| `ART12_NOT_MODELLED` | every plan | Departures from the rules under Art 12 (up to +1 h, or +2 h, to reach the base or home for a weekly rest) are not planned. They are the driver's decision on the road. | Art 12 | Nothing, unless the driver needs the exception; then re-plan from the new position. |
| `RETURN_HOME_NOT_CHECKED` | every plan | The obligation to let the driver return home or to base every four weeks is not checked. | Art 8(8a) | Check the driver's return-home schedule yourself. |
| `WTD_NOT_CHECKED` | every plan | Working-time limits of Directive 2002/15/EC are not checked. | Directive 2002/15/EC | Check working time separately. |
| `WAIT_ASSUMED_REST` | idle time at a stop (waiting for a window, or service the driver does not work) was counted as a break or rest | Directive 2002/15/EC treats waiting of unknown duration as availability, not rest. The plan assumes it can be used as rest. | Art 4(d); Art 4(f) | If the driver will not really be free, set `policy.wait_counts_as_rest: false`, or `counts_as_rest: false` on the stop, and re-plan. |
| `REGULAR_WEEKLY_REST_ON_ROAD` | a regular weekly rest was placed between stops (the block has `at: null`) | A regular weekly rest must not be taken in the vehicle, so accommodation is needed wherever the rest falls. | Art 8(8) | Book accommodation near the position shown, or add a stop with accommodation to the route so the rest can be tagged there. |
| `INTERNATIONAL_EXCEPTION_NOT_USED` | the previous weekly rest was reduced, so a regular one is required next | The international-transport exception (two consecutive reduced weekly rests outside the Member State of establishment) is not modelled, so the plan takes a regular weekly rest. | Art 8(6) | If the exception applies to this trip, the real schedule may be shorter than the plan. |
| `PREFERENCE_UNMET:<knob>` | a preference could not be honoured without breaking the rules; `<knob>` is `rest_places` or `rest_start_window` | The policy asked for rests at tagged places, or inside a daily window as a hard constraint. Honouring it would have left only weekly rests, or nothing, so the latest-legal placement was used instead. | none | Tag suitable stops as rest places (`"tags": ["rest"]`), widen the window, or accept the placement. |
| `DAILY_REST_REDUCED_BY_WINDOW` | the policy asked for regular daily rests, and a rest of 11 h or more started so late in its 24 h period that fewer than 11 h of it fall inside the period | The rest is labelled reduced (subtype `reduced`) although it is 11 h long, and it counts against the three reduced rests allowed between weekly rests. | Art 8(2) | Start the rest earlier, or accept the use of a reduced rest. |
| `DAILY_REST_OVERDUE_AT_START` | the 24 h period after the driver's last daily rest ends less than 9 h after `start`, or ended before it | No daily rest can be completed in that period any more. The plan opens with a rest; the shortfall belongs to the driver state, not the plan, and the next 24 h period is counted from the end of the old one (Guidance Note 7). | Art 8(2) | Check the driver state; the infringement, if real, has already happened. |
| `REST_AT_STOP_NOT_ALLOWED` | a rest was planned at a stop with `rest_allowed: false` | No other placement kept the plan legal (typically a rest that must start before the stop's service), and legality outranks the stop's setting. | none | Allow rests at the stop, or change the route or the start so the rest falls elsewhere. |
| `STATE_FROM_REMAINING` | the request gave the driver in tachograph `remaining` form | The raw driver state was reconstructed from the remaining times. The message lists the assumptions that conversion made (for example that `remaining_drive_today` was read against the 9 h cap, or that compensation owed without a deadline is treated as due at the end of the plan). | none | Prefer the `raw` form when you know when the last rests ended. |

The three standing warnings (`ART12_NOT_MODELLED`, `RETURN_HOME_NOT_CHECKED`, `WTD_NOT_CHECKED`) appear
first, in that order; the others follow in the order they were first raised, each at most once per plan.
