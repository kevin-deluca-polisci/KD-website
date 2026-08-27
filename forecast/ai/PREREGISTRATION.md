# The AI panel — pre-registration

**Status: written before the first API call. Nothing has been queried.** Git
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
November knows the result and is no longer forecasting. What a model says
about NC-01 in August 2026 cannot be recovered in December by any amount of
effort. Everything else in this archive can be rebuilt from stored bytes.
This cannot, so it has to start before it is convenient.

---

## 0. Two arms, and why there have to be two

The first version of this document forbade web access. The panel now runs
**two arms**, because the ban answered one good question and made another
impossible.

| arm | source id | web | cadence | the question it answers |
|---|---|---|---|---|
| **cold** | `ai_panel_cold` | off | once, **early** (§5c), plus scoring horizons | What is latent in the model? |
| **search** | `ai_panel_search` | on | weekly through the **final six weeks** (§5a, §5c) | What is a person actually told when they ask? |

The two arms run on **different clocks**, and §5c is where that is argued.
Briefly: the cold arm measures something that decays — a model's latent prior
about a race is contaminated a little more by every week of campaign coverage
that reaches a training set — so it is worth running early and it is cheap
enough to run early. The search arm measures what a person is told when they
ask, and the people this is about ask late.

**The cold arm is the original question** and it is cheap: no search results
in context, so input is a few hundred tokens rather than ten thousand. It is
asked once because a model with no web access **cannot update** — nothing
changes between Tuesday and Wednesday except sampling noise, and repeated
cold waves would buy a variance estimate obtainable more cheaply with more
draws.

**The search arm is what a normal person experiences**, and it is the arm in
which the media question lives (§6). It is also, unavoidably, *downstream of
the polling category*: a model reading a poll aggregate and reporting a
number near it has not independently forecast anything. That has two
consequences recorded here so nobody has to rediscover them:

1. The search arm **must never be folded into a composite average alongside
   the polling category**, because the aggregators would be counted twice.
2. The **tide inversion in §8 uses the cold arm** when both are available.

Running only the search arm would leave the archive unable to say whether a
model has internalised anything about American politics or is simply a slow,
expensive poll aggregator. Running only the cold arm would leave it unable to
say anything about how AI mediates political information, which is the more
distinctive contribution. Hence both.

## 1. The question being asked

Whether a large language model produces a forecast worth anything, and how it
compares to the polling, fundamentals, professional and market categories
already in the archive, scored by the same code on the same horizons.

Second, and specific to the search arm: **which sources a model consults when
asked about American elections**, and in particular the balance between
national outlets, local outlets, partisan outlets, and material of doubtful
provenance. This is a media-agenda question and the citation record (§6) is
its dataset.

Both have a real chance of an unflattering answer. A model that parrots the
expert ratings in its training data would look accurate and be uninformative;
one that reads three national outlets and nothing else would be telling us
something important about the information environment it creates.

## 2. What is asked, exactly

**One race per call, and one candidate per call.** Never a batch. A list of
forty races in one prompt invites the model to make the set internally
consistent — to balance the seat count, to anchor race five on race four —
and that is a different object from an independent judgment about one
contest.

**Everything about a single subject goes in ONE call.** With search enabled
the cost is dominated by searches and by injected results, not by output
tokens, so asking for six measures in one response costs a few percent more
than asking for one, while asking them in six calls costs six times as much
and produces six unrelated citation sets. Bundling is therefore both cheaper
and better: every measure about a candidate rests on the same evidence, and
the citations attach to a single act of retrieval.

Prompts are frozen and hash-pinned in `ai_panel.py`. Any change to a single
character is a **new source id** and an amendment in §11, never an edit in
place, because a number produced under a different question is a different
number.

### 2a. Race-level battery

- `prob_D` — probability the Democratic candidate wins, decimal 0 to 1
- `pick` — which candidate the model would name if forced to choose one
- `reasoning` — at most two sentences
- `confidence` — low / medium / high, in its own words

**Whether the probability is asked as one question naming both candidates, or
as separate questions, is decided by the pilot** (§10) and not before. The
joint form constrains the answers to sum to one, which is either a fix or a
concealment: a model that would answer 0.6 to each candidate separately will
happily normalise when both are on screen, and we would never learn that the
underlying judgment was incoherent. The pilot runs both forms on the same
races and the difference decides the production wording.

### 2b. Candidate-level battery — the "AI Perceptions" dataset

Asked per candidate, one call each, all measures in one response.

**Ideology.** Three items, deliberately overlapping:

- the standard 7-point survey placement (very liberal … very conservative),
  worded to match ANES/CCES so the model's answer occupies the same
  measurement space as human respondents placing the same candidate
- a 0–100 placement, because coarse ordinals cluster on the middle and the
  labels while a continuous scale has the resolution to correlate with DIME
  CFscores
- one sentence of justification

**Quality.** Split, because the literature's "candidate quality" and a
voter's sense of a good candidate are different constructs:

- **factual** — has this person previously held elected office, and which?
  This is Jacobson's operationalisation, it is checkable against the roster,
  and it therefore doubles as a **measured hallucination rate on a
  consequential political fact**. It is the only item in the battery with a
  right answer and it is the one to protect if anything has to be cut.
- **perceived** — a 0–100 judgment of candidate strength

**Open-ended.** One free-text question ("what should a voter know about this
candidate?"). This exists primarily to maximise retrieval: a tightly
constrained numeric question may be answered from priors after a single
search, while an open question pulls many more sources. Since the citation
set is the outcome variable in the search arm, the open item is the
highest-yield instrument in the battery.

Free text is coded afterwards in a **separate, cheap pass** over stored
responses — no search, small model, batch API — and a sample is also coded by
hand. Classifier-to-human agreement is reported in the appendix. Coding is
never done in the same call that produced the text.

### 2c. Answer format

JSON and nothing else. A response that does not parse is stored anyway,
marked `unparseable`, and counted. Silently dropping the answers a model
fumbled would bias the record toward the races it found easy.

## 3. Models

Five providers, so the category clears the MIN_N=3 disclosure floor with two
to spare and so no single model is published by name.

**Two, not one, is the right margin.** With four providers a single
mid-season model deprecation, a billing failure, or a provider that starts
refusing election questions drops the panel to exactly three, where one more
parse failure in one race suppresses the cell entirely. The fifth is
insurance against publishing nothing in the last week of October, which is
the week that matters most.

**Mid tier, not flagship.** The target is what a person on a roughly
$20/month consumer plan is served, not the most capable model money can buy.
The API analogue of that tier as of August 2026:

| provider | model | rate (in / out per Mtok) |
|---|---|---|
| Anthropic | Claude Sonnet 5 | $2 / $10 |
| OpenAI | GPT-5.6 Terra | $2.50 / $15 |
| Google | Gemini 3.1 Pro | $2 / $12 |
| xAI | Grok 4.6 | $2 / $6 |
| open weights | to be chosen, via a single host | — |

Grok is included deliberately: it is marketed as politically differentiated,
and a panel of models that all decline in the same direction would tell us
less than one that does not. The open-weights model is included so the panel
is not entirely a study of four American frontier labs.

**Google is included because of who uses it rather than because of what it
scores.** Gemini is the assistant reached through Search, Android, and
Workspace rather than through a decision to visit a chat site, which makes it
the model most likely to answer a question from someone who was not looking
for an AI in the first place. That is precisely the person §1 is about, and a
panel that omitted it would be a study of people who chose a chatbot.

The tier choice for Google is **3.1 Pro and not Flash**, on the same
criterion as everywhere else: Google AI Pro is the $19.99 consumer plan and
Pro is what it serves. Flash is the free tier. Taking Flash because it is
cheaper would put one member of the panel on a different rung from the other
four and quietly confound provider with tier.

**Google's search is Google's.** Gemini's native retrieval is Google Search,
so of the five providers it is the only one where the model and the retrieval
engine are the same company. This is not a problem to correct — it is the
condition a real user is in — but it makes Gemini the informative case in the
native-vs-uniform test in §10, because any native-search advantage it shows
over the uniform condition is integration rather than a better index.

Each call records:

| field | why |
|---|---|
| `provider`, `model_id` | the exact version string the API returns, never a family name |
| `knowledge_cutoff` | as documented by the provider on the day of the call |
| `temperature`, `top_p`, `max_tokens`, `seed` if offered | the sampling regime |
| `web_access` | true or false, **recorded from the request actually sent**, never assumed |
| `search_provider` | whose search the model used, or `none` |
| `n_searches` | how many searches the call triggered |
| `requested_at`, `latency_ms` | ordinary provenance |

`model_id` matters more than it looks. "Claude" is not a forecaster; a
specific dated snapshot is. When a provider silently upgrades an alias, the
recorded id changes and the archive shows a new forecaster appearing rather
than an old one mysteriously improving.

**Search is not uniform across providers**, and this is a known limitation
rather than a solved problem. Anthropic and OpenAI have first-party search
tools; an open-weights model on a third-party host generally does not. Doing
the search ourselves and injecting results would make the arms comparable and
would no longer be what a real user experiences. We take native search where
it exists, record `search_provider` per call, and treat cross-provider
citation comparisons as conditional on it.

## 4. Sampling

**Five draws** per model per race per wave for the race-level battery, at a
fixed temperature recorded per call. One draw is a sample from a
distribution, not an opinion, and treating it as a point estimate hides
variance often larger than the differences being measured.

**One draw** for the candidate-level battery. These are closer to retrieval
than to probabilistic judgment, and five draws of a factual question buys
little. The pilot (§10) measures draw dispersion and may revise this number
upward before collection begins; it will not be revised after.

A model's value for a race in a wave is the **median of its draws**.
Draw-level dispersion is kept and published alongside, because a model that
answers 0.55 five times and one that answers 0.2, 0.4, 0.55, 0.7, 0.9 have
the same median and are not the same forecaster.

## 5. Which races, how often, and the roster

### 5a. Cadence

| tier | definition | search arm | cold arm |
|---|---|---|---|
| competitive | our win probability in [0.05, 0.95], **or** any expert rating on the newest day that is not Safe / Solid / Likely | weekly; the tightest ~30 daily in the final month | once |
| everything else | remaining House, Senate, governor races | **once**, plus a rotating control sample of ~30 per wave | once |
| candidate battery | every candidate in a competitive race | four fixed waves, to measure source drift | once |
| all races | — | once at each scoring horizon in RULES §3 | once per horizon |

**Uncompetitive races are asked once** because they are not expected to move,
and asking 385 safe races weekly would consume most of the budget for no
signal. But they are **not dropped**: a rotating sample of about thirty is
carried in every wave, because the safe races are the ones that detect a
broken forecaster. A model returning 0.5 for a district that has voted
Republican by thirty points since 2010 has told us something important about
itself, and a single observation cannot distinguish stable from lucky.

**Re-entry.** Competitiveness is computed from the archive on the day of the
run. A race that becomes competitive later — a retirement, a scandal, an
indictment — joins the cadence from that wave. Being in the asked-once bucket
is a property of a date, not a permanent assignment.

**The candidate battery is re-asked in the search arm even though the answers
are not expected to change.** The outcome being measured there is not the
ideology score but the citation set, and a candidate whose placement is
identical every wave while its sources shift from a metro daily to a national
cable outlet is exactly the finding the media arm exists to detect.

The selected list is stored with each wave, so a later reader can see which
races were asked and when rather than inferring it from which answers exist.

### 5b. The roster

The candidate-level battery requires knowing who is on the ballot, and the
archive does not currently contain that. `conditions/candidacy_events.csv`
records departures — retirements, members seeking other office — and not
candidacies; it holds no party and no nominees.

The roster will be imported from a general-election candidate list (DDHQ or
50+1 class of source), stored under the same capture/parse split as every
other source, and pinned per wave so a later reader knows which slate was
asked about. **The roster is never model-generated.** Asking a model who the
candidates are and then asking it to grade them would let it mark its own
homework about a person it may have invented, and would destroy the
hallucination measure in §2b.

Until the roster exists, only the race-level battery can run.

### 5c. The collection window, and why the two arms run on different clocks

**Search arm: the final six weeks.** First wave **2026-09-22**, weekly on
Tuesdays, the tightest ~30 races daily from **2026-10-06**, last wave
**2026-11-02**. Nine waves, plus roughly four weeks of daily runs on the
tight set.

This is a narrower window than an August start would have given, and it was
chosen rather than merely accepted. The reason is that the window is part of
the estimand.

The person this arm is about is not an election junkie. Someone who follows
politics year-round is not asking a chatbot in October who to vote for; they
already know, and they have opinions about the aggregators. The person who
asks is the one who does not think about the election until it is nearly
here — the low-attention voter who notices in the last month, or the last
week, that there is a ballot coming and has to find out what is on it. That
person is a large share of the electorate and a very large share of the
persuadable electorate, and the question of what an AI tells *them* is the
question worth answering.

A March or June wave measures a model answering about a race that no such
person is asking about, in an information environment that does not yet
exist: no candidates settled in some districts, little local coverage, and
nothing for retrieval to find but a poll average. That is a real measurement
of something, but it is not a measurement of the thing this arm exists to
measure, and paying for twenty weeks of it in order to have a longer line on
a chart would be spending the budget on the wrong quantity.

**What this costs, stated plainly so it is not discovered later.** Three
things get worse and one does not:

1. **Within-model drift over a long horizon cannot be observed.** If a model
   is systematically overconfident in June and calibrated in October, this
   design cannot see it. Accepted; it is a different paper.
2. **Response to a changing national environment is compressed.** Nine waves
   over six weeks are highly autocorrelated, so they are not nine
   independent observations of how the panel reacts to news.
3. **The §9 dated-archive defence starts later.** Still sound — every row is
   committed before there is a result to leak — but the margin is weeks
   rather than months, and it applies to the search arm only.
4. **Calibration is not much affected**, because its sample size is races and
   not waves. Roughly 470 races at each scoring horizon is the number that
   drives the calibration table, and that number is unchanged.

**Cold arm: run early, in the first half of September.** The cold arm does
*not* move to the six-week window, and the reason is that its measurement
decays while the search arm's improves. What it measures is a model's latent
prior about a race — what is in the weights before anything is retrieved —
and every week of campaign coverage that reaches a training set or a silent
alias upgrade makes that prior a little more a memory of the campaign and a
little less a prior about it. Running it in September and the search arm in
October also makes the arm gap in §12 an honest quantity: the cold answers
are demonstrably not a copy of what the search arm read that morning.

The cold arm is also cheap enough that this is nearly free. No search results
in context means a few hundred input tokens per call rather than ten
thousand, and no per-search charge at all; the whole cold pass across all
five providers is on the order of thirty dollars. There is no budget argument
for delaying it and a measurement argument for not.

**Freeze dates.** The rest of the capture pipeline freezes 2026-09-01. The AI
panel does not, because its pilot has not run; it freezes on **2026-09-21**,
the day before the first search wave, and the pilot (§10) must be complete
and reported before that date. This document and `PILOT.md` are pinned by
hash in `ai_panel.py`, so a prompt edited after the freeze cannot be run
against by accident. Decoupling the two dates is deliberate and is recorded
here rather than left as a slipped deadline: a pipeline that captures other
people's forecasts and a panel that generates its own are different
instruments and there is no reason they must freeze together.

## 6. The citation record

Every search-arm response stores, in addition to its text:

- every URL the model cited or retrieved
- the resolved domain and, where determinable, the outlet
- an outlet classification applied afterwards, never by the model in the same
  call: national / local / partisan-left / partisan-right / aggregator /
  institutional / other / unresolved
- the search queries the model issued, where the provider exposes them

This is a dataset in its own right and is analysed independently of whether
the forecast was any good. The questions it answers: whether models reason
from evidence or restate a poll average; whether local journalism reaches an
AI answer about a local race at all; whether outlet mix differs across
providers on identical prompts; and whether it shifts as an election nears.

Classification of outlets is done from a fixed, published list committed
before collection. An outlet not on the list is `unresolved` and counted, not
guessed.

## 7. Aggregation

The category value for a race on a date is the **median across models of each
model's median draw**. Median at both levels, because with four or five
models one provider returning 0.99 for everything would drag a mean and
cannot drag a median.

`n_sources` is the number of models returning a parseable answer for that
race in that wave. The MIN_N=3 floor applies exactly as to every other
category: fewer than three gated models and the cell is suppressed.

The two arms are aggregated separately and never pooled. They are different
questions.

## 8. From race probabilities to a national tide

Every other category in this archive produces a national tide that is carried
through partisan lean to 435 districts. The AI panel arrives from the other
end: a probability per race with no tide behind it. Summing those
probabilities gives an expected seat count with the wrong variance, because
it contains no correlated national error — the term that dominates the tails
and gives the seat distribution its width.

So the panel's tide is **inverted**: find the national margin *T* minimising
the squared difference between our model's implied probability for each race
at *T* and the panel's probability for that race, across every race the panel
answered. One-dimensional, cheap, and it uses the whole slate rather than the
close races alone.

This is a genuinely different route to a tide from every other line on the
site — district-level judgment aggregated upward, rather than a national
number pushed down — which is the reason to do it rather than simply
declining to give the panel a national figure.

**The residuals are kept and published.** A race whose probability no
national tide can explain is a race where the panel is making a
candidate-specific claim, and those are the rows to read against the
reasoning text and the citations.

The inversion runs on the **cold arm** where available, for the independence
reason in §0.

## 9. Contamination

The original worry was a model whose training data includes the 2026 result.
With the search arm that worry inverts: contamination *by the result* is
impossible before election day because there is no result, while
contamination *by other forecasters* is guaranteed and is the point.

Defences, by how much they are worth:

1. **The archive is dated.** A forecast recorded on a given day and committed
   to git that day cannot have been informed by a November result whatever a
   provider later claims about the model. This is the strong defence, and it
   holds for a six-week window exactly as it holds for a six-month one — a
   row committed on 2026-10-13 is as safely pre-result as one committed in
   March. What the narrower window costs is margin, not validity: see §5c(3).
   It is, separately, the reason the **cold** arm runs in early September
   rather than late October (§5c).
2. **Every row carries the documented knowledge cutoff on the day of the
   call.** A row whose cutoff postdates the resolution date is excluded from
   real-time scoring and reported separately. Bookkeeping, only as good as the
   provider's disclosure.
3. **The cold arm is the control.** Its answers cannot have come from a poll
   read this morning, so the gap between the arms measures how much of the
   search arm's forecast is retrieval rather than reasoning.
4. **The safe-race and calibration checks** would show a model recalling
   rather than reasoning: near-perfect calibration on competitive races
   combined with implausible confidence is the signature.

**After election day** the same harness may keep running. Those answers are a
different dataset — memory, not forecast — labelled as such and never scored
as real-time. In the search arm they are independently interesting: which
outlets a model cites when *explaining* a result, against which it cited when
predicting one. Pre-registered here because it costs nothing today and could
not be added credibly in November.

## 10. The pilot

A pilot runs before any production wave, on all five providers, and is itself
pre-registered in `forecast/ai/PILOT.md` with its own hash pin. Its results
are reported as an appendix and are the justification for the final design.
A pilot chosen after its results are in is no better than a design chosen
after the answers are in.

**It must be complete and reported by 2026-09-21** (§5c). A pilot whose
results arrive after the first production wave has decided nothing.

| test | what it decides |
|---|---|
| nonexistent race | Ask about a district that does not exist (WY-3). A confident answer means confidence is unanchored, and calibrates trust in everything else. |
| complement / joint vs separate | Do separately-asked probabilities sum to 1? Does the joint form fix that or conceal it? **Decides §2a wording.** |
| paraphrase invariance | Same race, two or three harmless rewordings. If a comma moves `prob_D` by 0.2, the frozen prompt is doing more work than the model. |
| resolved-election recall | A handful of 2022 races. Not a forecast test — a test of whether the model knows the political world at all. |
| draws to convergence | One race, 40 draws, running median. **Decides `DRAWS` in §4.** |
| ideology instrument | Scale-only, text-only, and both-together on the same candidates. Order effects, and whether the closed scale and the coded text agree. **Decides §2b wording.** |
| external validation | Correlate ideology placements against DIME CFscores for candidates with known scores. If near zero, the measure measures nothing. |
| source concentration | The same competitive race across all five providers with search on. Do they read the same three outlets? |
| native vs uniform search | The same races run twice: once on each provider's own search, once with retrieval done by a single external engine for all five. Native has external validity and is confounded with provider; uniform has internal validity and is not what any user experiences. **Decides which is primary and which is the robustness check.** Gemini is the informative cell — see §3. |

## 11. Publication

Default tier `aggregate_only` for every provider until their terms are read
and a decision recorded in `sources/2026.yaml`: the category average may be
published, no model is named beside a number during the cycle. Raw responses
live in the private archive like every other captured byte.

Provenance is `captured`. The model produced that text on that date and we
stored what it said, which is the same relationship the archive has to a
market price or a Wikipedia revision.

Under the two-facet taxonomy in `collect/facets.py` both arms are
`type = composite`, `source = ai`.

## 12. Scoring

Scored by `score.py`, on the horizons in RULES §3, by the same code and with
no exemption, alongside every other category. Additionally:

- a calibration table in ten bins, per model and for the panel
- the rate of unparseable and refused answers, per model
- median draw-level dispersion, per model
- a **safe-race sanity score**: mean absolute error where every expert rater
  said "Safe", which should be near zero for any forecaster worth reading
- agreement with the expert ratings the models were likely trained on, as a
  crude parroting check
- **the arm gap**: search minus cold, per race, which is the estimate of how
  much retrieval is worth. Under §5c the arms are collected weeks apart, so
  this gap is retrieval **plus elapsed time** and is reported as such. The
  cold arm is re-run once at each scoring horizon (§0), and those re-runs are
  what separate the two: a cold answer taken on the same day as a search wave
  gives the retrieval effect with time differenced out, while the September
  cold pass gives the latent prior before the campaign's final weeks. Both
  are wanted; conflating them would attribute to search whatever the calendar
  did on its own.
- **prior-office accuracy** from §2b, which is a hallucination rate on a
  checkable political fact

## 13. Amendments

Every change to this file after its first commit is listed here with its date
and reason. `ai_panel.py` refuses to run against an unrecognised hash.

| version | sha256 | in git at | status |
|---|---|---|---|
| 1 | `7b7280e88f8b42df77059a8a704651290fc009a6a781d0f916ec7506af0311dd` | `8de997a` | superseded |
| 2 | recompute on commit | — | current |

Version 1 is recorded by hash and commit so a reader can check what it
actually said rather than taking this section's word for it. Recompute the
current hash with `shasum -a 256 forecast/ai/PREREGISTRATION.md` at the
commit that lands it, and pin that value in `ai_panel.py`. The pin is only
worth anything if it is set from the committed file rather than from a draft.

**Amendment 1 — 2026-08-27. Web search, two arms, candidate battery, roster,
tide inversion, pilot.**

Made **before any API call**. No data of any kind had been collected under
version 1, which is the only thing that makes a change of this size
legitimate; the same edit after one wave would have been indefensible and the
record should show that it was not made then.

What changed and why:

- **Web search from forbidden to central (§0, §2, §6).** Version 1 asked what
  is latent in a model. That is a good question and it survives as the cold
  arm. But it cannot address how AI mediates political information, which is
  the more distinctive contribution available here and requires the model to
  actually retrieve. Rather than replace one question with the other, both
  are run as separate arms with separate source ids.
- **§6 added: the citation record.** The reason the search arm exists. The
  Anthropic API returns citations with source URLs, so the outlet mix is a
  captured field rather than something inferred from the reasoning text.
- **§2b added: the candidate-level battery**, with ideology asked on both a
  survey-comparable scale and a continuous one, quality split into a
  checkable factual item and a perceived one, and an open-ended item whose
  purpose is to maximise retrieval.
- **§5b added: the roster**, which version 1 assumed and the archive does not
  have.
- **§8 added: the tide inversion**, so the panel is comparable to every other
  category rather than confined to a race-level panel.
- **§10 added: the pilot**, and several §2 wordings deferred to it rather
  than fixed here. Deferring a choice to a pre-registered test is not the
  same as leaving it open.
- **§3: models fixed at the mid tier** rather than left as "at least three
  providers", and a fourth added.
- **§4: draws split** — five for probability judgments, one for the candidate
  battery, subject to revision by the pilot and not after.
- **§9 rewritten**: with search on, contamination by the result is replaced
  by contamination by other forecasters, and the cold arm becomes the
  control.

**Amendment 2 — 2026-08-27. A fifth provider, and a six-week search window
with the cold arm split off early.**

Also made **before any API call**. Nothing has been collected under any
version of this document. Both changes are recorded here with what they cost
as well as what they buy, because an amendment that lists only the upside is
a press release.

- **§3: Google added, panel goes from four providers to five.** Two reasons,
  and the second is the real one. The disclosure margin: four providers means
  one deprecation or one refusing model leaves the panel at exactly MIN_N=3,
  where a single parse failure suppresses a cell in the week that matters
  most. And reach: Gemini is the assistant that arrives through Search,
  Android, and Workspace rather than through a decision to open a chatbot,
  which makes it the model most likely to be answering the low-attention
  voter §5c is about. Omitting it would have made the panel a study of people
  who chose an AI.
- **§3: Gemini 3.1 Pro, not Flash**, holding the "$20/month consumer plan"
  criterion fixed rather than taking the cheaper tier and confounding
  provider with tier.
- **§3, §10: Gemini's native search is Google Search**, the one cell where
  model and retrieval engine share a company. Recorded because it makes that
  cell the informative one in the native-vs-uniform test rather than an
  awkward exception to it.
- **§10: the native-vs-uniform test added explicitly** to the pilot table. It
  was described in discussion and not written down, which is the same as not
  pre-registered.
- **§5c added: the collection window.** The search arm moves to the final six
  weeks, first wave 2026-09-22. This began as a schedule constraint — the
  pilot cannot be run and evaluated before 2026-09-01 — and on examination
  is the better design, because the window is part of the estimand: the
  people who ask an AI about an election are disproportionately the people
  who start paying attention in the last month. A June wave measures a model
  answering about a race nobody is asking about. The three things this costs
  are listed in §5c and not buried.
- **§5c, §0, §9: the cold arm decoupled and moved earlier**, to the first
  half of September. Its measurement decays with time where the search arm's
  improves, it costs on the order of thirty dollars, and running it early
  makes the arm gap in §12 defensible. This is the part of the amendment that
  would have been indefensible to make later, and it is being made now for
  that reason.
- **§12: the arm gap redefined** as retrieval *plus* elapsed time, with the
  same-day cold re-runs at each scoring horizon identified as what separates
  the two. Under version 1 the arms were contemporaneous and the gap needed
  no such qualification; under §5c they are not, and pretending otherwise
  would attribute the calendar to search.
- **§10: the pilot given a deadline**, 2026-09-21. A pilot reported after the
  first production wave has decided nothing.
- **Freeze dates decoupled.** The capture pipeline freezes 2026-09-01; the AI
  panel freezes 2026-09-21. Recorded as a decision rather than allowed to
  look like a missed deadline.

Not changed, and worth saying: no question wording, no draw count, no
aggregation rule, and no scoring horizon moved in this amendment. The two
changes are *who is in the panel* and *when it runs*.
