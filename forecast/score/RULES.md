# Scoring rules — 2026 cycle

**Status: pre-registered.** Written and committed on 2026-08-22, seventy-three
days before the election, and before any 2026 result exists. Git records when
this file appeared and every change to it since. `score.py` prints this file's
SHA-256 into every result it produces, so a published score can always be tied
to the exact rules that produced it.

The point of writing it now is that afterwards is too late. Every choice below
— which horizon counts, what "control" means, whether unopposed races are in
the denominator — changes who comes out ahead, and a choice made after the
answer is known cannot be distinguished from a choice made because the answer
is known. Fixing them in advance costs nothing today and is the whole value of
the exercise later.

If a rule here turns out to be wrong or unworkable, it gets changed in the open:
`score.py` refuses to run against a rules file whose hash it does not recognise
unless `--rules-changed "<reason>"` is passed, and the reason is written into
the output beside the new hash. A revision is not a problem. A silent revision
is.

---

## 1. What is scored

Every source in the archive that published a scorable quantity, and every
category average the site published, are scored on the same footing and by the
same code. That includes our own class models, which get no exemption and no
special pleading.

Scoring covers all publication tiers. Per-source scores for `aggregate_only`
and `private` sources are held back until the post-election release, in line
with the standing policy on those sources; nothing about scoring changes what
may be shown during the cycle, because there is nothing to score until the
election is over.

Student forecasts are scored by this same module, on the same horizons and the
same metrics. Only the prize winner's forecast is made public, as announced.

## 2. Quantities and metrics

| Quantity | Where | Metric |
|---|---|---|
| `win_prob_D` for `NATL_HOUSE_2026`, `NATL_SENATE_2026` | national | Brier score; Brier skill score against the baselines in §6 |
| `margin_D` for `NATL_HOUSE_2026` | national | absolute error, and signed error kept separately as a bias term |
| `seats_D` for `NATL_HOUSE_2026`, `NATL_SENATE_2026` | national | absolute error, and signed error |
| `win_prob_D` per race | House districts, Senate and Governor states | Brier score averaged over races; a calibration table in ten bins; the count of races the source covered |
| Seat *distributions* where a source publishes one | Kalshi and Polymarket ladders, our own simulation | log score of the realised seat count, and continuous ranked probability score |

`rating_ordinal` is **not** scored. Turning "Lean R" into a number is a
modelling decision the site already refuses to make inside its averages, and
making it here to produce a score would be the same decision wearing a
different hat. Expert raters are scored only where they also publish a
probability or a seat count.

Signed error is reported alongside every absolute error. A family that is off
by four points in the same direction every time has a different problem from
one that is off by four points in random directions, and one number cannot say
which.

## 3. Horizons

A forecast is a claim about a date. Scoring only the last value rewards
whoever updated latest, so each source is scored at fixed distances from
election day:

**180, 120, 90, 60, 30, 14, 7 and 1 day(s) before 2026-11-03, plus `final`.**

`final` is the newest value published on or before 2026-11-02, the day before
polls open.

At each horizon a source is scored on its **newest value dated on or before
that horizon date**, provided that value is no more than **21 days** older than
the horizon. A source whose newest value is staler than that is *not scored at
that horizon* — it is neither carried forward nor penalised, and it is recorded
as absent. Twenty-one days matches the staleness tolerance the site already
uses for its movement card, and it exists so that an episodic publisher like
Fair, who posts a few times a year, is not scored on a number he had already
superseded, nor credited with an opinion he had not yet formed.

Sources that stop publishing mid-cycle are scored at every horizon they reached
and absent thereafter. Going dark is not scored as an error; it is recorded as
coverage, and the coverage table is published beside the scores. A perfect
score over two horizons is not a better record than a good score over nine, and
the table has to let a reader see that.

## 4. What counts as the truth

**Resolution date: 2027-01-06.** Certified results as they stand on that date,
by which point every state has certified and any runoff required by state law
has been held. Louisiana and Georgia can push a contest into December or
January; this date is chosen to be after that rather than to be tidy.

**Source of truth.** Certified statewide and district returns, in this order of
preference: the MEDSL official-returns release for 2026 where it exists by the
resolution date; otherwise the certifying authority of each state, captured and
archived like any other source. Whatever is used is stored in `raw/` with its
hash, so the numbers that resolved the scores are auditable rather than
asserted.

**Seats.** The party of the certified winner of the November 3 2026 general
election, including any runoff required by state law, for each of the 435 House
seats and each Senate seat on the ballot. Party is as certified. A member who
later dies, resigns, switches party, or is not seated does not change the
result of the election, and special elections after November 3 are not part of
this cycle's truth.

**House control** is Democrats holding at least 218 of 435 seats by that
definition.

**Senate control for the Democrats requires 51 seats, not 50.** The Vice
President elected in 2024 is a Republican and breaks ties through this
Congress, so a 50-50 Senate is Republican control. This matches the
`prob_D_51_plus` quantity the site already publishes, and it is stated here
because "control" is ambiguous in exactly this case and the ambiguity is worth
four percentage points of probability in some forecasts.

**The national House margin** is the two-party margin over all 435 districts:

```
margin_D = 100 × (D votes − R votes) / (D votes + R votes)
```

counted over votes as cast, including districts where a candidate was
unopposed, and excluding third-party votes from both numerator and
denominator. Aggregators differ on unopposed races, and the choice is worth
several tenths of a point, so the alternative — the same figure computed with
unopposed districts dropped from both sides — is published beside it as a
robustness check. The primary number is the one above; the alternative is
reported, never substituted after the fact.

**Races that cannot be resolved** by 2027-01-06, through litigation or an
unresolved recount, are dropped from the universe for every source alike, and
the number dropped is published. Nobody is scored on a race nobody can resolve,
and no source gets a different universe from another.

## 5. Missing values

- A source that never published a quantity is not scored on it, and this is
  recorded as coverage rather than as an error.
- A source that published a quantity at some horizons and not others is scored
  where it published.
- Race-level Brier scores are averaged over the races that source covered, and
  the count is always shown, because a source covering forty competitive
  districts and a source covering all 435 are not comparable on an average
  alone. A second figure — the Brier averaged over the intersection of races
  every scored source covered — is published for a like-for-like comparison.
- A probability of exactly 0 or 1 is scored as given. Log scores are computed
  with probabilities clipped to [0.001, 0.999], and the clipping is stated in
  the output. A source that says 0 and is wrong should take a large penalty,
  not an infinite one that makes every other number meaningless.

## 6. Baselines

Scores without a reference point are not interpretable, so the same machinery
scores four naive forecasts:

1. **Coin flip.** P(D) = 0.5 for both chambers, at every horizon. Brier 0.25.
2. **Climatology.** The base rate at which the president's party loses the House
   in postwar midterms, computed from the same 1946–2022 table the fundamentals
   model is fitted on, applied as a constant probability.
3. **No change.** The 2024 result carried forward: 220 R / 215 D in the House,
   the 2024 Senate division, and the 2024 national House two-party margin.
4. **Final polling average.** The polling category's own `final` margin, used as
   a benchmark for the seat and probability forecasts through the site's
   published seat curve.

A forecast that cannot beat "no change" has not demonstrated anything, and the
table should make that visible rather than leaving it to be inferred.

## 7. What is published, and when

After the resolution date: `derived/scores.json` and `derived/scores.csv`,
carrying every metric above, per source, per horizon, per quantity, with the
coverage table and the baselines. A `scores` page renders it. This file's hash
and commit date are shown beside it.

Nothing is published before the resolution date, because nothing exists to
publish. The coverage report in §8 is a working tool, not a result.

## 8. Coverage, which is a live tool

`score.py --coverage` answers, today, which sources will have a value at each
future horizon given what they have published so far. It is the reason to write
this module in August rather than in November: a gap it finds now is a gap
there is still time to close, and a gap discovered on election night is a
permanent hole in the archive.

## 9. Conflicts of interest, stated plainly

This archive is maintained by the same person who wrote two of the models being
scored, and the scoring code lives in the same repository as those models. The
protections are that the rules were fixed in advance, that the code path is
identical for every source, that the class models carry no exemption anywhere
in it, and that the raw archive is released afterwards so anyone can recompute
every number here from the stored bytes. That is not the same as independence
and is not offered as such.

## 10. Provenance: which rows count as a forecast

Three quarters of the per-source rows in this archive carry dates earlier than
the day daily capture began, and they are not all the same kind of thing. Every
row therefore carries a `provenance` field, and scoring depends on it.

| value | what it is | scored as real-time? |
|---|---|---|
| `captured` | fetched from the publisher that day | yes |
| `computed` | one of our models produced it that day from inputs held that day | yes |
| `archival` | recovered later from the publisher's own dated record: an exchange's candlesticks, a Wikipedia revision, Ray Fair's dated table | **yes** |
| `retrospective` | we computed it later for an earlier date, from data as it stands now | **no** |

The line between the last two is the one that matters. An exchange's
candlestick for 15 January is what the market traded at on 15 January; the
publisher committed to the number that day and we read the commitment
afterwards. Our reconstructed poll average for the same date is a 21-day mean
of a poll file **as it stands today**, computed with a window length and a
grid we chose in August 2026 with the cycle already visible. The first is
evidence about what was knowable at the time. The second is not, however
deterministic and however honestly built.

**The headline scores are computed on real-time rows only.** Retrospective rows
are scored too, reported in a clearly separated table, and used for description
and for figures — never as evidence about the timeline of knowledge.

Where a category average mixes the two, `n_retrospective` records how many of
its contributors were retrospective, and an average with any retrospective
contributor is excluded from the real-time table rather than partially counted.

For the retrospective series we publish a sensitivity: the same reconstruction
at windows of 7, 14, 21 and 28 days, so a reader can see what the specification
choice was worth.

## 11. Amendments

Every change to this file after its first commit is listed here, with its date
and reason. `score.py` refuses to run against a changed file until the new hash
is pinned, so an amendment cannot be silent.

- **2026-08-23 — provenance (§10 added).** The first version treated every row
  as equally real-time. Measuring the archive showed 75.5% of published
  per-source rows predate live capture, and that they divide into recovered
  publisher records and our own later recomputation. Scoring the second kind as
  though it were a real-time forecast would have made any timeline result
  circular. Added before any result existed.
