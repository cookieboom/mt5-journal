import { EquitySvg } from "../lib/types";

export default function EquityChart({ svg, label }: { svg: EquitySvg; label: string }) {
  if (svg.empty) {
    return <div className="text-muted text-sm py-10 text-center">Belum ada data {label}.</div>;
  }
  const vbW = svg.viewbox.split(" ")[2] ?? "720";
  const uid = label.replace(/\s+/g, "-");
  return (
    <svg viewBox={svg.viewbox} preserveAspectRatio="none" className="w-full h-[150px]">
      <defs>
        <linearGradient id={`eqArea-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`eqLine-${uid}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#a78bfa" />
          <stop offset="100%" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      <polygon points={svg.area} fill={`url(#eqArea-${uid})`} />
      <polyline points={svg.points} fill="none" stroke={`url(#eqLine-${uid})`} strokeWidth="2.5" />
      <line x1="0" y1={svg.baseline_y} x2={vbW} y2={svg.baseline_y}
            stroke="rgba(255,255,255,0.18)" strokeWidth="1" strokeDasharray="4 4" />
    </svg>
  );
}
