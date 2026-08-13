import { useApi } from "../lib/api";
import { LiveData } from "../lib/types";
import { money } from "../lib/format";
import StalenessBadge from "../components/StalenessBadge";
import LivePositionCard from "../components/LivePositionCard";
import ConfirmModal from "../components/ConfirmModal";
import LiveLabBadge from "../components/LiveLabBadge";
import { useLiveCommand } from "../hooks/useLiveCommand";
import { useChartPrefs } from "../hooks/useChartPrefs";
import { SYMBOLS, type Sym } from "../lib/candles";

export default function Live() {
  const { data, error, loading } = useApi<LiveData>("/api/live", 2500);
  const cmd = useLiveCommand();
  // Only the FALLBACK for a symbol with nothing trained — LiveLabBadge reads
  // the timeframe off the symbol's own model. The user's saved preference
  // (not the hardcoded default) is the right guess when there is no model.
  const { settings } = useChartPrefs();

  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  const { header, live } = data;

  // Known-traded symbols only (CLAUDE.md's symbol set) — an unrecognised
  // value would have nothing trained for it anyway.
  const openSymbols = Array.from(new Set(live.positions.map((p) => p.symbol)))
    .filter((s): s is Sym => (SYMBOLS as string[]).includes(s));

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-headline font-bold">Live</h1>
          <div className="text-body text-muted mt-0.5">
            {live.count} posisi · total floating{" "}
            <span className={(live.total_floating >= 0 ? "text-pos" : "text-neg") + " num"}>
              {money(live.total_floating, header.currency, { sign: true })}
            </span>
          </div>
        </div>
        <StalenessBadge live={live} />
      </div>

      {/* Descriptive only — this is deliberately in the page header, away from
          every position card's close/add-volume buttons below (CLAUDE.md
          rule 9: nothing here may read as an instruction to act). */}
      {openSymbols.length > 0 && (
        <div className="glass p-3 mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {openSymbols.map((s) => (
            <LiveLabBadge key={s} symbol={s} fallbackTf={settings.defaultTimeframe} />
          ))}
        </div>
      )}

      {cmd.toast && <div className="glass p-3 mb-3 text-body text-cyan">{cmd.toast}</div>}
      {cmd.error && !cmd.preview && <div className="glass p-3 mb-3 text-body text-neg">Ditolak: {cmd.error}</div>}

      {live.empty ? (
        <div className="glass p-6 text-muted text-body">
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
