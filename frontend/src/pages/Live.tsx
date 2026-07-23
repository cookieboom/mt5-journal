import { useApi } from "../lib/api";
import { LiveData, ActionKind, CommandBody } from "../lib/types";
import { money } from "../lib/format";
import StalenessBadge from "../components/StalenessBadge";
import LivePositionCard from "../components/LivePositionCard";

export default function Live() {
  const { data, error, loading } = useApi<LiveData>("/api/live", 2500);
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, live } = data;

  // Wired to the two-step confirm in Task 6.
  const onAction = (action: ActionKind, body: CommandBody) => {
    console.debug("action (wiring in Task 6)", action, body);
  };

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

      {live.empty ? (
        <div className="glass p-6 text-muted text-sm">
          Tidak ada posisi terbuka — atau <code>journal live</code> belum pernah jalan.
          Tanpa heartbeat, keduanya tak bisa dibedakan dari sini.
        </div>
      ) : (
        live.positions.map((p) => (
          <LivePositionCard key={p.position_id} pos={p} currency={header.currency} onAction={onAction} />
        ))
      )}
    </div>
  );
}
