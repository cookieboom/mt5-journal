import { useState, useRef } from "react";
import { useApi, postJson } from "../lib/api";
import { LiveData, PreviewResult, ActionKind, CommandBody } from "../lib/types";
import { money } from "../lib/format";
import StalenessBadge from "../components/StalenessBadge";
import LivePositionCard from "../components/LivePositionCard";
import ConfirmModal from "../components/ConfirmModal";

export default function Live() {
  const { data, error, loading } = useApi<LiveData>("/api/live", 2500);
  const [pending, setPending] = useState<{ action: ActionKind; body: CommandBody } | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const enqueuing = useRef(false);

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, live } = data;

  // Step 1: preview — writes nothing on the server; opens the confirm modal.
  const onAction = async (position_id: number, action: ActionKind, body: CommandBody) => {
    setActionError(null);
    const r = await postJson<PreviewResult>(`/api/live/${position_id}/${action}/preview`, body);
    if (!r.ok) { setToast(null); setActionError(r.error ?? "gagal"); setPreview(null); return; }
    setPending({ action, body });
    setPreview(r.data ?? null);
  };

  // Step 2: enqueue — the ONLY write. Server re-validates.
  const onConfirm = async () => {
    if (!preview || !pending) return;
    if (enqueuing.current) return;   // sub-tick double-submit latch (money path)
    enqueuing.current = true;
    setSubmitting(true);
    const r = await postJson<{ ok: boolean; command_id: number }>(
      `/api/live/${preview.position_id}/${pending.action}`, pending.body);
    setSubmitting(false);
    if (!r.ok) { setActionError(r.error ?? "gagal"); enqueuing.current = false; return; }
    setPreview(null); setPending(null); setActionError(null);
    setToast(`Perintah #${r.data?.command_id} masuk antrean — journal live akan mengeksekusi.`);
    enqueuing.current = false;
  };

  const onCancel = () => { setPreview(null); setPending(null); setActionError(null); enqueuing.current = false; };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-[18px] font-bold tracking-tight">Live</h1>
          <div className="text-[12px] text-muted mt-0.5">
            {live.count} posisi · total floating{" "}
            <span className={(live.total_floating >= 0 ? "text-pos" : "text-neg") + " num"}>
              {money(live.total_floating, header.currency, { sign: true })}
            </span>
          </div>
        </div>
        <StalenessBadge live={live} />
      </div>

      {toast && <div className="glass p-3 mb-3 text-[12px] text-cyan">{toast}</div>}
      {actionError && !preview && <div className="glass p-3 mb-3 text-[12px] text-neg">Ditolak: {actionError}</div>}

      {live.empty ? (
        <div className="glass p-6 text-muted text-sm">
          Tidak ada posisi terbuka — atau <code>journal live</code> belum pernah jalan.
          Tanpa heartbeat, keduanya tak bisa dibedakan dari sini.
        </div>
      ) : (
        live.positions.map((p) => (
          <LivePositionCard key={p.position_id} pos={p} currency={header.currency}
            onAction={(action, body) => onAction(p.position_id, action, body)} />
        ))
      )}

      {preview && (
        <ConfirmModal preview={preview} submitting={submitting} error={actionError}
          onConfirm={onConfirm} onCancel={onCancel} />
      )}
    </div>
  );
}
