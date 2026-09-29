# National model: versions already tried

If the question of adding consumer sentiment (or other economic terms) to the
class model's national equation comes up again, start here. These were tested
on September 29, 2026. See also the course memo
`2026_assignments/planning/PLSC2219_sentiment_investigation.md` (September 7,
2026), which reached the same conclusion on sentiment.

**Decision (Sept 29, 2026):** keep the class model as it is (midterm, income,
gas, approval). No sentiment version on the site.

## Setup

Outcome: change in the president's party's two-party House vote share. 35
elections, 1956-2024 (1954 excluded), from `inputs/elections_pooled.csv`.
Economy terms are calendar-year values from `inputs/economy_sept.csv`.
"LOO" is the leave-one-election-out prediction error (root mean square);
"LOO midterms" is the mean absolute leave-one-out error on the 17 midterms only.
The 2026 column is the national Democratic share with approval 37.1% and the
one-year economy rule (income +0.39%, gas about +17%; gas and sentiment
approximate).

| Version | Economy term (se) | Adj R² | LOO | LOO midterms | D share 2026 |
|---|---|---|---|---|---|
| midterm + approval | none | .685 | 2.05 | 1.72 | 54.2 |
| + gas | gas_1yr -0.045 (0.024) | .709 | 2.01 | 1.81 | 54.5 |
| + income | income_1yr (t < 1) | .676 | 2.20 | 1.87 | 54.2 |
| class model (+ income + gas) | income -0.18 (0.19), gas -0.060 (0.028) | .708 | 2.10 | 1.97 | 54.5 |
| sentiment level replaces income | sentiment -0.038 (0.034) | .711 | 2.02 | | 53.7 |
| sentiment change replaces income | -0.057 (0.041) | .717 | 2.00 | | 54.2 |

## What we concluded

- Consumer sentiment adds nothing once midterm and approval are in: the sign is
  wrong (higher sentiment, worse for the president's party) and it is not
  significant. The 2026 value (about 52) is also below every year in the fit
  (59 to 108), so any sentiment version is extrapolating.
- Income makes out-of-sample error worse. It is in the class model only because
  the course built it that way.
- Gas is significant in the class model as fit (-0.060, se 0.028, t -2.11,
  p = .04) and keeps its sign when any single election or the oil-shock years
  are dropped. Without income it is -0.045 (t -1.88, p = .07). The effect comes
  from presidential years; in midterms alone it is about zero (see below), and
  it does not improve the midterm-only out-of-sample error.
- Size of the gas term in 2026: about +0.7 (one-year rule) to +1.1 (2026 to
  date) points of D share relative to an average midterm's gas change. Approval
  (37% against a midterm average of 51%) is worth about +1.6. The midterm
  penalty itself is the largest piece (the average midterm alone puts the D
  share near 52.4). Comparing models with and without gas shows only a 0.3
  point gap because the refit intercept and approval slope absorb part of it.
- Baseline: midterm + approval for the national vote, then PVI + incumbency +
  the national vote for each race. A proposed addition should beat that
  baseline out of sample (leave one election out nationally; leave one cycle
  out for races), not only in sample.

## Is the midterm dummy fair? Does pooling hold? (also Sept 29)

The dummy is known before the election, so it is a legitimate predictor, and
dropping it wrecks the fit (adj R² .136, LOO 3.36). The real question is
whether slopes estimated partly from presidential years apply to midterms.

| Version | Approval | Gas | Adj R² | LOO | D share 2026 |
|---|---|---|---|---|---|
| pooled, midterm + approval + gas | 0.110 (0.031) | -0.045 (0.024) | .709 | 2.01 | 54.5 |
| midterms only (n = 17), approval + gas | 0.162 (0.064) | -0.009 (0.041) | .313 | 2.55 | 54.8 |
| pooled, slopes allowed to differ by type | midterm slope 0.162 | midterm slope -0.009 | .711 | 2.10 | 54.8 |

Separate slopes are not significantly better (F test p = .34), but the gas
effect comes from presidential years; in midterms it is about zero. Approval
matters at least as much in midterms as in presidential years.

## Where the code is

`national_variants.R` fits the versions above and applies them to each date's
inputs (`output/class_national_variants*.csv`). It runs daily in the capture
workflow and is not shown on the site.
