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

<div style="max-width: 900px; margin: 40px auto; padding: 0 20px;">
    <div id="chartContainer" style="margin: 20px 0;"></div>
    <p style="font-size: 12px; color: #9C9488; margin: 16px 0 4px; line-height: 1.5;" id="annotation">Nearly every candidate receives net-negative coverage.</p>
</div>

<script>
const DATA = [
    { year: "1948", dem: "Truman", rep: "Dewey", demCov: -0.029, repCov: 0.105, diff: -0.134 },
    { year: "1952", dem: "Stevenson", rep: "Eisenhower", demCov: 0.046, repCov: 0.105, diff: -0.060 },
    { year: "1956", dem: "Stevenson", rep: "Eisenhower", demCov: 0.098, repCov: 0.138, diff: -0.040 },
    { year: "1960", dem: "Kennedy", rep: "Nixon", demCov: 0.071, repCov: 0.081, diff: -0.010 },
    { year: "1964", dem: "Johnson", rep: "Goldwater", demCov: 0.091, repCov: -0.002, diff: 0.093 },
    { year: "1968", dem: "Humphrey", rep: "Nixon", demCov: 0.096, repCov: 0.058, diff: 0.038 },
    { year: "1972", dem: "McGovern", rep: "Nixon", demCov: 0.004, repCov: -0.011, diff: 0.015 },
    { year: "1976", dem: "Carter", rep: "Ford", demCov: -0.013, repCov: -0.043, diff: 0.030 },
    { year: "1980", dem: "Carter", rep: "Reagan", demCov: -0.091, repCov: -0.021, diff: -0.070 },
    { year: "1984", dem: "Mondale", rep: "Reagan", demCov: 0.024, repCov: -0.068, diff: 0.092 },
    { year: "1988", dem: "Dukakis", rep: "Bush Sr.", demCov: -0.010, repCov: -0.065, diff: 0.055 },
    { year: "1992", dem: "Clinton", rep: "Bush Sr.", demCov: -0.034, repCov: -0.211, diff: 0.177 },
    { year: "1996", dem: "Clinton", rep: "Dole", demCov: -0.079, repCov: -0.131, diff: 0.052 },
    { year: "2000", dem: "Gore", rep: "Bush Jr.", demCov: -0.062, repCov: -0.068, diff: 0.006 },
    { year: "2004", dem: "Kerry", rep: "Bush Jr.", demCov: -0.047, repCov: -0.157, diff: 0.110 },
    { year: "2008", dem: "Obama", rep: "McCain", demCov: 0.010, repCov: -0.065, diff: 0.075 },
    { year: "2012", dem: "Obama", rep: "Romney", demCov: -0.104, repCov: -0.086, diff: -0.019 },
    { year: "2016", dem: "Clinton", rep: "Trump", demCov: -0.120, repCov: -0.279, diff: 0.160 },
    { year: "2020", dem: "Biden", rep: "Trump", demCov: -0.032, repCov: -0.354, diff: 0.322 },
    { year: "'24 (Biden)", dem: "Biden", rep: "Trump", demCov: -0.292, repCov: -0.305, diff: 0.013 },
    { year: "'24 (Harris)", dem: "Harris", rep: "Trump", demCov: -0.015, repCov: -0.305, diff: 0.289 },
];

let currentView = 'sideBySide';

function drawChart() {
    const width = 880;
    const height = 420;
    const padding = { top: 20, right: 20, bottom: 60, left: 50 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    
    const yMin = currentView === 'sideBySide' ? -0.4 : -0.2;
    const yMax = currentView === 'sideBySide' ? 0.2 : 0.35;
    const yRange = yMax - yMin;
    
    const barWidth = chartWidth / DATA.length * 0.8;
    const barGap = chartWidth / DATA.length * 0.2;
    
    let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;
    
    // Grid lines
    for (let i = -0.4; i <= 0.4; i += 0.1) {
        if (i >= yMin && i <= yMax) {
            const y = padding.top + chartHeight * (1 - (i - yMin) / yRange);
            svg += `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="#E0DDD5" stroke-width="1"/>`;
            svg += `<text x="${padding.left - 10}" y="${y + 4}" font-size="11" text-anchor="end" fill="#9C9488">${i.toFixed(1)}</text>`;
        }
    }
    
    // Zero line
    const zeroY = padding.top + chartHeight * (1 - (0 - yMin) / yRange);
    svg += `<line x1="${padding.left}" y1="${zeroY}" x2="${width - padding.right}" y2="${zeroY}" stroke="#1A1A1A" stroke-width="2"/>`;
    
    // Axes
    svg += `<line x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}" stroke="#1A1A1A" stroke-width="2"/>`;
    svg += `<line x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}" stroke="#1A1A1A" stroke-width="2"/>`;
    
    // Data
    DATA.forEach((d, i) => {
        const x = padding.left + i * chartWidth / DATA.length + barGap / 2;
        
        if (currentView === 'sideBySide') {
            const demHeight = (d.demCov - yMin) / yRange * chartHeight;
            const repHeight = (d.repCov - yMin) / yRange * chartHeight;
            
            const demY = padding.top + chartHeight - demHeight;
            const repY = padding.top + chartHeight - repHeight;
            
            svg += `<rect x="${x}" y="${demY}" width="${barWidth / 2 - 1}" height="${demHeight}" fill="#2166AC"/>`;
            svg += `<rect x="${x + barWidth / 2 + 1}" y="${repY}" width="${barWidth / 2 - 1}" height="${repHeight}" fill="#B2182B"/>`;
        } else {
            const diffHeight = (d.diff - yMin) / yRange * chartHeight;
            const diffY = padding.top + chartHeight - diffHeight;
            const color = d.diff >= 0 ? '#2166AC' : '#B2182B';
            
            svg += `<rect x="${x}" y="${diffY}" width="${barWidth}" height="${diffHeight}" fill="${color}"/>`;
        }
        
        svg += `<text x="${x + barWidth / 2}" y="${height - padding.bottom + 20}" font-size="11" text-anchor="middle" fill="#6B6559">${d.year}</text>`;
    });
    
    svg += `</svg>`;
    
    document.getElementById('chartContainer').innerHTML = svg;
}

drawChart();
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
