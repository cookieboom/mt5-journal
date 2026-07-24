import { useState } from "react";
import { postJson } from "../lib/api";

export default function TagEditor({
  positionId, tags, onChanged,
}: {
  positionId: number;
  tags: [string, string][];
  onChanged: () => void;
}) {
  const [newTag, setNewTag] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    if (busy || newTag.trim() === "") return;
    setBusy(true); setErr(null);
    const r = await postJson(`/api/trades/${positionId}/tags`, { tag: newTag.trim() });
    setBusy(false);
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    setNewTag(""); onChanged();
  };
  const del = async (tag: string) => {
    if (busy) return;
    setBusy(true); setErr(null);
    const r = await postJson(`/api/trades/${positionId}/tags/delete`, { tag });
    setBusy(false);
    if (!r.ok) { setErr(r.error ?? "gagal"); return; }
    onChanged();
  };

  return (
    <div className="glass p-4">
      <h2 className="text-[13px] font-semibold uppercase tracking-wider text-muted mb-3">Tags</h2>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {tags.length === 0 && <span className="text-muted text-[12px]">(belum ada tag)</span>}
        {tags.map(([tag, source]) => (
          <span key={tag} className={"px-2 py-0.5 rounded text-[11px] flex items-center gap-1 " +
            (source === "manual" ? "bg-violet/15 text-violet" : "bg-white/6 text-muted")}>
            {tag}
            {source === "manual" && (
              <button className="text-muted hover:text-neg" title="hapus tag manual" onClick={() => del(tag)}>×</button>
            )}
          </span>
        ))}
      </div>
      <div className="flex gap-2 text-[12px]">
        <input className="bg-white/5 rounded px-2 py-1 text-ink flex-1" value={newTag}
          onChange={(e) => setNewTag(e.target.value)} placeholder="tag manual, mis. revenge-trade"
          onKeyDown={(e) => { if (e.key === "Enter") add(); }} />
        <button className="px-3 py-1.5 rounded bg-violet/20 ring-1 ring-violet/40 text-ink" onClick={add} disabled={busy}>Tambah</button>
      </div>
      {err && <div className="text-neg text-[12px] mt-2">Ditolak: {err}</div>}
      <p className="text-[11px] text-muted mt-2">Tag <span className="px-1 rounded bg-white/6">auto</span> di-set oleh <code>rebuild</code> dan tak bisa dihapus di sini.</p>
    </div>
  );
}
