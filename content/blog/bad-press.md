---
title: "Nearly Every Presidential Candidate Gets Bad Press. The Question Is How Much Worse."
date: 2025-03-07
tags: ["Media", "Headlines", "Elections"]
image: "/KD-website/blog-images/bad-press.png"
author: "Kevin DeLuca"
description: "An interactive look at 75 years of newspaper coverage shows that nearly every major-party presidential candidate has received net-negative coverage—but the gaps between candidates tell us something real about elections."
---

Here's something you might not expect: almost every major-party presidential candidate since 1948 has received net-negative coverage from American newspapers. Positive headlines about candidates are the exception, not the rule. What varies, sometimes dramatically, is the *gap* between how the two candidates are covered.

In a new paper with my coauthor Zoe Kava, we measured what newspaper headlines actually imply about specific candidates' performance, using a technique called stance detection applied to nearly 850,000 headlines. The interactive chart below shows the results for every presidential election from Truman-Dewey in 1948 through Harris-Trump in 2024.

<div id="viz-root" style="margin: 40px 0;"></div>

<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/recharts@2.10.3/dist/Recharts.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Sans+3:wght@300;400;600&display=swap" rel="stylesheet" />

<script>
const { useState } = React;
const { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell, Legend } = Recharts;

const TOKENS = {
  bg: "#FAFAF5",
  textPrimary: "#1A1A1A",
  textSecondary: "#6B6559",
  textMuted: "#9C9488",
  accentDem: "#2166AC",
  accentRep: "#B2182B",
  gridLine: "#E0DDD5",
  ruleLine: "#1A1A1A",
};

const DATA = [
  { year: "1948", dem: "Truman", rep: "Dewey", demCov: -0.029, repCov: 0.105, diff: -0.134, voteMargin: 4.5 },
  { year: "1952", dem: "Stevenson", rep: "Eisenhower", demCov: 0.046, repCov: 0.105, diff: -0.060, voteMargin: -10.9 },
  { year: "1956", dem: "Stevenson", rep: "Eisenhower", demCov: 0.098, repCov: 0.138, diff: -0.040, voteMargin: -15.4 },
  { year: "1960", dem: "Kennedy", rep: "Nixon", demCov: 0.071, repCov: 0.081, diff: -0.010, voteMargin: 0.2 },
  { year: "1964", dem: "Johnson", rep: "Goldwater", demCov: 0.091, repCov: -0.002, diff: 0.093, voteMargin: 22.6 },
  { year: "1968", dem: "Humphrey", rep: "Nixon", demCov: 0.096, repCov: 0.058, diff: 0.038, voteMargin: -0.7 },
  { year: "1972", dem: "McGovern", rep: "Nixon", demCov: 0.004, repCov: -0.011, diff: 0.015, voteMargin: -23.2 },
  { year: "1976", dem: "Carter", rep: "Ford", demCov: -0.013, repCov: -0.043, diff: 0.030, voteMargin: 2.1 },
  { year: "1980", dem: "Carter", rep: "Reagan", demCov: -0.091, repCov: -0.021, diff: -0.070, voteMargin: -9.7 },
  { year: "1984", dem: "Mondale", rep: "Reagan", demCov: 0.024, repCov: -0.068, diff: 0.092, voteMargin: -18.2 },
  { year: "1988", dem: "Dukakis", rep: "Bush Sr.", demCov: -0.010, repCov: -0.065, diff: 0.055, voteMargin: -7.8 },
  { year: "1992", dem: "Clinton", rep: "Bush Sr.", demCov: -0.034, repCov: -0.211, diff: 0.177, voteMargin: 5.6 },
  { year: "1996", dem: "Clinton", rep: "Dole", demCov: -0.079, repCov: -0.131, diff: 0.052, voteMargin: 8.5 },
  { year: "2000", dem: "Gore", rep: "Bush Jr.", demCov: -0.062, repCov: -0.068, diff: 0.006, voteMargin: 0.5 },
  { year: "2004", dem: "Kerry", rep: "Bush Jr.", demCov: -0.047, repCov: -0.157, diff: 0.110, voteMargin: -2.4 },
  { year: "2008", dem: "Obama", rep: "McCain", demCov: 0.010, repCov: -0.065, diff: 0.075, voteMargin: 7.2 },
  { year: "2012", dem: "Obama", rep: "Romney", demCov: -0.104, repCov: -0.086, diff: -0.019, voteMargin: 3.9 },
  { year: "2016", dem: "Clinton", rep: "Trump", demCov: -0.120, repCov: -0.279, diff: 0.160, voteMargin: 2.1 },
  { year: "2020", dem: "Biden", rep: "Trump", demCov: -0.032, repCov: -0.354, diff: 0.322, voteMargin: 4.5 },
  { year: "'24 (Biden)", dem: "Biden", rep: "Trump", demCov: -0.292, repCov: -0.305, diff: 0.013, voteMargin: -1.5 },
  { year: "'24 (Harris)", dem: "Harris", rep: "Trump", demCov: -0.015, repCov: -0.305, diff: 0.289, voteMargin: -1.5 },
];

const CustomTooltip = ({ active, payload, label, view }) => {
  if (!active || !payload || !payload.length) return null;
  const d = DATA.find(r => r.year === label);
  if (!d) return null;

  return React.createElement('div', {
    style: {
      background: TOKENS.bg,
      border: `1px solid ${TOKENS.gridLine}`,
      padding: "12px 16px",
      fontFamily: "'Source Sans 3', sans-serif",
      fontSize: 13,
      lineHeight: 1.5,
      maxWidth: 260,
      boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
    }
  }, [
    React.createElement('div', { key: 'title', style: { fontSize: 15, marginBottom: 6, color: TOKENS.textPrimary, fontWeight: 600 } }, `${d.year}: ${d.dem} vs. ${d.rep}`),
    view === "sideBySide" ? [
      React.createElement('div', { key: 'd', style: { color: TOKENS.accentDem } }, `${d.dem}: ${d.demCov > 0 ? "+" : ""}${d.demCov.toFixed(3)}`),
      React.createElement('div', { key: 'r', style: { color: TOKENS.accentRep } }, `${d.rep}: ${d.repCov > 0 ? "+" : ""}${d.repCov.toFixed(3)}`),
    ] : React.createElement('div', { key: 'gap', style: { color: d.diff > 0 ? TOKENS.accentDem : TOKENS.accentRep } }, `Gap: ${d.diff > 0 ? "Dem" : "Rep"} +${Math.abs(d.diff).toFixed(3)}`),
    React.createElement('div', { key: 'margin', style: { color: TOKENS.textMuted, marginTop: 4, fontSize: 12, borderTop: `1px solid ${TOKENS.gridLine}`, paddingTop: 4 } }, `Vote margin: Dem ${d.voteMargin > 0 ? "+" : ""}${d.voteMargin}`)
  ]);
};

function CandidateCoverageViz() {
  const [view, setView] = useState("sideBySide");

  const sideBySideData = DATA.map(d => ({ year: d.year, democrat: d.demCov, republican: d.repCov }));
  const diffData = DATA.map(d => ({ year: d.year, diff: d.diff, positive: d.diff >= 0 }));

  return React.createElement('div', { style: { background: TOKENS.bg, padding: "20px", borderRadius: "4px" } }, [
    React.createElement('div', { key: 'buttons', style: { display: "flex", gap: 0, marginBottom: 20, borderBottom: `1px solid ${TOKENS.gridLine}` } }, [
      { key: "sideBySide", label: "Each candidate" },
      { key: "difference", label: "Coverage gap" },
    ].map(({ key, label }) =>
      React.createElement('button', {
        key,
        onClick: () => setView(key),
        style: {
          fontSize: 13,
          fontWeight: view === key ? 600 : 400,
          color: view === key ? TOKENS.textPrimary : TOKENS.textMuted,
          background: "none",
          border: "none",
          borderBottom: view === key ? `2px solid ${TOKENS.textPrimary}` : "2px solid transparent",
          padding: "8px 16px",
          cursor: "pointer",
          marginBottom: -1,
        }
      }, label)
    )),
    React.createElement('div', { key: 'chart', style: { width: "100%", height: 400 } },
      React.createElement(ResponsiveContainer, {},
        view === "sideBySide" ?
          React.createElement(BarChart, { data: sideBySideData, barGap: 1, barCategoryGap: "20%", margin: { top: 5, right: 10, bottom: 5, left: 10 } },
            React.createElement(CartesianGrid, { vertical: false, stroke: TOKENS.gridLine }),
            React.createElement(XAxis, { dataKey: "year", tick: { fontSize: 11, fill: TOKENS.textSecondary }, axisLine: { stroke: TOKENS.ruleLine }, tickLine: false }),
            React.createElement(YAxis, { tick: { fontSize: 11, fill: TOKENS.textSecondary }, axisLine: false, tickLine: false, domain: [-0.4, 0.2], tickFormatter: v => v.toFixed(1) }),
            React.createElement(ReferenceLine, { y: 0, stroke: TOKENS.ruleLine, strokeWidth: 1 }),
            React.createElement(Tooltip, { content: React.createElement(CustomTooltip, { view: "sideBySide" }) }),
            React.createElement(Bar, { dataKey: "democrat", fill: TOKENS.accentDem, radius: [2, 2, 0, 0], name: "Democrat" }),
            React.createElement(Bar, { dataKey: "republican", fill: TOKENS.accentRep, radius: [2, 2, 0, 0], name: "Republican" }),
            React.createElement(Legend, { wrapperStyle: { fontSize: 12, paddingTop: 8 } })
          ) :
          React.createElement(BarChart, { data: diffData, margin: { top: 5, right: 10, bottom: 5, left: 10 } },
            React.createElement(CartesianGrid, { vertical: false, stroke: TOKENS.gridLine }),
            React.createElement(XAxis, { dataKey: "year", tick: { fontSize: 11, fill: TOKENS.textSecondary }, axisLine: { stroke: TOKENS.ruleLine }, tickLine: false }),
            React.createElement(YAxis, { tick: { fontSize: 11, fill: TOKENS.textSecondary }, axisLine: false, tickLine: false, domain: [-0.2, 0.35], tickFormatter: v => v.toFixed(1) }),
            React.createElement(ReferenceLine, { y: 0, stroke: TOKENS.ruleLine, strokeWidth: 1 }),
            React.createElement(Tooltip, { content: React.createElement(CustomTooltip, { view: "difference" }) }),
            React.createElement(Bar, { dataKey: "diff", radius: [2, 2, 0, 0], name: "Coverage gap" },
              diffData.map((entry, i) => React.createElement(Cell, { key: i, fill: entry.positive ? TOKENS.accentDem : TOKENS.accentRep }))
            )
          )
      )
    )
  ]);
}

const root = ReactDOM.createRoot(document.getElementById('viz-root'));
root.render(React.createElement(CandidateCoverageViz));
</script>

## How to read it

Toggle between the two views. "Each candidate" shows the raw net coverage score for the Democrat (blue) and Republican (red) in each election. Scores range from -1 (entirely negative) to +1 (entirely positive). "Coverage gap" shows the difference: positive values mean the Democrat got relatively more favorable coverage.

A few patterns jump out. In the first view, notice how far below zero most bars sit. The press is not in the business of making candidates look good. But there's real variation in *relative* coverage, and that variation tracks election outcomes more than you might think.

Switch to the "coverage gap" view and the story sharpens. The 2020 and 2024 elections show by far the largest gaps in the dataset, driven almost entirely by unusually negative Trump coverage. In 2020, Trump's net coverage score was -0.354, the most negative for any candidate in the entire 75-year sample. In 1992, George H.W. Bush received a -0.211, the worst before Trump's era, presaging his loss to Clinton. On the other end, the 2000 election between Gore and Bush Jr. shows a coverage gap of nearly zero (0.006), consistent with an election that came down to a few hundred votes in Florida.

## What this tells us

These scores aren't just interesting trivia. In the paper, we show that when you add this coverage measure to standard election forecasting models that use economic fundamentals, prediction error drops by 25 to 30%. Coverage contains real information about how elections will go, information that the economy alone doesn't capture.

The chart also illustrates something subtler: the press doesn't simply favor one party. Democrats got better coverage in some elections, Republicans in others. What drives the gap is the interaction between candidate quality, incumbency dynamics, and the political moment. That's exactly what a good measure of media coverage should pick up.

---

*Kevin DeLuca is an Assistant Professor of Political Science at Yale University. This post discusses "Candidate-specific media coverage predicts presidential approval ratings and election results," coauthored with Zoe Kava. Read the full paper [here](link).*
