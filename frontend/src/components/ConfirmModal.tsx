import { PreviewResult } from "../lib/types";
import Modal from "./Modal";

export default function ConfirmModal({
  preview, submitting, error, onConfirm, onCancel,
}: {
  preview: PreviewResult;
  submitting: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal label="Konfirmasi perintah" width="w-[min(28rem,calc(100vw-2rem))]" onClose={onCancel}>
      <h2 className="text-headline font-bold mb-1">Konfirmasi perintah</h2>
      <p className="text-body text-muted mb-3">
        Perintah masuk antrean; <code>journal live</code> yang mengeksekusinya. Belum ada yang dikirim.
      </p>
      <div className="rounded-lg bg-white/5 p-3 text-title mb-3">{preview.intent}</div>
      {error && <div className="text-neg text-body mb-3">Ditolak: {error}</div>}
      <div className="flex justify-end gap-2">
        <button className="px-3 py-1.5 rounded bg-white/8 ring-1 ring-panel-border text-ink"
          onClick={onCancel} disabled={submitting}>Batal</button>
        <button className="px-3 py-1.5 rounded bg-cyan/20 ring-1 ring-cyan/45 text-ink font-semibold"
          onClick={onConfirm} disabled={submitting}>
          {submitting ? "Mengirim…" : "Konfirmasi & kirim"}
        </button>
      </div>
    </Modal>
  );
}
