# Candidacy events — coding instructions

> **Start here:** open `conditions/candidacy_worklist.xlsx`, not
> `candidacy_events.csv`. The CSV is the empty *destination* — it stays empty
> until rows have been checked. The workbook already contains the 128 drafted
> rows, colour-coded: **blue** = filled by the scraper, verify it; **amber** =
> blank, code it; **grey** = the machine's evidence, read-only. Rebuild it any
> time with `python3 forecast/conditions/build_candidacy_worklist.py`.

## What this table is for

One row per **dated change in who is running** for a House or Senate seat in
2026. Two models on the site need it. Lockerbie's model takes a count of *open
seats*; Lewis-Beck & Quinlan's takes a count of *Democratic Senate
retirements*. Both counts change through the cycle as members announce, and
both are currently frozen at a single hand-typed number, which is why every
backfilled point those models produce is a flat line.

The purpose of this table is therefore not just "who retired" but **when we
could first have known**. A model run for 3 March 2026 is entitled to know
about an announcement made on 2 March and not one made on 4 March. That
distinction is the whole point of the file, and it is the thing most likely to
be coded carelessly, because it feels like pedantry until you realise the
alternative is a forecast that quietly knew the future.

The rows were drafted automatically from Wikipedia revision history. **Every
drafted value is a proposal, not an answer.** The `_`-prefixed columns at the
right-hand end are the machine's evidence for its guess; they are there so a
coder can check the guess rather than trust it, and they are dropped before the
file is used.

## Fields to code

The first six are already filled in by the scraper and need **verifying**. The
rest are blank and need **coding**.

| field | what to put in it |
|---|---|
| `race_id` | Already filled. `HOU_<ST>_<DD>_2026` or `SEN_<ST>_2026`. Check it matches the seat the person actually holds — the scraper infers this from page context and can attach a person to the wrong district. |
| `person` | Already filled. The member's name as it appears in the source. Fix obvious mangling; do not "improve" it into a formal name. |
| `event_type` | Already filled, verify. One of: `retiring` (leaving public office), `seeking_other_office` (running for governor, Senate, etc.), `lost_primary`, `withdrew`. |
| `known_by` | Already filled. The **first date this appeared on Wikipedia**. Do not change it. It is the machine's honest upper bound on when the fact became public. |
| `date_basis` | Already filled as `wiki_first_seen`. Change to `announcement` only if you also fill `event_date` from a source that gives the real date. |
| `source_url` | Already filled. Check the link actually supports the row. |
| **`event_date`** | The date the person **actually announced**, from a news report or press release, in `YYYY-MM-DD`. Leave blank if you cannot find one — a blank here is fine and honest, because `known_by` still bounds it. Never guess. |
| **`party`** | `D`, `R`, or `I`. The party of the *member*, not of the seat. |
| **`is_incumbent`** | `TRUE` if this person currently holds the seat named in `race_id`. `FALSE` if they are a challenger or a non-incumbent candidate who withdrew. |
| **`incumbent_on_ballot_after`** | **The most important field.** `FALSE` if, after this event, the sitting member is not on the November ballot for this seat — i.e. the seat is now open. `TRUE` if they are still running for it. |
| `notes` | Anything a later reader would need: contested facts, a reversal, a member who un-retired, an ambiguity you resolved and how. |

## The one rule that matters most

**`incumbent_on_ballot_after` is what the models actually read.** It is
deliberately separate from `event_type`, because the mapping between them is
not reliable:

- A `retiring` incumbent leaves the seat open → `FALSE`.
- An incumbent `seeking_other_office` leaves the seat open → `FALSE`.
- An incumbent who `lost_primary` leaves the seat open **in the sense that the
  incumbent is gone**, but the party still has a nominee → `FALSE` for our
  purposes, since the models count *incumbent not on the ballot*.
- A **non-incumbent** who `withdrew` changes nothing about the incumbent →
  `TRUE`, or leave the row out entirely if the person was never a factor.

Code the field from what is true, not from the event type. If those two ever
disagree, the field wins and the disagreement goes in `notes`.

## Judgment calls, and how to resolve them

**A member announces, then reverses.** Code both events as separate rows, each
with its own date. The models read the table in date order, so a reversal
correctly re-closes the seat from the reversal date onward.

**A member resigns mid-term.** That is a change in who holds the seat, not who
is running. Code it, mark `incumbent_on_ballot_after` `FALSE`, and say so in
`notes` — it matters for a special election.

**"Retiring to run for X."** That is `seeking_other_office`, not `retiring`.
The distinction is real: one is leaving politics, the other is a candidate
elsewhere, and only the second shows up as a name in another race.

**You cannot tell whether they are the incumbent.** Check the current member
list rather than inferring from the Wikipedia section heading. The scraper's
`_section` column often says "Retirements" for a page region that also contains
challengers.

**The date is reported as a month with no day.** Leave `event_date` blank and
let `known_by` carry it. A blank is better than the first of the month, because
the first of the month looks like a real date to anything reading the file.

## What "done" looks like

Every row has `party`, `is_incumbent` and `incumbent_on_ballot_after` filled;
`event_type` verified against the evidence columns; `event_date` filled wherever
a real announcement date could be found and blank otherwise. Save the workbook
and hand it back — the blue and amber columns get exported to
`conditions/candidacy_events.csv`, which is generated rather than edited by
hand. The `_`-prefixed columns are ignored.

There are 128 drafted rows, 115 House and 6 Senate, spanning January 2025 to
August 2026.
