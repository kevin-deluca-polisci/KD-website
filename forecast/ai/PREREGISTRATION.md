# The AI panel — pre-registration

**Status: written before the first API call.** Nothing has been queried. Git
records when this file appeared and every change to it since, and
`ai_panel.py` refuses to run against a version of this file whose hash it does
not recognise.

The reason to write it first is the same reason `score/RULES.md` was written
in August. Every choice below — which models, what exactly they are asked,
how many draws, how the draws become one number — changes the answer, and a
choice made after the answers are in cannot be distinguished from a choice
made because of them. Fixing them now costs an afternoon and is the whole
value of the exercise later.

There is a second reason here that scoring did not have. **These answers are
perishable.** A model version is deprecated and gone; a model retrained after
November knows the result and is no longer forecasting. What GPT-5 or Claude
or Gemini says about NC-01 in August 2026 cannot be recovered in December by
any amount of effort. Everything else in this archive can be rebuilt from
stored bytes. This cannot, so it has to start before it is convenient.

---

## 1. The question being asked

Whether a large language model, asked cold, produces a forecast that is worth
anything — and how it compares to the polling, fundamentals, professional and
market categories already in the archive, scored by the same code on the same
horizons.

That is a real question with a real chance of an unflattering answer. A model
that simply parrots the expert ratings in its training data would look
accurate and be uninformative; one that has genuinely internalised political
fundamentals would be interesting. The design below is built to tell those
apart, mostly through §5 and §7.

## 2. What is asked, exactly

**One race per call.** Never a batch. A list of forty races in one prompt
invites the model to make the set internally consistent — to balance the seat
count, to anchor race five on race four — and that is a different object from
an independent judgment about one contest. One race per call is more
expensive and is the only version that means what it says.

The prompt is frozen. It is stored in `ai_panel.py` as `PROMPT_V1` and its
hash is pinned here. Any change to a single character is a **new source id**
(`ai_panel_v2`) and an amendment in §10, never an edit in place, because a
number produced under a different question is a different number.

The model is asked for:

- `prob_D` — the probability the Democratic candidate wins, as a decimal
  between 0 and 1
- `reasoning` — at most two sentences
- `confidence` — low / medium / high, in its own words

and told to answer with JSON and nothing else. A response that does not parse
is stored anyway, marked `unparseable`, and counted. Silently dropping the
answers a model fumbled would bias the record toward the races it found easy.

**No web access, no tools, no search.** Every provider is called with browsing
disabled where the API allows it, and the flag is recorded per call. A model
that can search is a slow, expensive poll aggregator, and we already have poll
aggregators. The question is what is in the model.

## 3. Models

At least three providers, so the category can clear the MIN_N=3 disclosure
floor without publishing any single model by name. Each call records:

| field | why |
|---|---|
| `provider` | Anthropic / OpenAI / Google / … |
| `model_id` | the exact version string the API returns, never a family name |
| `knowledge_cutoff` | as documented by the provider on the day of the call |
| `temperature`, `top_p`, `max_tokens`, `seed` if offered | the sampling regime |
| `web_access` | must be false; recorded, not assumed |
| `requested_at`, `latency_ms` | ordinary provenance |

`model_id` matters more than it looks. "Claude" is not a forecaster; a
specific dated snapshot is. When a provider silently upgrades an alias, the
recorded id changes and the archive shows a new forecaster appearing rather
than an old one mysteriously improving.

## 4. Sampling

**Five draws per model per race per wave**, at a fixed temperature recorded
per call. One draw is a sample from a distribution, not an opinion, and
treating it as a point estimate hides variance that is often larger than the
differences we are trying to measure.

The model's value for a race in a wave is the **median of its draws**. The
draw-level dispersion is kept and published alongside, because a model that
answers 0.55 five times and one that answers 0.2, 0.4, 0.55, 0.7, 0.9 have the
same median and are not the same forecaster.

## 5. Which races, and how often

| tier | definition | cadence |
|---|---|---|
| competitive | our own win probability in [0.05, 0.95], **or** any expert rating not "Safe" | weekly |
| everything else | the remaining House, Senate and governor races | monthly |
| all races | — | once at each scoring horizon in RULES §3 |

Race selection is computed from the archive on the day of the run and the
selected list is stored with the wave, so a later reader can see which races
were asked and when rather than inferring it from which answers exist.

**The safe races are not filler.** A model that returns 0.5 for a district
that has voted Republican by thirty points since 2010 has told us something
important about itself, and a panel that only ever asked about close races
could not detect it.

## 6. Aggregation

The category value for a race on a date is the **median across models of each
model's median draw**. Median rather than mean at both levels, because with
three or four models one provider returning 0.99 for everything would drag a
mean and cannot drag a median.

`n_sources` is the number of models that returned a parseable answer for that
race in that wave. The MIN_N=3 floor applies exactly as it does to every other
category: fewer than three gated models and the cell is suppressed.

## 7. Contamination, which is the thing most likely to ruin this

A model whose training data includes the 2026 result is not forecasting. That
becomes certain after election day and is uncertain before it, because
providers do not always publish cutoffs precisely and do not always update
them honestly.

Three defences, in order of how much they are worth:

1. **Every row carries the model's documented knowledge cutoff on the day of
   the call.** A row whose cutoff postdates the resolution date is excluded
   from real-time scoring and reported separately. This is bookkeeping, and it
   is only as good as the provider's disclosure.
2. **The archive is dated.** A forecast recorded on 2026-08-25 and committed
   to git that day cannot have been informed by a November result whatever the
   provider later claims about the model. This is the strong defence and it is
   the reason to start now rather than in October.
3. **The safe-race and calibration checks in §5 and §9** would show a model
   that is recalling rather than reasoning: near-perfect calibration on
   competitive races combined with implausible confidence is the signature.

After election day the same harness may keep running, and those answers are a
different dataset — memory, not forecast — labelled as such and never scored
as real-time.

## 8. Publication

Default tier `aggregate_only` for every provider until their terms are read
and a decision recorded in `sources/2026.yaml`: the category average may be
published, no model is named beside a number during the cycle. Raw responses
live in the private archive like every other captured byte.

Provenance is `captured`. The model produced that text on that date and we
stored what it said, which is the same relationship the archive has to a
market price or a Wikipedia revision.

## 9. Scoring

Scored by `score.py`, on the horizons in RULES §3, by the same code and with
no exemption, alongside every other category. Additionally, and specific to
this panel:

- a calibration table in ten bins, per model and for the panel
- the rate of unparseable and refused answers, per model
- median draw-level dispersion, per model
- a **safe-race sanity score**: mean absolute error on races where every
  expert rater said "Safe", which should be near zero for any forecaster worth
  reading
- agreement with the expert ratings the models were likely trained on, as a
  crude parroting check

## 10. Amendments

Every change to this file after its first commit is listed here with its date
and reason. `ai_panel.py` refuses to run against an unrecognised hash.

- *(none yet — this is the first version)*
