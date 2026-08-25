# What is not built yet

Ordered by urgency, not by size. Everything here is a decision that has been
thought about and deferred, not a thing nobody has noticed. Where a choice has
already been made it is written down as made, so that a later version of this
project does not relitigate it from scratch.

---

## Now

**The redistricting page.** Blocked only on district partisanship from Dave's
Redistricting. The dated-baseline machinery (`model/maps.py`,
`conditions/redistricting_effective.csv`) is built, self-tested and deployed;
what is missing is a public-domain district index to draw, because the one we
hold is Cook's and Cook's cannot be published.

**The AI panel.** Pre-registered (`ai/PREREGISTRATION.md`), harness written and
dry-run. Blocked on API keys, a `sources/2026.yaml` entry, a per-provider
publication tier, and one open question: whether "Likely" counts as competitive
(121 races if not, 197 if so).

Both are perishable in the same way, and that is why they are first. A model
version is deprecated and gone; a redistricting page is worth most while the
maps are still being argued about.

---

## Next

**Candidate quality from endorsements.** The long entry below.

**Per-race pages.** A page per contest: every method's number, the rating
history, the district's baseline, and what changed when. The data all exists;
this is templating and URL design.

**A decision log.** This file is half of one. The other half is the running
record of judgment calls that currently lives in code comments and in
`methods.html`'s table.

**HYPOTHESES.md.** What we expect to find, written before the results, for the
same reason `RULES.md` and the AI pre-registration were.

---

## Candidate quality from endorsements

**What we have.** Ballotpedia has shared a large dataset of 2026 candidate
endorsements, mostly from interest groups, and asked that other academics be
told it exists. Kevin has a candidate-quality estimation model built on
newspaper endorsements. The idea is to estimate quality from the endorsement
data and let it into the forecast.

**What is actually hard about it**, in the order that matters.

### 1. Endorsement counts measure attention, not quality

The number of endorsements a candidate holds is overwhelmingly a function of
how much attention the race gets. A competitive open House seat draws dozens of
interest-group endorsements; a safe seat draws three. Salience correlates with
competitiveness, and competitiveness correlates with the outcome, so a raw
count will look predictive while predicting through a channel that has nothing
to do with the candidate.

That failure is not detectable by fit. It produces *better* fit, which is the
problem.

The fix is that quality has to be identified **within** a race, never across
races. This makes the "simpler version" -- share of endorsements in a race --
the more defensible specification rather than the fallback one. Worth saying
plainly, because it inverts the intuition that the simple version is the
compromise.

### 2. Share-within-race is mostly partisanship

Having normalised out salience, the next problem: endorsers are not
exchangeable. An AFL-CIO endorsement and a Chamber of Commerce endorsement are
each "one endorsement," and each is close to automatic given the candidate's
party. Share of endorsements therefore mostly measures which groups showed up,
which is partisanship again, which the model already has from PVI.

The way out has the same structure as the newspaper model, and this is where
that model genuinely transfers rather than transferring by analogy:

- Estimate each **group's own lean** from its endorsement history: a group
  ideal point, the same object a newspaper's lean is.
- Candidate quality is then the **residual** -- endorsements won from groups
  that do not usually endorse that party.

A cheaper version of the same idea, usable before any ideal-point estimation:
count **cross-pressured endorsements**, where a group's modal endorsement in
other races that cycle went the other way. That is a defection count, it needs
only the 2026 data, and it is already more informative than a share.

### 3. Primaries are OUT, and the reason is the estimand

An earlier version of this section argued that primaries were the cleanest
identification available, because party is constant in a primary so endorsement
share cannot be a restatement of partisanship. That argument is correct about
primaries and wrong about what we are trying to measure. Kevin's objection,
recorded because it settles the design:

> A primary tells you who the high quality candidate is IN THE PRIMARY, but not
> in the general. The key thing the model picks up is who gets endorsed in the
> general, after the candidates in the primary are decided. It's the same as my
> newspaper endorsement measure -- it only measures quality DIFFERENTIALS
> between candidates. So a primary has different candidates, and it might not
> tell you about the quality differences in the general election.

Candidate quality here is not a property of a person in isolation. It is a
comparison between the two people actually on the general-election ballot, and
a primary compares a different set. A candidate who dominated a six-way primary
and one who was unopposed have incomparable primary records and may be
identically matched in November.

So: `stage == pri` rows are dropped in `collect/mit_returns.py`, and primary
contests are not modelled. This also happens to remove a data problem -- MEDSL
carries essentially no primary returns anyway (60 rows in fifty years of House
elections) -- but the reason is the estimand, not the availability. Had the
returns existed we would still not use them.

### 3a. The specification, stated

    general-election two-party vote share
        ~ partisanship (the district or state baseline)
        + endorsement share differential between the two nominees
        + incumbency
        + controls

Endorsement share is within-race by construction, which is what handles the
salience problem in section 1. Partisanship is what the coefficient has to be
adjusted for, and is the entire reason the DRA baseline work matters here as
well as for the seat model.

**Cross-party endorsements are expected to carry most of the signal**, for the
same reason they do in the newspaper model: an endorsement from a group or a
politician whose usual side is the other one is evidence about the candidate
rather than about the party. Wikipedia annotates these by hand -- 65 in the
2026 sweep, 50 in 2024 -- which is a small enough n to be a validation set
rather than a regressor, so the group-lean residual still has to do the work at
scale.

**Open question for the pre-registration**, not settled here: whether an
endorsement a nominee collected DURING their primary counts toward their
general-election share. Including them is more data and imports the contested
primary's salience; excluding them is cleaner and discards a lot. Decide before
seeing a coefficient, and report the other as a robustness check.

### 4. Training data: ask before scraping

The stated problem is that we hold only 2026, so there is nothing to fit on.
Two candidate sources, and the recommendation is to pursue both in a specific
order.

**First, email Ballotpedia and ask for 2020, 2022 and 2024 in the same format.**
They shared 2026 unprompted and asked for the word to be spread, so they are
plainly disposed to help. Same instrument matters enormously here: a model fit
on one measurement process and applied to another is exactly where this kind of
thing breaks quietly. A ten-minute email plausibly saves a month of scraping,
and one cycle of training data is a description rather than a model, so two or
three prior cycles is the real requirement.

**Second, scrape Wikipedia**, and this is now built and measured rather than
proposed. `collect/wiki_endorsements.py` extracts endorsements from race
articles; `--self-test` covers it, `--audit` reports on a sweep.

A full 2026 pass on 2026-08-25 returned **11,234 rows from 121 pages**, and the
numbers below are what the pre-registration has to be written against. They
replace the guess this section previously carried, which was that Wikipedia
covered Senate races and marquee House races and nothing else. That was wrong.

| | measured |
|---|---|
| contests with any endorsement | 322 of ~435 House, 32 of ~35 Senate, 36 of 36 governor |
| distinct endorsers | 4,637 |
| endorsers in >=2 contests | 1,237 (27%) |
| endorsers in >=5 contests | 319 (7%) |
| contests with >=2 endorsed candidates | 279 of 390 (72%) |
| rows carrying a usable citation date | 69% |
| wikilinked (so entity-keyed) | 98% |

**Four findings that decide the specification.**

1. **The group-lean residual is identified, but only for organisations.** The
   endorsers who recur are national groups -- AIPAC in 173 contests, Planned
   Parenthood in 132, the AFL-CIO in 112, the League of Conservation Voters in
   90. Their lean is estimable. Individual politicians overwhelmingly appear
   once, usually endorsing in their own state, so their lean is not estimable
   the same way and must come from party instead. Two mechanisms, one model,
   and the pre-registration has to say which applies to whom.

2. **Salience is confirmed and quantified.** Median 12 endorsements per
   contest, p90 of 65, maximum 417 (California governor). Counts are
   unusable. Share-within-race is the only defensible form, and this is now a
   measurement rather than an argument.

3. **113 House districts have no endorsements at all**, and they are the safe
   seats -- missing, and missing not at random. They cannot be dropped, since
   that is a competitive-races-only model presented as a full one, and they
   cannot be imputed zero, since no data is not no quality. The decision to
   record in advance: candidate quality enters as an adjustment only where data
   exists, with the untouched districts stated explicitly.

4. **Arrival is roughly flat, not clustered.** Roughly 575-750 dated
   endorsements a month through 2026. The worry that a share-to-date is
   incomparable across dates is weaker than it looked -- though this is the
   stock of dates on pages today, not the flow of when entries appeared, so
   weekly capture is what will actually answer it. One real exception: the
   California governor's race has been accumulating endorsements since early
   2023, so its runway is three years where a Senate race that opened last
   autumn has one. Any time normalisation has to handle that.

**A bonus the design did not anticipate.** Wikipedia editors annotate
cross-party endorsements by hand, in a consistent italic parenthetical -- a
Democrat endorsing in a Republican primary is written as such on the page. 65
of them in the 2026 sweep. Too few to fit on, but they are a free validation
set for whatever cross-pressure measure gets built, and for individual
endorsers they supply directly what the group-lean step has to estimate.

The remaining caution is depth, not breadth. Coverage is broad; how MUCH is
listed still tracks how interesting the race is, which is finding 2.

### 5. The dating problem decides what this can ever be

This archive's whole discipline is that a number carries the date on which it
was knowable. A dump of 2026 endorsements as of today is a snapshot with no
dates in it. Feeding it to the model would give every backfilled projection
endorsements that had not happened yet, which under `score/RULES.md` section 10
makes the result `retrospective` and inadmissible as real-time evidence.

Two honest options, and the choice determines the whole shape of the feature:

- **Undated.** Candidate quality is an *evaluation covariate*. It explains
  errors after the fact and appears in the post-mortem. Useful, publishable, not
  a forecast input.
- **Dated.** Candidate quality becomes a real-time model input. This requires
  endorsement dates, which means Wikipedia revision history, or Ballotpedia
  supplying timestamps if asked.

Ask for timestamps in the same email as section 4. It is the difference between
a covariate and a feature.

### 6. What the page should say

Not "here is our candidate quality score." A page worth building shows:

- the distribution of scores, and which races the extremes are in;
- the cross-pressure cases by name -- candidates endorsed by groups that
  usually go the other way, which is the interesting output regardless of
  whether the score predicts anything;
- how much the score moves the forecast, as its own panel;
- an explicit **does this add anything over PVI and incumbency** test.

If the answer to the last one is no, the page says no. A page that reports a
null is more interesting than one that asserts a success, and this project has
already committed to that standard elsewhere.

### 7. What is time-sensitive about it, despite not being urgent

The modelling can wait. The **capture cannot**, for the same reason the AI panel
could not: interest-group endorsement pages get updated in place, and
Ballotpedia's live pages will eventually show the final state rather than the
September state. Two things are worth doing now even though the rest waits.

1. Snapshot the delivered 2026 dataset into the private archive with a hash, as
   a `captured` artefact dated the day it was received.
2. Start dated Wikipedia capture of endorsement sections, so the revision
   history is read forward rather than reconstructed later.

Everything else here can happen in December.

### 8. It is Kevin's own forecast, so it needs its own pre-registration

Decided 2026-08-24: this becomes a model in the archive, not a captured source.
That means a source id, a registry entry, a place beside polling and
fundamentals in the class models, and scoring by `score.py` on the same
horizons with no exemption.

**Name.** `endorsement_quality`, displayed as *endorsement-based candidate
quality*. Not "candidate quality model" flat: the model measures endorsements
and INFERS quality, and whether that inference works is the thing being tested.
A name that asserts the conclusion is a name that will read badly if the
answer is no. It also keeps it distinct from the newspaper-endorsements
project it borrows its structure from.

**A section in the pre-registration report, alongside the other evaluations.**
More necessary here than for the AI panel, not less: this is Kevin's own model
in Kevin's own research area, which is exactly where "you chose that
specification after seeing the numbers" is most damaging and least answerable.
Written before the first estimate, the choices below cost an afternoon; written
after, they cannot be distinguished from choices made because of the answers.

What has to be fixed in advance:

- the specification -- raw share, cross-pressure count, or the group-lean
  residual of section 2, and if more than one, which is *the* forecast
- which endorser categories count, and whether `newspaper` is in or out given
  that it overlaps Kevin's other data
- how `declined` and `withdrawn` rows are treated: dropped, negative weight, or
  a separate covariate
- incumbency and open-seat controls
- **the time-normalisation.** Endorsements do not arrive uniformly. They
  cluster after primaries, after debates, and in the last three weeks, so a
  candidate at 60% share in June and 60% in October are not the same object.
  Normalise within date, or model the arrival process; decide which now.
- the horizon at which the model starts, which is the day capture starts
- **the null**: does the seat model with candidate quality beat the same model
  without it, on the same horizons. Pre-register that this is the headline
  result and that a null gets published as a null.

### 9. Licence, unresolved

Ballotpedia's data comes with terms that have not been read yet, and the default
is the project default: `private` until a decision is recorded in
`sources/2026.yaml`. Wikipedia is CC BY-SA, which is redistributable with
attribution and share-alike, so the derived endorsement table can be published
with attribution. Worth confirming before it becomes load-bearing.

---

## After the election

Tag and freeze the archive, mint a DOI, flip the private repo. Permission
requests to 50+1, DDHQ, The Economist and Split Ticket for republication of
their series, which have been deliberately not asked during the cycle.
