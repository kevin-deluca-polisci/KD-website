# Conditions on the ground

Three hand-maintained tables recording **what was true, and when it became
known**, for the two facts our models depend on and cannot currently see:

| file | what it records |
|---|---|
| `redistricting_plans.csv` | one row per proposed or adopted congressional map |
| `redistricting_events.csv` | one row per dated event in a plan's life |
| `candidacy_events.csv` | one row per dated change in who is running |

They are not forecasts and they are not scored. They are the **state of the
world** that a model run for date *T* is entitled to know about.

---

## Why these exist

Two problems, one shape.

**Redistricting.** The backfilled polling and academic models were run in
August 2026, on the district lines that exist in August 2026. The Supreme Court
decision and the southern redraws happened in summer 2026. So every backfilled
seat projection dated before that summer is a projection over districts that
did not exist on its own date. The margins are fine — a national two-party
margin does not care where the lines are — but the seat counts and the
chamber probabilities derived from them are counterfactual, and right now
nothing in the archive says so.

**Retirements.** Lewis-Beck & Quinn-style structural models take incumbency as
an input, and incumbency is not a property of a district, it is a property of a
district *on a date*. An open seat that opened in March 2026 was not an open
seat in February. Without dated announcements the model can only be run with
today's roster, which makes every backfilled date wrong in the same direction
as the redistricting problem.

Both are fixed by the same thing: a dated event table, and a rule that a model
run for date *T* may use only rows with `known_by <= T`. That is the same
real-time discipline `RULES.md` §10 already applies to forecasts, applied now
to inputs.

## The one rule

**Every row carries two dates and they are different things.**

- `event_date` — when the thing happened in the world.
- `known_by` — the first date a member of the public could have known it.

For a signed bill these are the same. For a court ruling handed down at 6pm
they differ by a day. For anything recovered from Wikipedia they differ by
however long it took someone to edit the page, which is why `date_basis`
records where the date came from. When the two disagree, **models use
`known_by`**, because a forecaster on date *T* could not act on a fact nobody
had published yet.

`date_basis` vocabulary:

- `primary_source` — a dated document: the bill, the order, the filing, the
  press release. Best.
- `secondary_dated` — a dated news report of it.
- `wiki_first_seen` — the first Wikipedia revision in which the fact appears.
  Cheap, machine-derivable from the revision archive we already hold, and
  usually within a day or two — but it dates the *edit*, not the event, and
  should be upgraded to `primary_source` for anything the results turn on.
- `estimated` — we are guessing. Must carry a note. Should be rare, and any
  analysis that depends on one of these has to say so.

## Failed attempts are rows, not omissions

A map that passed a legislature and was then blocked is not nothing. It is a
plan that reached `passed` and then reached `blocked`, and both dates matter:
markets moved on the first one. So `redistricting_events.csv` records the whole
life of a plan, including plans that never took effect, and `status_after`
carries the state the plan was in *after* that event.

This also keeps two questions separate that are easy to conflate:

- **Which map was legally in effect on date T?** Answered by the events table
  by construction. This is what a backfilled model should run on.
- **Which map did forecasters expect on date T?** Not answered here, and not
  knowable from a table of legal facts. Where it matters — the summer 2026
  market moves — it is a finding, not an input.

## What goes on the public site and what does not

The tables themselves are dates and citations, so they are publishable.

The baseline these point at is **the Cook PVI capture we already hold**:
`cook_pvi/2026-08-19/manual.json`, 435 rows, each carrying `pvi` and
`pvi_prior`. Because no state redrew twice this cycle, that single
before/after pair is a complete baseline for every state whose map changed,
and there is nothing further to collect.

Those values may sit in the public data and be used as model inputs — they
are Cook's own published figures — but the site does not display them as a
table. `baseline_file` names the capture rather than duplicating it.

## Conventions settled on 2026-08-24

**At-large seats are district `00`.** Checked before choosing: no at-large
House `race_id` existed anywhere in the parsed archive, so there was no
convention to match and nothing to migrate. `00` also reads as "no district
number" rather than as the first of several, which is what an at-large seat
is.

**Non-voting delegates get no `race_id`.** DC and the territories are not
among the 435, no other source in the archive has a race for them, and
minting an id would create a race nothing can ever join. The extractor still
emits the row, marked `non-voting delegate`, so the fact is not lost.

**An incumbent who moves districts is still an incumbent.** Al Green moved
from TX-09 to TX-18 under the 2025 Texas map. There is no
`switched_district` event type, because the member follows his seat: TX-18
has an incumbent running, and TX-09 becomes an open seat. This is worth
revisiting only if a candidate-quality term ever enters a model, since a
member in a redrawn seat faces a large share of voters who have never voted
for him and "incumbent" is doing less work than usual.

**A map is "in effect" when it governs the ballot that actually gets
printed.** This is the only test a seat forecast can use, because a seat
forecast is a claim about ballots. Virginia is the case that forces the
question: the referendum passed on 2026-04-21, so for seventeen days it was
in a real sense "the map" — but no map was ever drawn under it, the state
supreme court threw out the referendum result on 2026-05-08, and no ballot
was ever going to carry it. `in_effect_for_2026` is therefore `no`
throughout. What changed in those seventeen days was expectation, and
expectation is what the markets price rather than what this table records.

## Columns

### `redistricting_plans.csv`

| column | meaning |
|---|---|
| `plan_id` | stable slug, `STATE-YYYY-shortname`, e.g. `TX-2026-hb1`. Never renamed. |
| `state` | two-letter |
| `plan_name` | what it is called publicly |
| `proposer` | legislature / commission / court / other |
| `n_districts_changed` | districts whose lines moved at all |
| `est_net_seat_shift_R` | best public estimate of the partisan effect, R-positive. Estimate, not truth. |
| `shift_source` | who produced that estimate |
| `baseline_file` | filename in the private repo holding per-district PVI for this map. Empty if we do not have it yet. |
| `notes` | free text |

### `redistricting_events.csv`

| column | meaning |
|---|---|
| `plan_id` | joins to the plans table |
| `event_date` | when it happened |
| `known_by` | when it was public |
| `date_basis` | see above |
| `event_type` | `introduced` · `passed_chamber` · `passed_legislature` · `signed` · `commission_adopted` · `referendum_approved` · `referendum_rejected` · `court_filed` · `court_ruling` · `stayed` · `blocked` · `revived` · `superseded` · `final_for_2026` |
| `status_after` | `proposed` · `enacted` · `enjoined` · `dead` · `in_effect` |
| `in_effect_for_2026` | `yes` / `no` / `unknown` — the map's status *as of this event*, which is the field the backfill actually reads |
| `court_case` | case name, if any |
| `source_url` | citation |
| `notes` | free text |

Exactly one event per plan should ever carry `event_type = final_for_2026`.
That is the row that says the question is settled.

### `candidacy_events.csv`

| column | meaning |
|---|---|
| `race_id` | our id, exactly as `parsers.race_id()` mints it: `HOU_TX_21_2026`, `SEN_GA_2026`, `GOV_AZ_2026`. A bogus one does not error, it silently creates a race no other source can match. |
| `person` | name as commonly written |
| `bioguide` | Bioguide id if they have one. Empty for challengers. |
| `party` | D / R / I |
| `is_incumbent` | `yes` / `no` — incumbent of *this* seat |
| `event_type` | `retiring` · `seeking_other_office` · `resigned` · `died` · `lost_primary` · `withdrew` · `filed` · `nominated` · `unretired` |
| `event_date` | when it happened |
| `known_by` | when it was public |
| `date_basis` | see above |
| `incumbent_on_ballot_after` | `yes` / `no` — after this event, is the sitting member on the November ballot? This single column is what the incumbency term reads. |
| `source_url` | citation |
| `notes` | free text |

`lost_primary` matters as much as `retiring` and is easy to forget: a member
who is defeated in a primary stops being an incumbent on the ballot on a date,
same as one who announced a retirement, and primaries are spread across the
spring in a way that puts real structure in the timeline.

## How to fill them in

**Do not start from a blank file.** Most of this is already sitting in the
Wikipedia revision archive that `wiki_history.py` built — 499 daily Senate
revisions, 427 governor, 308 House-ratings, back to 2025-01-20. A first-seen
diff over those revisions produces a candidate row for every retirement and
every map change, dated `wiki_first_seen`, without anyone typing anything.

So the order is:

1. Machine drafts the tables from the revision archive (`date_basis =
   wiki_first_seen`), with `collect/wiki_firstseen.py`. Run `--survey` first
   to see what it is matching, tune the patterns at the top of that file, then
   `--extract`. Drafts land in `conditions/drafts/` and carry diagnostic
   columns prefixed `_`, which get deleted on the way into the real tables.
2. A human corrects them — which is a much smaller job than writing them, and
   is mostly deleting false positives and fixing dates that the edit lagged.
3. Anything the results turn on gets upgraded to `primary_source`: every
   redistricting court date, and every retirement whose wiki date is more than
   three days from the press release.

Step 3 is the only part that genuinely needs hand collection, and it is a
couple of dozen rows, not a couple of hundred.

Spreadsheet is fine for step 2 — these are CSVs with stable headers, so export
back over the file and the pipeline will not notice the difference. Keep the
column order.
