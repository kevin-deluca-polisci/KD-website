# Plan: capturing professional forecasts on November 1

**Status: draft, 2026-10-02.** Nothing here is built yet. Decisions marked
**[Kevin]** are open.

## Goal

Record each professional forecaster's forecast as it stood on **November 1,
2026**, the day the student forecasts and the class model lock, so that all
three can be scored on the same information. Record the final pre-election
forecast (November 2) as well, as `score/RULES.md` already requires.

What to record for each forecaster, where published:

- House and Senate control: chance of Democratic control, expected seats.
- National House vote margin.
- Every Senate race, and every House race they publish: chance the Democrat
  wins, and expected margin.

## The forecasters

| Forecaster | How it reaches us now | Route for November 1 |
|---|---|---|
| Kalshi, Polymarket, PredictIt | automated daily capture | automated (no change) |
| Race to the WH | automated daily capture | automated (no change) |
| Cook, Sabato, Inside Elections (ratings) | Wikipedia ratings tables, automated | automated; ratings are not scored (RULES.md §2) |
| Decision Desk HQ / The Hill | not collected (terms prohibit scraping) | permission request, else manual record |
| Silver Bulletin | not collected (subscriber model) | permission request, else manual record |
| The Economist | not collected (registration wall) | check GitHub for a data release; permission request; else manual record |
| FiftyPlusOne / G. Elliott Morris | not collected (terms unread) | read the subscription terms; permission request; else manual record |
| Split Ticket (via 270toWin / The Argument) | not collected | permission request (the Datawrapper CSV route is already verified); else manual record |
| VoteHub | not collected (terms prohibit scraping) | permission request, else manual record |
| JHK Forecasts | not collected | ask Jack Kersting directly |

## Three tracks

**A. Automated sources.** Nothing new to build. Add a second scheduled run on
November 1 and 2 (morning and evening, Eastern) so a failed run cannot leave the
day empty, and check both days' snapshots on the morning of November 2.

**B. Permission requests** **[Kevin: send, and when]**. A narrow ask is more
likely to get a yes than a general one: a one-time copy of the November 1 and
final forecasts (chamber and race level), used for an academic accuracy
comparison with student forecasts, published only after the election and with
attribution. A forecaster who prefers not to send data can say whether we may
score the numbers shown on their public page. To be useful these need to go out
by about October 9, three weeks before the date.

**C. Manual record, for every forecaster without permission (and as a backup
for those with it).** On November 1 at a fixed time (noon Eastern), one person
opens each forecaster's page and:

1. enters the chamber-level numbers and every race-level number into a
   template CSV (schema below);
2. saves a PDF print of each page and a screenshot, named with the source and
   time;
3. submits each page URL to the Wayback Machine's "Save Page Now", which makes
   an independent, time-stamped public copy.

The same is repeated on November 2. Numbers recorded by hand stay in the
private tier: they are used for scoring, and whether a forecaster's scores are
published by name follows the permission decision for that forecaster, as for
every other gated source. **[Kevin: who does the recording; whether House
coverage is all published races or the competitive ones only.]**

A practice run around October 15 will show how long the manual record takes
per forecaster and whether the template works.

## After the election

Many forecasters publish their model output after the election. Request or
download those files, compare them with the manual record, and use the
forecaster's own file wherever it exists, keeping the manual record as the
time-stamped evidence.

## Student forecasts

Same schema, same race identifiers (`HOU_<state>_<district>_2026`,
`SEN_<state>_2026`). Locked at 11:59 pm Eastern on November 1 and stored in the
private repo. Scored by the same code (RULES.md §1); only the prize winner's
forecast is made public.

## Scoring

`score/RULES.md` scores at 180, 120, 90, 60, 30, 14, 7 and 1 day(s) before the
election plus `final`. November 1 is two days before, so it is not yet a
horizon. Add it as a named horizon, `student_lock`, by an amendment under §11
before November 1, so the comparison with students is pre-registered rather
than chosen afterwards. **[Kevin: approve the amendment.]**

## Schema for recorded forecasts

One CSV per forecaster per capture, in `model_private/nov1/`:

| column | meaning |
|---|---|
| `source_id` | registry id, e.g. `ddhq` |
| `captured_at` | time recorded, ISO 8601 with time zone |
| `method` | `automated`, `manual` or `provided` (sent by the forecaster) |
| `url` | page the number was read from |
| `race_id` | as in the archive |
| `quantity` | `win_prob_D`, `margin_D`, `seats_D` |
| `value` | as shown on the page, converted to the archive's units |
| `as_shown` | the text as it appears on the page, e.g. "71 in 100" |
| `evidence` | file name of the PDF or screenshot |
| `wayback_url` | the Save Page Now link |

## Timeline

| Date | Step |
|---|---|
| Oct 2-9 | confirm each forecaster's pages and what they show; write the template and checklist; send permission requests |
| Oct 15 | practice run of the manual record |
| Oct 25 | RULES.md amendment for `student_lock`; confirm the extra scheduled runs |
| Nov 1 | capture (automated + manual), noon Eastern; students lock 11:59 pm |
| Nov 2 | final capture |
| Nov 4 onward | results; collect forecasters' own files; score |
