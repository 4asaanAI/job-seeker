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
