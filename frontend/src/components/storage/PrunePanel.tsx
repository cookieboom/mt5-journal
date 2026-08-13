import { useState } from "react";
import { pruneCandles } from "../../lib/storageApi";
import Modal from "../Modal";

export interface PrunePanelProps {
  symbolsList?: string[];
  onPruneSuccess?: () => void;
}

const CUTOFF_OPTIONS = [
  { label: "30 hari", value: 30 },
  { label: "60 hari", value: 60 },
  { label: "90 hari", value: 90 },
  { label: "180 hari (6 bulan)", value: 180 },
  { label: "365 hari (1 tahun)", value: 365 },
];

export default function PrunePanel({
  // Only these three exist on this server; a symbol the account never traded
  // would offer a prune that deletes nothing.
  symbolsList = ["XAUUSD", "BTCUSD", "EURUSD"],
  onPruneSuccess,
}: PrunePanelProps) {
  const [selectedSymbol, setSelectedSymbol] = useState<string>("all");
  const [olderThanDays, setOlderThanDays] = useState<number>(180);
  const [showConfirmModal, setShowConfirmModal] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const targetLabel = selectedSymbol === "all" ? "semua symbol" : selectedSymbol;

  const handlePrune = async () => {
    setSubmitting(true);
    setFeedback(null);

    try {
      const symParam = selectedSymbol === "all" ? undefined : selectedSymbol;
      const res = await pruneCandles(symParam, olderThanDays);
      if (!res.ok || !res.data) {
        throw new Error(res.error ?? "Gagal prune candle");
      }

      const deletedCount = res.data.deleted_bars ?? 0;
      setFeedback({
        type: "success",
        message: `Prune selesai · ${deletedCount.toLocaleString()} bar M1 lebih tua dari ${olderThanDays} hari dihapus untuk ${targetLabel}.`,
      });

      setShowConfirmModal(false);
      if (onPruneSuccess) {
        onPruneSuccess();
      }
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err?.message || "Gagal menghapus candle historis.",
      });
      setShowConfirmModal(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Feedback Banner */}
      {feedback && (
        <div
          // Green is a profitable outcome, not "it worked" (DESIGN.md § Colors).
          className={`p-4 rounded-lg border flex items-start justify-between gap-3 text-body ${
            feedback.type === "success"
              ? "bg-white/5 border-panel-border text-ink"
              : "bg-neg/10 border-neg/30 text-neg"
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.type === "success" ? (
              <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            <span>{feedback.message}</span>
          </div>
          <button
            onClick={() => setFeedback(null)}
            className="text-muted hover:text-ink text-body px-2 py-0.5 rounded"
          >
            Tutup
          </button>
        </div>
      )}

      {/* Main Pruning Control Card */}
      <div className="glass p-6 space-y-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-ink font-bold text-title mb-1">
              <svg className="w-5 h-5 text-neg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
              <h3>Retensi data & prune candle</h3>
            </div>
            <p className="text-body text-muted leading-relaxed max-w-2xl">
              Hapus bar M1 yang lebih tua dari batas tertentu untuk mengosongkan disk dan menekan latensi query. Trade hasil rekonstruksi dan metrik performa tidak tersentuh.
            </p>
          </div>

          <div className="px-3 py-1.5 rounded-lg bg-neg/10 border border-neg/20 text-neg text-body font-mono font-medium flex items-center gap-1.5 shrink-0">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span>aksi destruktif</span>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-panel-border/50">
          {/* Target Symbol Selection */}
          <div>
            <label htmlFor="prune-symbol" className="block text-body font-medium text-muted mb-1.5">
              Symbol target
            </label>
            <select
              id="prune-symbol"
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="w-full glass bg-transparent px-3 py-2 text-body text-ink"
            >
              <option value="all" className="bg-bg">Semua symbol</option>
              {symbolsList.map((sym) => (
                <option key={sym} value={sym} className="bg-bg">
                  {sym}
                </option>
              ))}
            </select>
          </div>

          {/* Retention Threshold Selector */}
          <div>
            <label htmlFor="prune-cutoff" className="block text-body font-medium text-muted mb-1.5">
              Batas retensi
            </label>
            <select
              id="prune-cutoff"
              value={olderThanDays}
              onChange={(e) => setOlderThanDays(Number(e.target.value))}
              className="w-full glass bg-transparent px-3 py-2 text-body text-ink font-mono"
            >
              {CUTOFF_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value} className="bg-bg">
                  Lebih tua dari {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Trigger Button */}
        <div className="pt-3 flex flex-wrap items-center justify-between gap-3 border-t border-panel-border/50">
          <div className="text-body text-muted/70 flex items-center gap-1.5">
            <span>Target:</span>
            <span className="font-mono text-ink font-semibold">
              {selectedSymbol === "all" ? "semua symbol" : selectedSymbol}
            </span>
            <span>·</span>
            <span>Batas:</span>
            <span className="font-mono text-neg font-semibold num">&gt; {olderThanDays} hari</span>
          </div>

          <button
            type="button"
            onClick={() => setShowConfirmModal(true)}
            className="px-4 py-2 rounded-lg bg-neg/20 hover:bg-neg/30 text-neg ring-1 ring-neg/40 text-body font-semibold transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            <span>Prune candle lama</span>
          </button>
        </div>
      </div>

      {/* The one irreversible delete in the UI, so it gets the real dialog the
          rest of the app uses: Escape, focus trap, focus restored. */}
      {showConfirmModal && (
        <Modal
          label="Konfirmasi prune candle"
          width="w-[min(28rem,calc(100vw-2rem))]"
          onClose={() => !submitting && setShowConfirmModal(false)}
        >
          <div className="space-y-4">
            <div className="flex items-center gap-2.5 text-neg font-bold text-headline">
              <svg className="w-6 h-6 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <h2>Konfirmasi prune candle</h2>
            </div>

            <p className="text-body text-muted leading-relaxed">
              Hapus permanen bar M1 yang lebih tua dari{" "}
              <strong className="text-ink font-mono">{olderThanDays} hari</strong> untuk{" "}
              <strong className="text-ink font-mono">{targetLabel}</strong>. Tidak bisa dibatalkan setelah jalan.
            </p>

            {/* What survives the prune is reassurance, not a warning — rose
                here would say the opposite of what the sentence says. */}
            <div className="p-3 rounded-lg bg-white/5 border border-panel-border text-muted text-body">
              Riwayat trade, log rekonstruksi, dan cache PNG chart tetap utuh.
            </div>

            <div className="flex items-center justify-end gap-3 pt-3 border-t border-panel-border/50">
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-body font-medium transition-colors"
                onClick={() => setShowConfirmModal(false)}
                disabled={submitting}
              >
                Batal
              </button>
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-neg/25 hover:bg-neg/35 ring-1 ring-neg/45 text-neg text-body font-semibold transition-colors flex items-center gap-2"
                onClick={handlePrune}
                disabled={submitting}
              >
                {submitting ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      />
                    </svg>
                    <span>Menghapus…</span>
                  </>
                ) : (
                  <span>Konfirmasi & hapus</span>
                )}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export { PrunePanel };
