import { useApi } from "../lib/api";
import { LiveData } from "../lib/types";
import { money } from "../lib/format";
import StalenessBadge from "../components/StalenessBadge";
import LivePositionCard from "../components/LivePositionCard";
import ConfirmModal from "../components/ConfirmModal";
import { useLiveCommand } from "../hooks/useLiveCommand";

export default function Live() {
  const { data, error, loading } = useApi<LiveData>("/api/live", 2500);
  const cmd = useLiveCommand();

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, live } = data;

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

      {cmd.toast && <div className="glass p-3 mb-3 text-[12px] text-cyan">{cmd.toast}</div>}
      {cmd.error && !cmd.preview && <div className="glass p-3 mb-3 text-[12px] text-neg">Ditolak: {cmd.error}</div>}

      {live.empty ? (
        <div className="glass p-6 text-muted text-sm">
          Tidak ada posisi terbuka — atau <code>journal live</code> belum pernah jalan.
          Tanpa heartbeat, keduanya tak bisa dibedakan dari sini.
        </div>
      ) : (
        live.positions.map((p) => (
          <LivePositionCard key={p.position_id} pos={p} currency={header.currency}
            onAction={(action, body) => cmd.request(p.position_id, action, body)} />
        ))
      )}

      {cmd.preview && (
        <ConfirmModal preview={cmd.preview} submitting={cmd.submitting} error={cmd.error}
          onConfirm={cmd.confirm} onCancel={cmd.cancel} />
      )}
    </div>
  );
}
