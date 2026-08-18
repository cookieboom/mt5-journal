import { useState } from "react";
import Modal from "./Modal";
import { money } from "../lib/format";
import { archiveAccount, createAccount } from "../lib/paperApi";
import type { PaperAccount } from "../lib/types";

const CENTS_PER_DOLLAR = 100;

export default function PaperAccountDialog(props: {
  accounts: PaperAccount[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCreated: (account: PaperAccount) => void;
  onArchived: (id: number) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [balance, setBalance] = useState("1000000");
  const [leverage, setLeverage] = useState("500");
  const [stopout, setStopout] = useState("20");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);

  const dollars = Number(balance) / CENTS_PER_DOLLAR;

  async function create() {
    setBusy(true);
    setError(null);
    const r = await createAccount({
      name, initial_balance: Number(balance), leverage: Number(leverage),
      stopout_pct: Number(stopout),
    });
    setBusy(false);
    if (!r.ok || !r.data) { setError(r.error ?? "Gagal membuat akun."); return; }
    setName("");
    props.onCreated(r.data);
  }

  async function archive(id: number) {
    setBusy(true);
    setError(null);
    const r = await archiveAccount(id);
    setBusy(false);
    setConfirming(null);
    if (!r.ok) { setError(r.error ?? "Gagal mengarsipkan akun."); return; }
    props.onArchived(id);
  }

  return (
    <Modal label="Akun paper" width="w-[min(32rem,calc(100vw-2rem))]" onClose={props.onClose}>
      <h2 className="text-headline font-bold mb-1">Akun paper</h2>
      <p className="text-body text-muted mb-3">
        Akun virtual. Tidak ada order yang dikirim ke broker — semua isi
        <code> paper_*</code>, terpisah dari riwayat asli.
      </p>

      <div className="space-y-1 mb-4">
        {props.accounts.length === 0 && (
          <div className="text-body text-muted">Belum ada akun paper.</div>
        )}
        {props.accounts.map((a) => (
          <div key={a.id} className="flex items-center justify-between gap-2 text-body">
            <button className={`glass px-2 py-1 flex-1 text-left ${
                      a.id === props.selectedId ? "text-ink" : "text-muted"}`}
                    onClick={() => { props.onSelect(a.id); props.onClose(); }}>
              {a.name} · {money(a.balance, "USC")} · 1:{a.leverage}
              {a.status === "archived" && " · arsip"}
            </button>
            {a.status === "active" && (
              confirming === a.id ? (
                <span className="flex items-center gap-1 text-caption">
                  <span className="text-muted">Arsipkan? Riwayatnya tetap ada.</span>
                  <button className="glass px-2 py-0.5 text-neg" disabled={busy}
                          onClick={() => void archive(a.id)}>Ya</button>
                  <button className="glass px-2 py-0.5" onClick={() => setConfirming(null)}>
                    Batal
                  </button>
                </span>
              ) : (
                <button className="glass px-2 py-0.5 text-caption text-muted"
                        onClick={() => setConfirming(a.id)}>Arsipkan</button>
              )
            )}
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <div className="font-semibold text-body">Akun baru</div>
        <label className="flex flex-col gap-0.5">
          <span className="text-caption text-muted">nama</span>
          <input aria-label="name" value={name} onChange={(e) => setName(e.target.value)}
                 className="glass px-2 py-1 text-body" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-caption text-muted">balance awal (USC)</span>
          <input aria-label="initial-balance" value={balance} inputMode="decimal"
                 onChange={(e) => setBalance(e.target.value)}
                 className="glass px-2 py-1 text-body" />
          {/* This is the one screen where a USC figure is typed from scratch, so
              the unit cannot be implied — the account currency is cents. */}
          <span className="text-caption text-muted">
            {money(Number(balance) || 0, "USC")} ≈ ${dollars.toLocaleString("en-US")}
          </span>
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-caption text-muted">leverage</span>
            <input aria-label="leverage" value={leverage} inputMode="numeric"
                   onChange={(e) => setLeverage(e.target.value)}
                   className="glass px-2 py-1 text-body" />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-caption text-muted">stop-out %</span>
            <input aria-label="stopout-pct" value={stopout} inputMode="decimal"
                   onChange={(e) => setStopout(e.target.value)}
                   className="glass px-2 py-1 text-body" />
          </label>
        </div>
        <button className="glass px-3 py-1 text-body font-semibold disabled:opacity-40"
                disabled={busy || !name.trim()} onClick={() => void create()}>
          Buat akun
        </button>
      </div>

      {error && <div role="alert" className="text-neg text-caption mt-2">{error}</div>}
    </Modal>
  );
}
