# The AI-augmented forecast — pre-registration

Registered 2026-09-07, before any candidate data exists. Companion to
`PREREGISTRATION.md` (the AI panel) and `../endorse_quality/PREREGISTRATION.md`
(the endorsement work this replaces).

---

## 0. What this is, and what it is not

The AI panel asks models to forecast and scores the forecast. **This does not.**
It uses models as a **measuring instrument for candidate attributes**, and feeds
those measurements into the same partisanship-plus-incumbency model the
endorsement work used. The model is never asked who will win.

    two-party D margin
        = a
        + b1 * district partisanship      (DRA, vintage-matched)
        + b2 * cycle fixed effect
        + b3 * incumbency
        + b4..bk * AI-measured candidate attributes (below)

The claim under test is not "AI can forecast elections". It is **"AI can
cheaply measure something about candidates that predicts outcomes once
partisanship and incumbency are controlled"**. Those are different claims with
different failure modes, and a result on one says nothing about the other.

**Why now.** The endorsement route to candidate quality is closed: three
pre-registered measures, three nulls, and a fourth that dissolved under the
obvious follow-up. What survives is infrastructure — 196 races over four
cycles, validated incumbency coding, vintage-matched DRA partisanship — waiting
for a quality measure that works. This is the next candidate, and it is
pre-registered rather than searched for.

---

## 1. Items

Option B of the drafted sets, with cross-party appeal removed. Asked per
candidate, **in one call**, in the fixed order below.

### 1a. Order is part of the instrument

**Factual items first, judgments last.** Not cosmetic. A model asked "how
strong is this candidate, 0–100?" before "has this candidate held elected
office?" has already committed to a score, and the cheapest way to make the
second answer consistent with the first is to invent an office. Reversed, the
judgment is conditioned on facts the model has just had to state and cite.

The order below is frozen with the prompt. The pilot tests the reverse order on
a subsample and reports the difference; it does not choose the production order
after seeing which one predicts better.

### 1b. The battery, in order

1. **`held_office`** — has this person previously held elected office, and
   which offices in which years? *Factual. Checkable against the roster. This
   is Jacobson's operationalisation and it doubles as a measured hallucination
   rate on a consequential political fact. Protect this item if anything is
   cut.*
2. **`local_ties`** — how long has this candidate lived in the state or
   district they are running in? *Factual. The classic carpetbagger measure.*
3. **`record_open`** — open-ended and symmetric: "What are this candidate's
   most notable achievements, and what are the most notable controversies,
   ethics matters or legal proceedings involving them, if any? Cite a source
   for each claim." *This is where scandal is measured, and it is deliberately
   NOT a yes/no question — see 1c.*
4. **`legislative_effectiveness`** — for candidates who have held legislative
   office, 0–100: how effective were they in it? *Defined only where
   `held_office` is true. Externally validated against the Center for Effective
   Lawmaking's LES scores for anyone who served in Congress.*
5. **`ideology_7pt`, `ideology_100`** — as in panel §2b. *Validated against
   DIME CFscores.*
6. **`quality_perceived`** — 0–100 judgment of candidate strength. *The one
   purely perceptual item. Last, so it is conditioned on everything above.*

**Cross-party appeal is excluded.** It is a forecast wearing a
candidate-attribute costume: a judgment about how the other party's voters will
respond is a prediction about the race, and mixing it in is how this arm
quietly becomes the panel it exists to be independent of.

### 1c. Why scandal is open-ended and symmetric

Asking "has this candidate faced a significant scandal?" is a leading question
about a negative event, put to a class of respondent known to be agreeable.
Acquiescence bias is a solved problem in survey design and the solution is not
to ask it that way.

So `record_open` asks for achievements AND controversies together, in one open
item, with a citation required for each claim. Three consequences, all
deliberate:

- **No yes/no to acquiesce to.** A model with nothing to report can produce a
  short achievements list and no controversies, which is a different and more
  informative output than "no".
- **Citations make it checkable.** A fabricated scandal with a fabricated
  source is detectable; a fabricated "yes" is not.
- **Coding happens afterwards** in the separate cheap pass over stored
  responses (panel §2b), with a hand-coded sample and classifier-to-human
  agreement reported. The scandal variable is constructed by us from text, not
  asserted by the model as a number.

---

## 2. Retrieval

**Single call per candidate, provider's native search enabled, retrieval
instructed explicitly.** The prompt tells the model to research the candidate
before answering and to cite sources.

Not an agentic loop. A loop would produce a richer citation set — which is the
search arm's outcome variable, so this is a real cost — but it makes per-
candidate spend unpredictable, raises variance, and makes "the frozen
instrument" much harder to defend when the number of retrieval steps differs by
candidate and by provider. A single call with search on is also closer to what
a person actually gets.

**The harness has no search-arm prompt.** `PROMPT_V1` in `collect/ai_panel.py`
ends "Do not search the web. Answer from what you already know." That is the
COLD arm. A search-arm prompt does not currently exist and must be written and
frozen before 2026-09-21.

**The prompt carries nothing that is not load-bearing.** No persona, no
framing, no encouragement. Per panel §2b-i: irrelevant context measurably
degrades model coherence, so this is a documented failure mode rather than
style.

---

## 3. Fabrication control

The panel's pilot asks about a nonexistent RACE (WY-3). The candidate-level
analogue is added here: **a nonexistent CANDIDATE**, drawn from a plausible
name generator, asked with the full battery.

This is the sharpest available test of the item this arm depends on. A model
that returns a confident office history, a local-ties figure and a scandal for
a person who does not exist has told us the battery measures its own fluency.
The fabrication rate is reported per provider alongside every result.

---

## 4. Timing, and why it is the whole ballgame

**Every measurement must be recorded and committed before the outcome exists.**

The defence is not a claim about training data, which no provider will
document adequately. It is the git history: a row committed on a dated day
cannot have been informed by a November result. The cold arm runs in early
September for exactly this reason, and the archive is committed daily.

A candidate-quality measure collected after the election is not a measure of
candidate quality. It is a measure of hindsight, and it would be worthless for
the model in §0 regardless of how well it fit.

---

## 5. Publication

`NEVER_PUBLISH`, every item, matching the panel's candidate battery. These are
measurements about named living candidates, and a public table of what five
models think of a person is not what the forecast page is for.

Estimated coefficients and predicted values from the model in §0 may be
published on a model page, as with the endorsement work. The inputs may not.

---

## 6. Predictions, registered before collection

1. **`record_open` (scandal) carries the signal, if anything does.** It is the
   clearest case of candidate quality varying WITHIN a party, it is observable
   to voters, and it is the one item here a voter would obviously respond to.
2. **`quality_perceived` is null once partisanship and incumbency are in.** It
   is the closest analogue to the endorsement share that already failed, and it
   should fail for the same reason: it aggregates elite perception, which is
   mostly party.
3. **`held_office` shows a measurable fabrication rate above zero** on the
   nonexistent-candidate control, and a nonzero error rate against the roster
   on real candidates.

If the whole block is null except scandal, that is a finding and not a salvage.
If everything is null, that is reported as the endorsement nulls were.

---

## 7. What would make this fail honestly

Stated now so it cannot be explained away later.

- **The measures are collinear with incumbency.** `held_office` is close to
  incumbency by construction for sitting members. The incumbency-dropped
  robustness check matters here as much as it did for primary consensus, where
  it revealed a term trading places with a control rather than standing on its
  own.
- **Retrieval finds the horse race.** A model told to research a candidate will
  read coverage of the race, and coverage of a race is about who is winning.
  The cold arm is the control for this and the gap between arms is the measure
  of it.
- **n is still small.** 196 races, and the candidate battery only reaches
  competitive races. This arm is a description of a relationship on a modest
  sample, not a validated forecasting input, and nothing from it enters the
  published forecast on this evidence.
