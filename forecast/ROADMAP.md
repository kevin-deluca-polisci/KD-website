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

### 3. Primaries identify quality cleanly, and they are already resolved

In a primary, party is constant by construction, so endorsement share is not a
proxy for partisanship. If the Ballotpedia data covers primary endorsements,
that is the cleanest identification available, and **the 2026 primaries have
already happened** -- which means the model can be fit and validated now,
against known outcomes, with the data already in hand and no scraping at all.

This is the cheapest first move by a wide margin and should be checked before
anything else is planned.

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

**Second, scrape Wikipedia regardless**, because it is independently valuable to
the endorsements project and because it supplies something Ballotpedia's dump
probably does not: **dates**. Wikipedia's revision history says when an
endorsement first appeared, and `collect/wiki_firstseen.py` already does exactly
this job for retirement announcements -- heading paths, reference stripping,
person keys, first-seen grading. The machinery transfers with modest changes.

The caution on Wikipedia is coverage. Endorsement lists exist for Senate races
and high-profile House races and essentially nowhere else, so selection into
Wikipedia is selection on notability, which is selection on competitiveness --
the salience problem from section 1, reappearing in the training set. A model
fit on Wikipedia races and applied to all 435 is extrapolating. So: Wikipedia as
a **validation** set and as the source of dates, Ballotpedia's own prior cycles
as the training set, if they can be had.

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

### 8. Licence, unresolved

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
