import type { ReactNode } from "react";

// DESIGN.md § Status Pill. Three tints, one vocabulary: cyan = a reading that
// is current, neg = wrong or degraded, muted = nothing is running. The dot
// glows on `now` only (The One Glow Rule).
export type Tone = "now" | "wrong" | "absent";

/** Fill + ring only. Shape and text colour stay with the caller, so a
 *  multi-line card (LabBadge) can wear the same vocabulary as the pill. */
export const TONE_TINT: Record<Tone, string> = {
  now: "bg-cyan/10 ring-1 ring-cyan/25",
  wrong: "bg-neg/10 ring-1 ring-neg/25",
  absent: "bg-white/5",
};

const TONE_TEXT: Record<Tone, string> = {
  now: "text-cyan",
  wrong: "text-neg",
  absent: "text-muted",
};

const DOT: Record<Tone, string> = {
  now: "bg-cyan shadow-glow",
  wrong: "bg-neg",
  absent: "",
};

export default function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`text-meta px-2.5 py-1 rounded-full flex items-center gap-1.5 ${TONE_TEXT[tone]} ${TONE_TINT[tone]}`}>
      {DOT[tone] && <span className={`w-1.5 h-1.5 rounded-full ${DOT[tone]}`} />}
      {children}
    </span>
  );
}
