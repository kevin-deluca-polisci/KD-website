---
title: "What Headlines Actually Say About Presidents"
date: 2025-03-07
tags: ["Media", "Headlines"]
image: "/blog-images/headlines-summary.jpg"

---

# What Headlines Actually Say About Presidents

*The standard way political scientists measure media tone has a blind spot. We built a better method, and it changes what we know about how coverage shapes elections.*

Consider two headlines: "Biden struggles to contain inflation" and "Inflation eases under Biden." One is bad news for the president. The other is good news. But most tools used to measure media tone would code both of them as negative, because both contain the word "inflation."

This is not a minor technical glitch. It's a fundamental problem with how political scientists and media researchers have measured news coverage for decades. Standard sentiment analysis looks at the words in a headline and asks: is this positive or negative? What it *can't* do is tell you who it's positive or negative *for*. A headline that says "Several polls show Biden with lead over Trump" gets coded as positive for both candidates, even though it's obviously great news for one and terrible news for the other.

In a new paper with my coauthor Zoe Kava, we built a measure that fixes this. And once you can actually tell what headlines say about specific candidates, some important things come into focus.

## Reading between the headlines

The simple idea is that instead of asking "is this headline positive or negative," we ask a different question: "does this headline imply that *this particular candidate* is performing well or poorly?" We use a technique from natural language processing called stance detection, essentially training a model to evaluate whether a headline supports or undermines a specific claim about a specific person. (The approach builds on the Political DEBATE model developed by Michael Burnham and colleagues, which we fine-tuned for this task.)

We applied it to nearly 850,000 newspaper headlines from U.S. presidential elections stretching back to 1948. The model agrees with human coders 84.2% of the time, which is slightly *better* than the two human coders agree with each other (82.4%). The standard approach, a widely used sentiment classifier called RoBERTa, agrees with humans only 62% of the time. The gap comes from a specific failure: RoBERTa catches just 33% of headlines that humans rate as positive for a candidate, defaulting to "neutral" when it doesn't see an obviously positive or negative word.

[FIGURE: Model-human agreement comparison showing DEBATE at 84.2%, human intercoder at 82.4%, and RoBERTa at 62.0%, with recall breakdown by class. Source: Figure 1 from the paper]

## What the measure reveals

Three findings stand out.

First, newspapers cover candidates from different parties differently, and those differences line up with their editorial endorsements. Papers that endorse more Democrats also cover Democratic presidential candidates more favorably. The *New York Times* and *Washington Post* sit in the upper-right corner of the graph: Democratic-leaning endorsements and more favorable coverage of Democratic candidates. The *Chicago Tribune* falls near the center, reflecting its historically mixed record. None of this is shocking, but it's reassuring. The measure recovers patterns that make intuitive sense.

![](/blog-images/bias_validation_decade.png)

Second, and this is where it gets interesting, month-to-month shifts in how newspapers portray the president predict changes in presidential approval. When coverage of the president improves, approval goes up. A one-standard-deviation improvement in coverage corresponds to about a 0.11 standard deviation increase in approval in the short run, and roughly half a standard deviation over the long run. These effects hold even after controlling for consumer sentiment about the economy. The standard sentiment measure? Statistically insignificant. When both measures are included simultaneously, the standard measure actually flips sign. It becomes *negatively* associated with approval, suggesting it's picking up noise rather than signal.

Third, adding our coverage measure to a standard presidential election forecasting model (the well-known Fair model, which uses economic fundamentals like GDP growth and inflation) reduces prediction error by about 25–30%. That's a meaningful improvement on a model that already works pretty well. The gains are most visible in elections where economic conditions alone give ambiguous signals, like 2000 and 2016.

[FIGURE: In-sample election predictions from 1948 to 2024, comparing the Fair model alone vs. Fair + candidate coverage, with actual results. Source: Figure 3 from the paper]

## Why this matters beyond academia

The broader point is that media coverage isn't just a mirror reflecting what's already happening in politics. It contains independent evaluative information, judgments about whether a president is doing well or badly, that shapes how voters assess their leaders. But you can only see this if you measure coverage in a way that tracks *who* it evaluates, not just whether it uses positive or negative words.

This matters especially right now, as debates about media bias, consolidation, and corporate ownership intensify. If we want to understand whether and how the press holds politicians accountable, we need measurement tools that capture what coverage actually communicates to voters. A tool that can't distinguish "good news for Biden" from "bad news for Biden" isn't up to the task.

There's a lot more to explore: how coverage varies by topic, whether it works differently for incumbents versus challengers, how television and social media compare to newspapers. But the core finding is clear. What headlines say about specific candidates matters. And for 75 years of American elections, it's been shaping both public opinion and election outcomes in ways that our standard tools simply couldn't detect.

---

*Kevin DeLuca is an Assistant Professor of Political Science at Yale University. This post discusses "Candidate-specific media coverage predicts presidential approval ratings and election results," coauthored with Zoe Kava. Read the full paper [here](link).*
