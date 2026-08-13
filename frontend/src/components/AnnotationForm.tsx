import { useEffect, useState } from "react";
import { Annotation } from "../lib/types";
import { postJson } from "../lib/api";

export default function AnnotationForm({
  positionId, annotation, onSaved,
}: {
  positionId: number;
  annotation: Annotation | null;
  onSaved: () => void;
}) {
  const a = annotation;
  const [setup, setSetup] = useState(a?.setup ?? "");
  const [confidence, setConfidence] = useState(a?.confidence != null ? String(a.confidence) : "");
  const [emotion, setEmotion] = useState(a?.emotion ?? "");
  const [fp, setFp] = useState(a?.followed_plan === 1 ? "yes" : a?.followed_plan === 0 ? "no" : "");
  const [notes, setNotes] = useState(a?.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  // Resync fields only when the trade (positionId) changes, NOT on same-trade
  // refetches — a TagEditor reload must not clobber unsaved edits.
  useEffect(() => {
    setSetup(a?.setup ?? "");
    setConfidence(a?.confidence != null ? String(a.confidence) : "");
    setEmotion(a?.emotion ?? "");
    setFp(a?.followed_plan === 1 ? "yes" : a?.followed_plan === 0 ? "no" : "");
    setNotes(a?.notes ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positionId]);

  const submit = async () => {
    setSaving(true); setErr(null); setOk(false);
    const confRaw = confidence.trim();
    if (confRaw !== "") {
      const c = Number(confRaw);
      if (!Number.isInteger(c) || c < 1 || c > 5) {
        setSaving(false);
        setErr("confidence harus bilangan bulat 1–5 (kosongkan bila belum dicatat)");
        return;
      }
    }
    const body = {
      setup: setup.trim() === "" ? null : setup.trim(),
      confidence: confRaw === "" ? null : Number(confRaw),
      emotion: emotion.trim() === "" ? null : emotion.trim(),
      followed_plan: fp === "yes" ? true : fp === "no" ? false : null,
      notes: notes.trim() === "" ? null : notes,
    };
    const r = await postJson(`/api/trades/${positionId}/annotate`, body);
    setSaving(false);
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    setOk(true); onSaved();
  };

  const field = "bg-white/5 rounded px-2 py-1 text-ink w-full";
  return (
    <div className="glass p-4">
      <h2 className="text-title font-semibold uppercase tracking-wider text-muted mb-3">Anotasi</h2>
      <div className="flex flex-col gap-3 text-body">
        <label className="flex flex-col gap-1 text-muted">Setup
          <input className={field} value={setup} onChange={(e) => setSetup(e.target.value)} placeholder="mis. breakout" /></label>
        <label className="flex flex-col gap-1 text-muted">Confidence (1–5, kosong=belum dicatat)
          <input className={field + " num"} type="number" min={1} max={5} value={confidence}
            onChange={(e) => setConfidence(e.target.value)} /></label>
        <label className="flex flex-col gap-1 text-muted">Emosi
          <input className={field} value={emotion} onChange={(e) => setEmotion(e.target.value)} placeholder="mis. tenang / fomo" /></label>
        <div className="flex flex-col gap-1 text-muted">Ikut plan?
          <div className="flex gap-4 text-ink">
            {[["yes", "ya"], ["no", "tidak"], ["", "—"]].map(([v, label]) => (
              <label key={v} className="flex items-center gap-1">
                <input type="radio" name={`fp-${positionId}`} checked={fp === v} onChange={() => setFp(v)} /> {label}
              </label>
            ))}
          </div>
        </div>
        <label className="flex flex-col gap-1 text-muted">Catatan
          <textarea className={field + " min-h-[72px]"} value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="apa yang terjadi, pelajaran…" /></label>
        {err && <div className="text-neg">Ditolak: {err}</div>}
        {ok && !err && <div className="text-cyan">Tersimpan.</div>}
        <button className="self-start px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold"
          onClick={submit} disabled={saving}>{saving ? "Menyimpan…" : "Simpan anotasi"}</button>
      </div>
    </div>
  );
}
