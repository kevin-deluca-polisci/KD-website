import { useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, Cell, Legend } from "recharts";

// ============================================================
// DESIGN TOKENS — edit these to restyle the visualization
// ============================================================
const TOKENS = {
  bg: "#FAFAF5",
  bgAlt: "#F3F1EA",
  textPrimary: "#1A1A1A",
  textSecondary: "#6B6559",
  textMuted: "#9C9488",
  accentDem: "#2166AC",
  accentRep: "#B2182B",
  accentNeutral: "#878078",
  gridLine: "#E0DDD5",
  ruleLine: "#1A1A1A",
  fontHeading: "'Playfair Display', 'Georgia', serif",
  fontBody: "'Source Sans 3', 'Helvetica Neue', sans-serif",
  fontData: "'Source Sans 3', 'Helvetica Neue', sans-serif",
};

// ============================================================
// DATA — from Table S5 in the paper (DEBATE model)
// ============================================================
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

  return (
    <div style={{
      background: TOKENS.bg,
      border: `1px solid ${TOKENS.gridLine}`,
      padding: "12px 16px",
      fontFamily: TOKENS.fontBody,
      fontSize: 13,
      lineHeight: 1.5,
      maxWidth: 260,
      boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
    }}>
      <div style={{ fontFamily: TOKENS.fontHeading, fontSize: 15, marginBottom: 6, color: TOKENS.textPrimary }}>
        {d.year}: {d.dem} vs. {d.rep}
      </div>
      {view === "sideBySide" ? (
        <>
          <div style={{ color: TOKENS.accentDem }}>
            {d.dem}: {d.demCov > 0 ? "+" : ""}{d.demCov.toFixed(3)}
          </div>
          <div style={{ color: TOKENS.accentRep }}>
            {d.rep}: {d.repCov > 0 ? "+" : ""}{d.repCov.toFixed(3)}
          </div>
          <div style={{ color: TOKENS.textMuted, marginTop: 4, fontSize: 12, borderTop: `1px solid ${TOKENS.gridLine}`, paddingTop: 4 }}>
            Actual margin: Dem {d.voteMargin > 0 ? "+" : ""}{d.voteMargin} pts
          </div>
        </>
      ) : (
        <>
          <div style={{ color: d.diff > 0 ? TOKENS.accentDem : TOKENS.accentRep }}>
            Coverage gap: {d.diff > 0 ? "Dem" : "Rep"} +{Math.abs(d.diff).toFixed(3)}
          </div>
          <div style={{ color: TOKENS.textMuted, marginTop: 4, fontSize: 12, borderTop: `1px solid ${TOKENS.gridLine}`, paddingTop: 4 }}>
            Actual margin: Dem {d.voteMargin > 0 ? "+" : ""}{d.voteMargin} pts
          </div>
        </>
      )}
    </div>
  );
};

export default function CandidateCoverageViz() {
  const [view, setView] = useState("sideBySide");

  const sideBySideData = DATA.map(d => ({
    year: d.year,
    democrat: d.demCov,
    republican: d.repCov,
  }));

  const diffData = DATA.map(d => ({
    year: d.year,
    diff: d.diff,
    positive: d.diff >= 0,
  }));

  return (
    <div style={{
      background: TOKENS.bg,
      fontFamily: TOKENS.fontBody,
      color: TOKENS.textPrimary,
      maxWidth: 900,
      margin: "0 auto",
      padding: "40px 32px 28px",
    }}>
      {/* Google Fonts */}
      <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Source+Sans+3:wght@300;400;600&display=swap" rel="stylesheet" />

      {/* Masthead rule */}
      <div style={{ borderTop: `3px solid ${TOKENS.ruleLine}`, marginBottom: 4 }} />
      <div style={{ borderTop: `1px solid ${TOKENS.ruleLine}`, marginBottom: 24 }} />

      {/* Headline */}
      <h1 style={{
        fontFamily: TOKENS.fontHeading,
        fontSize: 28,
        fontWeight: 900,
        lineHeight: 1.15,
        margin: "0 0 8px",
        letterSpacing: "-0.01em",
      }}>
        How Newspapers Covered Every Presidential Candidate Since 1948
      </h1>

      {/* Deck */}
      <p style={{
        fontFamily: TOKENS.fontBody,
        fontSize: 15,
        fontWeight: 300,
        color: TOKENS.textSecondary,
        margin: "0 0 24px",
        lineHeight: 1.4,
      }}>
        Implied candidate performance scores from nearly 850,000 newspaper headlines, measured using stance detection.
        Scores range from −1 (entirely negative coverage) to +1 (entirely positive).
      </p>

      {/* Toggle */}
      <div style={{
        display: "flex",
        gap: 0,
        marginBottom: 20,
        borderBottom: `1px solid ${TOKENS.gridLine}`,
      }}>
        {[
          { key: "sideBySide", label: "Each candidate" },
          { key: "difference", label: "Coverage gap (Dem − Rep)" },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setView(key)}
            style={{
              fontFamily: TOKENS.fontBody,
              fontSize: 13,
              fontWeight: view === key ? 600 : 400,
              color: view === key ? TOKENS.textPrimary : TOKENS.textMuted,
              background: "none",
              border: "none",
              borderBottom: view === key ? `2px solid ${TOKENS.textPrimary}` : "2px solid transparent",
              padding: "8px 16px",
              cursor: "pointer",
              marginBottom: -1,
              transition: "all 0.15s ease",
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Chart */}
      <div style={{ width: "100%", height: 420 }}>
        <ResponsiveContainer>
          {view === "sideBySide" ? (
            <BarChart data={sideBySideData} barGap={1} barCategoryGap="20%" margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
              <CartesianGrid vertical={false} stroke={TOKENS.gridLine} strokeDasharray="none" />
              <XAxis
                dataKey="year"
                tick={{ fontFamily: TOKENS.fontData, fontSize: 11, fill: TOKENS.textSecondary }}
                axisLine={{ stroke: TOKENS.ruleLine }}
                tickLine={false}
                interval={0}
                angle={-45}
                textAnchor="end"
                height={50}
              />
              <YAxis
                tick={{ fontFamily: TOKENS.fontData, fontSize: 11, fill: TOKENS.textSecondary }}
                axisLine={false}
                tickLine={false}
                domain={[-0.4, 0.2]}
                tickFormatter={v => v.toFixed(1)}
              />
              <ReferenceLine y={0} stroke={TOKENS.ruleLine} strokeWidth={1} />
              <Tooltip content={<CustomTooltip view="sideBySide" />} />
              <Bar dataKey="democrat" fill={TOKENS.accentDem} radius={[2, 2, 0, 0]} name="Democrat" />
              <Bar dataKey="republican" fill={TOKENS.accentRep} radius={[2, 2, 0, 0]} name="Republican" />
              <Legend
                wrapperStyle={{ fontFamily: TOKENS.fontBody, fontSize: 12, paddingTop: 8 }}
                iconType="square"
                iconSize={10}
              />
            </BarChart>
          ) : (
            <BarChart data={diffData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
              <CartesianGrid vertical={false} stroke={TOKENS.gridLine} strokeDasharray="none" />
              <XAxis
                dataKey="year"
                tick={{ fontFamily: TOKENS.fontData, fontSize: 11, fill: TOKENS.textSecondary }}
                axisLine={{ stroke: TOKENS.ruleLine }}
                tickLine={false}
                interval={0}
                angle={-45}
                textAnchor="end"
                height={50}
              />
              <YAxis
                tick={{ fontFamily: TOKENS.fontData, fontSize: 11, fill: TOKENS.textSecondary }}
                axisLine={false}
                tickLine={false}
                domain={[-0.2, 0.35]}
                tickFormatter={v => v.toFixed(1)}
              />
              <ReferenceLine y={0} stroke={TOKENS.ruleLine} strokeWidth={1} />
              <Tooltip content={<CustomTooltip view="difference" />} />
              <Bar dataKey="diff" radius={[2, 2, 0, 0]} name="Coverage gap">
                {diffData.map((entry, i) => (
                  <Cell key={i} fill={entry.positive ? TOKENS.accentDem : TOKENS.accentRep} />
                ))}
              </Bar>
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      {/* Annotation */}
      <p style={{
        fontFamily: TOKENS.fontBody,
        fontSize: 12,
        color: TOKENS.textMuted,
        margin: "16px 0 4px",
        lineHeight: 1.5,
      }}>
        {view === "sideBySide"
          ? "Nearly every candidate receives net-negative coverage. But the gap between candidates varies dramatically by election. Note the two 2024 entries: Biden's coverage was far more negative than Harris's after she replaced him."
          : "Positive values indicate the Democratic candidate received relatively more favorable coverage. The 2020 election and 2024 (Harris) show the largest coverage gaps, driven by unusually negative Trump coverage. When Biden was still the 2024 nominee, the coverage gap was near zero."
        }
      </p>

      {/* Bottom rule */}
      <div style={{ borderTop: `1px solid ${TOKENS.gridLine}`, margin: "16px 0 12px" }} />

      {/* Source */}
      <p style={{
        fontFamily: TOKENS.fontBody,
        fontSize: 11,
        color: TOKENS.textMuted,
        margin: 0,
        lineHeight: 1.5,
      }}>
        Source: DeLuca and Kava, "Candidate-specific media coverage predicts presidential approval ratings and election results" (2026). 
        Data from Table S5. Net coverage scores computed using the DEBATE stance detection model across five major U.S. newspapers (NYT, Washington Post, WSJ, LA Times, Chicago Tribune).
        Hover over bars for candidate names and actual vote margins.
      </p>
    </div>
  );
}
