import React from "react";
import { AbsoluteFill } from "remotion";

const BG = "#0b1020";
const INK = "#e8ecff";
const DIM = "#8a93b8";
const BLUE = "#7aa2ff";
const AMBER = "#fbbf24";
const SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif";

export type CardProps = { text: string };

export const Intro: React.FC = () => (
  <AbsoluteFill style={{ background: BG, alignItems: "center", justifyContent: "center", fontFamily: SANS }}>
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 150, fontWeight: 800, color: INK }}>
        JobPilot <span style={{ color: AMBER }}>×</span> Fo
      </div>
      <div style={{ fontSize: 56, color: BLUE, marginTop: 24 }}>My job search, run by Claude and Fo</div>
      <div style={{ fontSize: 36, color: DIM, marginTop: 56 }}>Real session · real data · GSD hackathon</div>
    </div>
  </AbsoluteFill>
);

export const Outro: React.FC = () => (
  <AbsoluteFill style={{ background: BG, alignItems: "center", justifyContent: "center", fontFamily: SANS }}>
    <div style={{ textAlign: "center", width: 1500 }}>
      <div style={{ fontSize: 66, fontWeight: 800, color: INK }}>
        JobPilot already existed. The Fo link, the safety layer and applications are new.
      </div>
      <div style={{ fontSize: 60, color: BLUE, marginTop: 48 }}>github.com/4asaanAI/job-seeker</div>
      <div style={{ fontSize: 42, color: DIM, marginTop: 28 }}>Open source · MIT · Thanks, @wajo_ai</div>
    </div>
  </AbsoluteFill>
);

// Transparent everywhere except the bottom strip, so it can be overlaid on the footage.
export const Caption: React.FC<CardProps> = ({ text }) => (
  <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end", paddingBottom: 26, fontFamily: SANS }}>
    <div style={{ background: "rgba(17,24,51,0.94)", border: "2px solid #26305c", borderRadius: 18,
      padding: "18px 34px", fontSize: 40, color: INK, maxWidth: 1700, textAlign: "center", lineHeight: 1.25 }}>
      {text}
    </div>
  </AbsoluteFill>
);

export type OutputProps = { title: string; lines: string[]; note: string };

// A text card for evidence that was not captured on screen. It says so in its note.
export const Output: React.FC<OutputProps> = ({ title, lines, note }) => (
  <AbsoluteFill style={{ background: BG, alignItems: "center", justifyContent: "center", fontFamily: SANS }}>
    <div style={{ width: 1560 }}>
      <div style={{ fontSize: 56, fontWeight: 800, color: INK, marginBottom: 28 }}>{title}</div>
      <div style={{ background: "#111833", border: "2px solid #26305c", borderRadius: 20, padding: "30px 40px",
        fontFamily: "Menlo, 'SF Mono', Consolas, monospace", fontSize: 34, lineHeight: 1.5, color: "#6ee7a8" }}>
        {lines.map((l, i) => <div key={i}>{l}</div>)}
      </div>
      <div style={{ fontSize: 32, color: AMBER, marginTop: 28 }}>{note}</div>
    </div>
  </AbsoluteFill>
);

export const Thumb: React.FC<{ line1: string; line2: string }> = ({ line1, line2 }) => (
  <AbsoluteFill style={{ background: BG, alignItems: "center", justifyContent: "center", fontFamily: SANS }}>
    <div style={{ textAlign: "center", width: 1600 }}>
      <div style={{ fontSize: 138, fontWeight: 800, color: INK, lineHeight: 1.05 }}>{line1}</div>
      <div style={{ fontSize: 64, color: BLUE, marginTop: 36 }}>{line2}</div>
      <div style={{ fontSize: 44, color: AMBER, marginTop: 56 }}>JobPilot × Fo · GSD hackathon</div>
    </div>
  </AbsoluteFill>
);

export const Attack: React.FC<{ head: string; sub: string; lines: string[]; tone: string; note: string }> = ({ head, sub, lines, tone, note }) => (
  <AbsoluteFill style={{ background: BG, alignItems: "center", justifyContent: "center", fontFamily: SANS }}>
    <div style={{ width: 1640 }}>
      <div style={{ fontSize: 60, fontWeight: 800, color: tone }}>{head}</div>
      <div style={{ fontSize: 34, color: DIM, margin: "8px 0 28px" }}>{sub}</div>
      <div style={{ background: "#111833", border: `2px solid ${tone}`, borderRadius: 20, padding: "28px 36px",
        fontFamily: "Menlo, 'SF Mono', Consolas, monospace", fontSize: 31, lineHeight: 1.55, color: INK }}>
        {lines.map((l, i) => <div key={i}>{l}</div>)}
      </div>
      <div style={{ fontSize: 30, color: AMBER, marginTop: 24 }}>{note}</div>
    </div>
  </AbsoluteFill>
);
