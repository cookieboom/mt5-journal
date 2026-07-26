# Roadmap — Trade & Chart Feature Batch (2026-07-26)

Peta kerja lintas-sesi untuk 4 ide yang dirancang 2026-07-26. Baca ini dulu
saat pindah sesi: menentukan **mana yang dikerjakan berikutnya** dan **apa isinya**.

**Urutan garap yang disepakati: Spec A → Spec B → Spec C** (mudah/risiko-rendah
dulu, realtime terakhir saat pola chart sudah mapan).

## Pemetaan singkat + estimasi kompleksitas

| # ide | Nama | Ukuran | Menyentuh | Spec | Status |
|-------|------|--------|-----------|------|--------|
| 2 + 3 | **Spec A** — Setting render PNG + Viewer trade interaktif | Kecil + Sedang | `render/chart.py`, `app_prefs`, `TradeDetail.tsx`, route baru `/trades/:id/view`, reuse engine chart + kursor replay + `annotate` | `docs/superpowers/specs/2026-07-26-trade-png-settings-and-viewer-design.md` | **Design approved; plan sedang ditulis** |
| 4 | **Spec B** — Gestur ukur harga (double-click + hold → jarak harga live) | Kecil | interaksi chart FE saja (lightweight-charts); berlaku untuk semua chart termasuk viewer #3 & monitor #1 | *belum ditulis* | Belum dibrainstorm |
| 1 | **Spec C** — Monitor simbol realtime | **Besar** | candle store baru (strategi simpan forming-bar), backend stream, polling FE + interval di setting, isu storage 1-bar-banyak-update | *belum ditulis* | Belum dibrainstorm |

## Isi tiap spec (ringkas)

### Spec A — Setting render PNG (#2) + Viewer trade (#3)
- **#2 PNG:** key `trade_png` di `app_prefs` (global default). Knob: tema warna,
  jumlah bar context (clamp 5–120), override TF, toggle overlay (SL/TP, marker,
  volume, grid). `RenderOpts` di `render_trade()`. Cache PNG di-rekey via
  signature setting (aturan 6). Panel kecil di `TradeDetail.tsx`.
- **#3 Viewer:** route `/trades/:id/view`, komponen sendiri, reuse engine chart +
  candle loader (`candle_requests`, web tak sentuh bridge). Prev/next **ikut
  filter daftar Trades** (deep-linkable via query-string) + keyboard ←/→. Overlay
  marker entry/exit + garis SL/TP. **Step = playback reveal opsional** (kursor
  replay saja, BUKAN evaluator — trade sudah selesai). Panel kanan: R-multiple
  (diutamakan), net_profit (USC), MAE/MFE_r, entry/exit, SL/TP (`—` bila null),
  durasi, waktu (WIB), sesi, symbol, auto-tag read-only. **Editable:** tag manual
  + anotasi via API `annotate`.

### Spec B — Gestur ukur harga (#4) *(brainstorm dulu saat gilirannya)*
- Double-click lalu tahan → ukur selisih harga dari titik double-click ke posisi
  cursor saat hold; info selisih terus diupdate selama hold. FE-only overlay di
  lightweight-charts. Pertanyaan yang belum digali: tampilkan juga jarak dalam
  pip/tick & % & waktu? snap ke OHLC? berlaku di chart mana saja?

### Spec C — Monitor simbol realtime (#1) *(brainstorm dulu saat gilirannya)*
- Chart update tiap N detik (N di setting) ambil data terbaru untuk update bar.
- **Isu inti storage:** normal 1 bar disimpan sekali; realtime 1 bar bisa update
  berkali-kali. Perlu strategi terbaik (mis. hanya forming-bar in-memory/`live.db`
  yang boleh sering di-overwrite, commit ke `candles` hanya saat bar tertutup;
  jaga agar `candles` tetap append-of-closed-bars & rebuildable). Pertanyaan yang
  belum digali: sumber data (bridge via antrian existing vs jalur baru), simbol
  mana, integrasi dengan `journal live`, batas rate, tampilan UI.

## Cara melanjutkan di sesi baru
1. Cek tabel Status di atas → ambil spec paling awal yang belum selesai.
2. Kalau specnya sudah ada → buka file spec-nya, lalu skill `writing-plans` /
   `executing-plans`. Kalau belum ada → mulai dari skill `brainstorming`.
3. Selesai satu spec: update kolom Status, jalankan `graphify update .`.
