import { useState } from "react";
import { clearCache, vacuumDb, rebuildTrades } from "../../lib/storageApi";
import { formatBytes } from "./DiskStatsCard";
import Modal from "../Modal";

export interface MaintenancePanelProps {
  onSuccess?: () => void;
}

type ActionType = "cache" | "vacuum" | "rebuild" | null;

export default function MaintenancePanel({ onSuccess }: MaintenancePanelProps) {
  const [activeAction, setActiveAction] = useState<ActionType>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const handleExecute = async () => {
    if (!activeAction) return;

    setSubmitting(true);
    setFeedback(null);

    try {
      if (activeAction === "cache") {
        const res = await clearCache();
        if (!res.ok || !res.data) {
          throw new Error(res.error ?? "Gagal bersihkan cache");
        }
        setFeedback({
          type: "success",
          message: `Cache dibersihkan · ${res.data.cleared_files.toLocaleString()} file · ${formatBytes(res.data.freed_bytes)} dibebaskan.`,
        });
      } else if (activeAction === "vacuum") {
        const res = await vacuumDb();
        if (!res.ok || !res.data) {
          throw new Error(res.error ?? "Gagal vacuum database");
        }
        setFeedback({
          type: "success",
          message: `VACUUM selesai · ukuran database sekarang ${formatBytes(res.data.db_size_after)}.`,
        });
      } else if (activeAction === "rebuild") {
        const res = await rebuildTrades();
        if (!res.ok || !res.data) {
          throw new Error(res.error ?? "Gagal rebuild trades");
        }
        setFeedback({
          type: "success",
          message: `Rebuild selesai · ${res.data.trades_rebuilt.toLocaleString()} trade dan auto-tag disusun ulang.`,
        });
      }

      setActiveAction(null);
      if (onSuccess) {
        onSuccess();
      }
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err?.message || `Gagal menjalankan ${activeAction}.`,
      });
      setActiveAction(null);
    } finally {
      setSubmitting(false);
    }
  };

  const modalDetails: Record<
    Exclude<ActionType, null>,
    { title: string; body: string; buttonText: string; buttonClass: string }
  > = {
    cache: {
      title: "Bersihkan cache PNG & report",
      body: "Hapus semua PNG chart dan HTML report yang di-cache. Trade dan candle tidak ikut terhapus; cache dirender ulang saat diminta lagi.",
      buttonText: "Bersihkan cache",
      buttonClass: "bg-cyan/20 ring-1 ring-cyan/45 hover:bg-cyan/30 text-ink font-semibold",
    },
    vacuum: {
      title: "Vacuum & optimalkan database",
      body: "Jalankan SQLite VACUUM: halaman database dirapikan dan ruang dari record yang sudah dihapus dikembalikan. Database terkunci sebentar selama proses.",
      buttonText: "Jalankan VACUUM",
      buttonClass: "bg-cyan/20 ring-1 ring-cyan/45 hover:bg-cyan/30 text-ink font-semibold",
    },
    rebuild: {
      title: "Rebuild trades & auto-tag",
      body: "Susun ulang semua trade dari deal mentah MT5. MAE/MFE, metrik performa, dan auto-tag dihitung ulang untuk seluruh trade.",
      buttonText: "Rebuild trades",
      buttonClass: "bg-cyan/20 ring-1 ring-cyan/45 hover:bg-cyan/30 text-ink font-semibold",
    },
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

      {/* Maintenance Action Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Card 1: Clear Cache */}
        <div className="glass p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 text-ink font-semibold mb-2">
              {/* Muted, like the panel titles elsewhere: none of these three is
                  a now-value, a user mark, or an outcome, so none of them gets
                  cyan, violet or green (DESIGN.md § Colors). */}
              <svg className="w-5 h-5 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
              <span>Cache PNG & report</span>
            </div>
            <p className="text-body text-muted leading-relaxed mb-4">
              Hapus PNG trade dan ringkasan mingguan yang sudah dirender untuk mengosongkan disk.
            </p>
          </div>
          <button
            onClick={() => setActiveAction("cache")}
            className="w-full py-2 px-3 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-body font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Bersihkan cache PNG & report
          </button>
        </div>

        {/* Card 2: Vacuum DB */}
        <div className="glass p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 text-ink font-semibold mb-2">
              <svg className="w-5 h-5 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              <span>Database SQLite</span>
            </div>
            <p className="text-body text-muted leading-relaxed mb-4">
              Jalankan SQLite VACUUM untuk merapikan penyimpanan dan menarik kembali ruang dari record yang dihapus.
            </p>
          </div>
          <button
            onClick={() => setActiveAction("vacuum")}
            className="w-full py-2 px-3 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-body font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Vacuum & optimalkan DB
          </button>
        </div>

        {/* Card 3: Rebuild Trades */}
        <div className="glass p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 text-ink font-semibold mb-2">
              <svg className="w-5 h-5 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              <span>Trade & auto-tag</span>
            </div>
            <p className="text-body text-muted leading-relaxed mb-4">
              Baca ulang deal mentah MT5, lipat kembali posisi jadi trade, hitung ulang MAE/MFE dan auto-tag.
            </p>
          </div>
          <button
            onClick={() => setActiveAction("rebuild")}
            className="w-full py-2 px-3 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-body font-semibold transition-colors flex items-center justify-center gap-1.5"
          >
            Rebuild trade & auto-tag
          </button>
        </div>
      </div>

      {/* These three gate a write against the store — one of them rewrites every
          trade row. That is exactly the case the shared Modal exists for: a real
          <dialog>, so Escape closes it, focus is trapped and restored, and the
          page behind it is inert. */}
      {activeAction && (
        <Modal
          label={modalDetails[activeAction].title}
          width="w-[min(28rem,calc(100vw-2rem))]"
          onClose={() => !submitting && setActiveAction(null)}
        >
          <div className="space-y-4">
            <h2 className="text-headline font-bold text-ink">
              {modalDetails[activeAction].title}
            </h2>
            <p className="text-body text-muted leading-relaxed">
              {modalDetails[activeAction].body}
            </p>

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-panel-border/50">
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 ring-1 ring-panel-border text-ink text-body font-medium transition-colors"
                onClick={() => setActiveAction(null)}
                disabled={submitting}
              >
                Batal
              </button>
              <button
                type="button"
                className={`px-4 py-2 rounded-lg text-body transition-colors flex items-center gap-2 ${modalDetails[activeAction].buttonClass}`}
                onClick={handleExecute}
                disabled={submitting}
              >
                {submitting ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      />
                    </svg>
                    <span>Menjalankan…</span>
                  </>
                ) : (
                  modalDetails[activeAction].buttonText
                )}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export { MaintenancePanel };
