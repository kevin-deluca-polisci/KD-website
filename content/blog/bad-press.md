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

<div id="chart-container" style="max-width: 100%; margin: 40px 0; background: #FAFAF5; padding: 20px; border-radius: 4px;">
    <div style="display: flex; gap: 0; margin-bottom: 20px; border-bottom: 1px solid #E0DDD5;">
        <button id="btn-sidebyside" onclick="switchView('sideBySide')" style="flex: 1; padding: 8px 16px; font-size: 13px; border: none; background: none; border-bottom: 2px solid #1A1A1A; font-weight: 600; color: #1A1A1A; cursor: pointer;">Each candidate</button>
        <button id="btn-diff" onclick="switchView('difference')" style="flex: 1; padding: 8px 16px; font-size: 13px; border: none; background: none; border-bottom: 2px solid transparent; color: #9C9488; cursor: pointer;">Coverage gap</button>
    </div>
    <svg id="chart" width="100%" height="400" style="background: white; border: 1px solid #E0DDD5;"></svg>
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

function switchView(view) {
    currentView = view;
    document.getElementById('btn-sidebyside').style.fontWeight = view === 'sideBySide' ? '600' : '400';
    document.getElementById('btn-sidebyside').style.color = view === 'sideBySide' ? '#1A1A1A' : '#9C9488';
    document.getElementById('btn-sidebyside').style.borderBottom = view === 'sideBySide' ? '2px solid #1A1A1A' : '2px solid transparent';
    
    document.getElementById('btn-diff').style.fontWeight = view === 'difference' ? '600' : '400';
    document.getElementById('btn-diff').style.color = view === 'difference' ? '#1A1A1A' : '#9C9488';
    document.getElementById('btn-diff').style.borderBottom = view === 'difference' ? '2px solid #1A1A1A' : '2px solid transparent';
    
    drawChart();
}

function drawChart() {
    const svg = document.getElementById('chart');
    svg.innerHTML = '';
    
    const width = svg.clientWidth;
    const height = svg.clientHeight;
    const padding = { top: 20, right: 20, bottom: 40, left: 50 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    
    const yMin = currentView === 'sideBySide' ? -0.4 : -0.2;
    const yMax = currentView === 'sideBySide' ? 0.2 : 0.35;
    const yRange = yMax - yMin;
    
    const barWidth = (chartWidth / DATA.length) * 0.7;
    const barSpacing = chartWidth / DATA.length;
    
    // Grid lines
    for (let i = -0.4; i <= 0.4; i += 0.1) {
        if (i >= yMin && i <= yMax) {
            const y = padding.top + chartHeight * (1 - (i - yMin) / yRange);
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', padding.left);
            line.setAttribute('y1', y);
            line.setAttribute('x2', width - padding.right);
            line.setAttribute('y2', y);
            line.setAttribute('stroke', '#E0DDD5');
            line.setAttribute('stroke-width', '1');
            svg.appendChild(line);
        }
    }
    
    // Zero line
    const zeroY = padding.top + chartHeight * (1 - (0 - yMin) / yRange);
    const zeroLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    zeroLine.setAttribute('x1', padding.left);
    zeroLine.setAttribute('y1', zeroY);
    zeroLine.setAttribute('x2', width - padding.right);
    zeroLine.setAttribute('y2', zeroY);
    zeroLine.setAttribute('stroke', '#1A1A1A');
    zeroLine.setAttribute('stroke-width', '2');
    svg.appendChild(zeroLine);
    
    // Axes
    const yAxis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    yAxis.setAttribute('x1', padding.left);
    yAxis.setAttribute('y1', padding.top);
    yAxis.setAttribute('x2', padding.left);
    yAxis.setAttribute('y2', height - padding.bottom);
    yAxis.setAttribute('stroke', '#1A1A1A');
    yAxis.setAttribute('stroke-width', '2');
    svg.appendChild(yAxis);
    
    const xAxis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    xAxis.setAttribute('x1', padding.left);
    xAxis.setAttribute('y1', height - padding.bottom);
    xAxis.setAttribute('x2', width - padding.right);
    xAxis.setAttribute('y2', height - padding.bottom);
    xAxis.setAttribute('stroke', '#1A1A1A');
    xAxis.setAttribute('stroke-width', '2');
    svg.appendChild(xAxis);
    
    // Data bars
    DATA.forEach((d, i) => {
        const xPos = padding.left + i * barSpacing + (barSpacing - barWidth) / 2;
        
        if (currentView === 'sideBySide') {
            // Democrat bar
            const demHeight = (d.demCov - yMin) / yRange * chartHeight;
            const demY = padding.top + chartHeight - demHeight;
            
            const demRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            demRect.setAttribute('x', xPos);
            demRect.setAttribute('y', demY);
            demRect.setAttribute('width', barWidth / 2 - 1);
            demRect.setAttribute('height', Math.max(0, demHeight));
            demRect.setAttribute('fill', '#2166AC');
            demRect.setAttribute('class', 'bar');
            demRect.setAttribute('data-year', d.year);
            demRect.setAttribute('data-name', d.dem);
            demRect.setAttribute('data-value', d.demCov.toFixed(3));
            demRect.style.cursor = 'pointer';
            svg.appendChild(demRect);
            
            // Republican bar
            const repHeight = (d.repCov - yMin) / yRange * chartHeight;
            const repY = padding.top + chartHeight - repHeight;
            
            const repRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            repRect.setAttribute('x', xPos + barWidth / 2 + 1);
            repRect.setAttribute('y', repY);
            repRect.setAttribute('width', barWidth / 2 - 1);
            repRect.setAttribute('height', Math.max(0, repHeight));
            repRect.setAttribute('fill', '#B2182B');
            repRect.setAttribute('class', 'bar');
            repRect.setAttribute('data-year', d.year);
            repRect.setAttribute('data-name', d.rep);
            repRect.setAttribute('data-value', d.repCov.toFixed(3));
            repRect.style.cursor = 'pointer';
            svg.appendChild(repRect);
        } else {
            // Diff bar
            const diffHeight = (d.diff - yMin) / yRange * chartHeight;
            const diffY = padding.top + chartHeight - diffHeight;
            const color = d.diff >= 0 ? '#2166AC' : '#B2182B';
            
            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('x', xPos);
            rect.setAttribute('y', diffY);
            rect.setAttribute('width', barWidth);
            rect.setAttribute('height', Math.max(0, diffHeight));
            rect.setAttribute('fill', color);
            rect.setAttribute('class', 'bar');
            rect.setAttribute('data-year', d.year);
            rect.setAttribute('data-value', d.diff.toFixed(3));
            rect.style.cursor = 'pointer';
            svg.appendChild(rect);
        }
        
        // Year labels
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', xPos + barWidth / 2);
        text.setAttribute('y', height - padding.bottom + 15);
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('font-size', '11');
        text.setAttribute('fill', '#6B6559');
        text.textContent = d.year;
        svg.appendChild(text);
    });
}

window.addEventListener('load', drawChart);
window.addEventListener('resize', drawChart);
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
