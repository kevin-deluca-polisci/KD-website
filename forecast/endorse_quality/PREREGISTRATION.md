# Candidate quality from endorsements — pre-registration

Written before any coefficient was estimated. The dataset assembly in
`model/endorse_quality.py --describe` had been run and its COVERAGE inspected;
no outcome regression had been fit. That distinction is the point of this file.

Companion to `ai/PREREGISTRATION.md`, and written for the same reason: a
measure that is chosen after seeing which version predicts best is not a
measure, it is a search result.

---

## 1. What is being measured

Candidate quality, as a **comparison between the two people on the
general-election ballot**. Not a property of a person in isolation.

This follows the newspaper-endorsement measure and inherits its estimand: the
quantity is a *differential*, and it is only defined within a contest.

## 2. Specification

    two-party Democratic margin
        = a
        + b1 * district partisanship          (DRA, vintage-matched)
        + b2 * cycle fixed effect             (2022 / 2024)
        + b3 * incumbency
        + b4 * endorsement share differential
        + b5 * cross-party differential

**Partisanship is DRA, not a lagged margin.** The lag was considered and
rejected: for a House seat the previous election ran on different lines
wherever the state redrew, so `margin(t-2)` is a different electorate wearing
the same district number, and it also embeds the previous incumbent's own
quality -- which is the thing being measured.

`data/DRA/` holds per-state congressional district statistics labelled by MAP
YEAR, and the composite `Dem`/`Rep` columns give a two-party partisan share
per district. The map in effect for a race is the latest vintage at or before
that race's year. Senate races take the same measure aggregated to the state,
population-weighted across its districts, so both chambers rest on one
instrument rather than two.

**DRA's license permits republication, and the repo reflects that.**
*Corrected 2026-09-10: this section previously asserted that the district
statistics "stay under `data/<cycle>/model_private/` and are never
republished." That was not true when written. 55 DRA exports were already
tracked in the public repo at `forecast/data/DRA/`, and the claim is recorded
here as wrong rather than quietly deleted, because a pre-registration that
misdescribes its own data handling is worth less than one that admits the
correction.*

The registry carries `license: permitted` for DRA and the export is computed
from public election returns, so the CSVs are committed to the public repo.
The registry's `publication: private` tier is a statement about the SITE, not
the repo: no per-district DRA figure is shown on a forecast page or written
into `derived/`, because the published object there is a category average and
a district composite is neither.

**Cook PVI is the genuinely private input** and the boundary that matters runs
around it. It stays under `data/<cycle>/model_private/`, which is why
`polling_model_house.json` is withheld -- the House model is Cook-derived and,
given the published national tide, the district numbers are exactly
recoverable: PVI = (district margin - tide) / 2.

Estimated coefficients and predicted values are this project's own output, do
not let a reader reconstruct a gated input, and may go on a public model page
when one exists. Attribution terms for any journal submission are Kevin's to
settle separately.

**The cycle term is a FIXED EFFECT, not an estimated tide.** With two cycles a
tide variable takes two values, so its "coefficient" would be a slope through
two points. A cycle dummy absorbs the same cycle-level shock and is the honest
name for what is identified.

Two endorsement differentials are pre-registered, reported together rather than
one being chosen after the fact.

**(a) Share.** The Democrat's share of the race's general-election
endorsements, minus 0.5. Within-race by construction, which is what handles
salience: a competitive open seat draws dozens of endorsements and a safe seat
draws three, so a raw COUNT would predict through attention rather than through
quality. Share is the defensible specification, not the fallback.

**(b) Cross-party count.** The Democrat's count of endorsements from endorsers
whose usual side is Republican, minus the Republican's mirror count. Wikipedia
annotates these by hand.

Prediction: **(b) carries signal that (a) does not**, because an endorsement
from a group that usually backs the other side is evidence about the candidate,
whereas an ordinary endorsement is mostly evidence about the party. If (a) is
significant and (b) is null, the measure is picking up partisanship and the DRA
term should absorb it.

## 2a. `endorser_party` is annotated, not recorded — measured 2026-09-07

This section was written after a measurement and BEFORE the fix it calls for,
so the prediction in it is on the record rather than fitted afterwards.

**The finding.** `endorser_party` is not missing at random. Wikipedia editors
record an endorser's party when it is NOTEWORTHY, and what makes it noteworthy
is that the endorsement crosses party lines. Within congressional endorsers --
the same people, the same categories, general phase, 2022 and 2024:

    party annotated by Wikipedia      29 cross of  29   = 100%
    party filled from the roster      25 cross of 495   =   5%

Every row an editor annotated was a defection. None was a Republican senator
endorsing a Republican. An annotated row is roughly twenty times likelier to be
cross-party than a representatively filled one.

**What follows.** The pre-fill `cross_party` flag did not measure defection. It
measured "defection OR an editor thought this worth flagging", and on this data
those are nearly the same event. The 24 races that showed a non-zero
differential before the roster fill were 24 races where somebody had annotated;
filling 529 congressional endorsers representatively moved that to 22, because
the added rows are 95% same-party. The fill did not fail. It showed the earlier
number was inflated.

The 67% cross-party rate among state and local officials is the same artifact
at a different magnitude, not a behavioural difference between office levels.
Those rows are still almost entirely editor-annotated.

**Consequence for the specification.** Measure (b) MUST NOT be fit on the data
as it stands; a coefficient on it would be an estimate of annotation
propensity. It is fit only once the party-bearing endorser population is filled
representatively rather than selectively.

**Registered prediction, before the infobox fill is written.** Filling the
remaining 1,651 party-bearing rows from linked Wikipedia articles will:

  1. move the state and local cross-party rate DOWN from 67% into single or low
     double digits, toward the 5% the congressional roster shows;
  2. raise the count of races with a non-zero cross-party differential above 22,
     but modestly -- to somewhere in the 30s, not to most of the sample;
  3. leave the D/R asymmetry (103 R-to-D against 21 D-to-R before the fill)
     substantially reduced, because that asymmetry is itself partly annotation
     bias rather than a fact about 2022 and 2024.

If (1) fails and state and local officials really do defect at 60%+ when filled
representatively, that is a finding about office level and party discipline and
it is reported as one, not quietly folded into a control.

**Measure (a), the share, is unaffected.** It is built from endorsement counts
and never touches `endorser_party`.

**The genuine congressional cross-party rate is 5%** (25 of 495). That is a
quantity this project now has and nobody had before, and it is reported
regardless of what the model does.

## 2b. Outcome of the 2a predictions — measured after the fill

The Wikipedia-category fill ran on 2026-09-07, taking the party-bearing
endorser population from 257 known rows (11%) to 1,236 (51%). The three
predictions in 2a were written before it. Scored honestly:

**Prediction 1 — CONFIRMED, and more sharply than stated.** Split by how the
party was obtained, in the 2022 and 2024 general phase:

    congressional, roster-filled        38 cross of 511   =  7%
    non-congressional, annotated        95 cross of  95   = 100%
    non-congressional, wikipedia-filled  7 cross of 425   =  2%

The 67% figure was entirely selection. Filled representatively, state and local
officials cross party lines at 2% -- LOWER than members of Congress, not
higher. The prediction said "single or low double digits, toward the 5% the
congressional roster shows"; the answer is 2%.

**Prediction 2 — WRONG.** Races with a non-zero cross-party differential were
predicted to rise above 22 "into the 30s". They went to 21. Coverage more than
quadrupled and the count did not move.

The reason is prediction 1: the newly filled rows are 98% same-party, so they
add to the denominator and almost nothing to the numerator. The races that
still show a differential are substantially the ones that were annotated, which
is the selected set 2a warned about. More data did not dilute the selection; it
revealed how little signal sits outside it.

**Prediction 3 — essentially unchanged, so counted as not confirmed.** The
asymmetry went from 103 R-to-D against 21 D-to-R, to 110 against 30. The ratio
moved from 4.9x to 3.7x, which is not the substantial reduction predicted, and
what remains is still dominated by annotated rows.

### What this does to measure (b)

**Cross-party endorsement is genuinely rare, and now measured rather than
inferred: about 7% of congressional endorsements and 2% of state and local
ones.** That is a quantity this project has and nobody else does, and it is
reported whatever the model does.

But at 75 races it is near-constant. 54 of 75 races sit at exactly zero, the
median race has 3 known-party endorsers, and the non-zero races remain
concentrated in the annotated subset. **Measure (b) is therefore demoted from a
pre-registered regressor to a DESCRIPTIVE quantity.** Fitting a coefficient on
a variable that is zero in 72% of the sample, and non-zero mostly where an
editor chose to annotate, would estimate the annotation process a second time
in a subtler disguise.

This is the outcome 2a registered as the risk -- "a null on it will be
difficult to distinguish from no power" -- and it is recorded as a measurement
result rather than quietly dropped.

**Measure (a), the share, is unaffected and remains the primary regressor.** It
never touches `endorser_party`.

**The fit therefore proceeds with (a), the DRA baseline, the cycle effect and
incumbency.** (b) is reported as a descriptive table beside it, not estimated.

## 2c. Measure (c): primary consensus — registered 2026-09-07, before fitting

A NEW term, added at Kevin's suggestion, and a DIFFERENT construct from the one
section 5 rules out. Section 5 excludes folding primary endorsements into the
nominee's general-election share, because that compares candidates who were
never on the same ballot. This does not do that. It asks a separate question:

> did a party nominate a consensus candidate, or one who emerged from a split
> field, and does that predict the general-election margin?

The estimand is the NOMINATION, not the nominee comparison. Primary and general
endorsements stay in separate terms and are never summed.

**Measure.** For each party, the eventual nominee's share of that party's own
primary-phase endorsements; then the differential, D minus R. Plus an indicator
for whether either primary was contested (more than one endorsed candidate), so
that a race where consensus is 1.0 because nobody else was endorsed does not
enter as though the nominee had consolidated a field.

**Where the data came from.** `candidate_party` is filled on 2% of primary
rows. The party is in the section heading instead -- "Republican primary >
Endorsements" -- and deriving it there takes usable primary rows from 177 to
14,214. The same heading carries the district for House races. This is
structural, not inferred, and it is the reason a term nobody could build last
week is buildable now.

**Coverage, measured before any fit:**

    races with a consensus differential          49 of 75
      at least one primary contested             39
      exactly zero differential                  10 of 49
    differential  min -0.52  max +1.00  sd 0.34
    nominee share of own primary endorsements: D median 1.00, R median 0.64

Compare measure (b), which was exactly zero in 54 of 75. This variable has
variance where that one had none.

**Collinearity, stated in advance.** Correlation with incumbency is +0.52. An
incumbent seeking renomination collects near-unanimous primary endorsements
close to by definition: mean differential is +0.34 where a Democrat holds the
seat, +0.20 open, -0.16 where a Republican holds. A VIF near 1.4 is workable,
but the two terms trade precision and the incumbency-dropped robustness check
matters more here than anywhere else.

**Identification worry, registered rather than discovered.** Consensus may run
backwards. A party that expects to win consolidates early, so "consensus
predicts the margin" could be "expecting a good year produces consensus". This
is the salience problem of section 2 in different clothes, and this design does
not solve it -- it is a within-race differential, which controls for how much
attention the RACE draws but not for which SIDE expected to win it. Any positive
coefficient is reported with that caveat attached and is not called an effect.

**Prediction.** Measure (c) carries more signal than measure (b), because it
has variance and (b) does not. Direction positive: the party whose nominee
consolidated its primary does better in November. Magnitude uncertain, and a
substantial part of whatever appears will be incumbency working through the
term rather than around it -- which the incumbency-dropped check will show.

## 3. What is NOT being estimated, and why

**Group ideal points are out of scope for this fit.** Pooling the general-phase
rows of 2022, 2024 and 2026 gives 2,458 distinct endorsers, of whom:

| appears in | endorsers | share |
|---|---|---|
| ≥2 races | 540 | 22% |
| ≥3 races | 292 | 12% |
| ≥5 races | 149 | 6% |
| ≥10 races | 65 | 3% |

Seventy-eight percent appear exactly once. An ideal point estimated from one
observation is not an estimate. The cross-party count in (b) is the
approximation used instead: it is a defection count, it needs only the cycle in
front of it, and it requires no latent parameter per endorser.

The 65 endorsers appearing in ten or more races ARE separately estimable, and a
later version may give those a lean and route everyone else through party. That
is a second study, and this file does not license reporting it as if it had
been planned here.

## 4. Sample

**75 races.** Assembled as: general-phase endorsements from the 2022 and 2024
cycles, at least one endorsed Democrat and one endorsed Republican, joined to a
race-level two-party margin in MIT's returns. Reproduce with
`endorse_quality.py --describe`, which reports coverage and deliberately
nothing about the outcome.

    races with >=1 endorsed D and >=1 endorsed R   108
      senate                                        43
      house                                         35
      governor                                      30
    joined to a two-party margin                    75
      lost: governor (no gubernatorial returns)     30
      lost: senate (gaps in MEDSL)                   3
    by cycle                        2022: 49   2024: 26
    incumbency          open: 28   D holds: 26   R holds: 21
    endorsements per race     min 3   median 15   max 153

Three limits, stated here rather than discovered in the results.

**The 30 gubernatorial races are lost to a missing file, not a design choice.**
`derived/returns.csv` carries House and Senate only. MEDSL publishes
gubernatorial returns separately; adding them takes the sample from 75 to
roughly 105, a 40% increase, and is the cheapest available improvement.

**The cross-party differential is zero in 51 of the 75 races.** Only 24 have a
non-zero value, and the total absolute differential across the whole sample is
53 endorsements. Measure (b) is therefore sparse as well as small, and a null
on it will be difficult to distinguish from no power. That is registered now so
that a null is not later read as a finding, and it is the second argument for
enlarging the sample before drawing conclusions from (b).

**The endorsement count ranges from 3 to 153 per race, median 15.** This is the
salience problem measured rather than asserted, and it is why the share in (a)
is the pre-registered form and a raw count is not a candidate specification at
all.

**DRA partisanship joins 66 of the 75 races.** The nine gaps are states whose
only DRA file in the archive is a LATER map than the race -- Alabama, Georgia,
New York, North Carolina, South Dakota and Vermont for 2022 Senate, plus Alaska
(at-large, no file at all) and NY-1. Downloading those states' earlier maps
closes it; until then the fit runs on 66 and the omission is by state, which is
not random and is reported with the result.

**75 races is a small n for two regressors plus controls.** This is a
description of a relationship, not a validated forecasting input. Nothing from
this model goes on the public site or into the seat model on this sample.

## 5. Inclusion and exclusion rules, fixed here

- **General phase only.** `phase == "general"`. Primary and runoff rows are
  dropped. A primary tells you who the strongest candidate was among a
  DIFFERENT set of people; the general-election differential compares the two
  who actually appear in November. A nominee who survived a six-way primary and
  one who was unopposed have incomparable primary records.
- **Robustness check, reported either way:** the same fit including
  primary-phase endorsements in each nominee's total. Reported, never
  substituted.
- Duplicate rows (`duplicate_key`) dropped.
- Candidate party must be D or R. Third-party candidates are not modelled.
- Where a party has more than one endorsed candidate in the general phase — an
  unresolved primary reflected in the article — the most-endorsed candidate of
  that party is taken.
- Special elections excluded.
- **Incumbency is DERIVED, and the derivation was validated before use.**
  No source in this repo carries incumbency for 2022 or 2024. A candidate is
  coded incumbent when they match the winner of the previous election for the
  same seat: year minus 6 for the Senate, year minus 2 for the House.

  Names are matched on SURNAME ONLY, derived from the candidate string rather
  than read from MEDSL's `surname_key`. That column is not trustworthy: it is
  built as though every row were "FIRST LAST", but a minority are
  "LAST, FIRST", so 2022 Hawaii carries `SCHATZ, BRIAN -> "brian s"` and 2022
  Nevada `CORTEZ MASTO, CATHERINE -> "catherine c"`. It is 38 rows in 2022 and
  20 in 2024 out of about 1,350 each, and none in 2020 -- small, silent, and
  capable of coding a seat OPEN with a sitting senator on the ballot.

  Requiring the first initial to agree was tried and is WORSE, because legal
  first names and used names differ often enough to matter at this n:

      MD-2   "Dutch Ruppersberger"    vs  C.A. Ruppersberger
      VA-5   "Bob Good"               vs  Robert Good
      PA-Sen "Bob Casey Jr."          suffix; last token is "jr"
      OR-5   "Lori Chavez-DeRemer"    compound; MEDSL keeps "deremer"

  All four are long-sitting incumbents and all four were coded OPEN by an
  initial-sensitive or last-token match. The initial is therefore used only to
  break a tie between two same-surname candidates in one race, a case that
  occurs zero times across all 936 House and Senate races in 2022 and 2024.

  **Validation.** Against an independent check -- is the previous winner on the
  current ballot at all, per the returns themselves -- the coding gives 0 false
  negatives and 0 false positives across the 75 races. 49 of 75 (65%) have an
  incumbent running.

  One failure mode survives and is not detectable here: a senator who reached
  the seat by appointment or succession has never won it, so a
  previous-winner derivation cannot see them. `conditions/build_senate_incumbency.py`
  documents the same limit for 2026 and names Sasse and Inhofe. If the fit is
  ever extended to seats that changed hands mid-term, incumbency needs an
  external roster -- Voteview's member file, keyed on ICPSR rather than on
  names, is the standard answer.

  **Robustness check, reported either way:** the same specification with the
  incumbency term dropped. Incumbency is a control here, not the estimand, so
  if the endorsement coefficients are stable across its removal, residual
  mis-coding is not driving the result.
- `still_present` is not required for settled cycles: an endorsement withdrawn
  from an article after the election still happened.

## 6. Stopping rules

- The specification in §2 is fit once. If it is refit, this file records why.
- No measure is dropped for being null. A null on (b) is the informative case.
- Coefficients are not to be inspected before this file is committed.
