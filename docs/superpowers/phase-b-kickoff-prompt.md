# Chart Phase B — kickoff prompt (paste as the first message of a new session)

> Copy everything inside the fenced block below into a fresh Claude Code session
> in this repo. It is written to hand you full context with zero back-reference
> to the Phase A session.

```
Kita lanjut ke CHART PHASE B dari proyek mt5-journal: membangun HALAMAN CHART
INTERAKTIF ala TradingView di SPA React. Ini fase ke-2 dari 4 untuk segment
"chart". Fase A (fondasi data) SUDAH SELESAI dan menjadi PR #7.

MULAI DENGAN BRAINSTORMING. Ini pekerjaan UI kreatif — pakai skill
superpowers:brainstorming dulu untuk memaku detail (layout, perilaku overlay
posisi live, UX saat data belum ter-cache / saat `journal live` mati, isi panel
setting minimal), LALU superpowers:writing-plans, LALU
superpowers:subagent-driven-development untuk eksekusi. Pakai graphify untuk
orientasi kode (jalankan `graphify query "..."` sebelum membaca sumber). Boleh
banyak bertanya ke saya satu per satu.

== LANGKAH 0 (WAJIB, sebelum apa pun) ==
Cek status PR #7 (`gh pr view 7`). Fase B BERGANTUNG pada Fase A.
- Jika PR #7 SUDAH merged ke main → `git checkout main && git pull`, lalu
  branch baru dari main: `git checkout -b chart-phase-b-interactive-chart`.
- Jika PR #7 BELUM merged → branch Fase B dari tip Fase A:
  `git checkout chart-phase-a-candle-store && git pull && git checkout -b
  chart-phase-b-interactive-chart` (dan catat bahwa Fase A perlu di-merge dulu
  sebelum Fase B di-merge).
Jangan bekerja langsung di main.

== APA ITU PROYEK INI (ringkas) ==
Jurnal trading otomatis dari MetaTrader 5. Python 3.12 + sqlite3 (stdlib) +
FastAPI + typer di backend; React 18 SPA (Vite, TypeScript, tailwind,
react-router, recharts, vitest) di `frontend/`. SPA adalah SATU-SATUNYA UI,
disajikan FastAPI di `/` (Jinja sudah pensiun). Single-user, lokal, macOS.
BACA `CLAUDE.md` untuk aturan keras (rule 1–12) — khususnya:
- Rule 1 & M9 boundary: `web/` TIDAK PERNAH menyentuh bridge MT5. Hanya proses
  `journal live` yang pegang koneksi bridge.
- Rule 3: semua timestamp = epoch MILLISECOND integer, SERVER time (broker UTC,
  offset 0 terukur). Konversi ke WIB (UTC+7) HANYA saat display.
- Akun dalam USC (US cents); jangan cetak angka telanjang sebagai "$".
- Rule 4: NULL = tidak diketahui, 0 = "none set" (penting untuk SL/TP).

== APA YANG FASE A SUDAH SEDIAKAN (yang Fase B konsumsi) ==
Endpoint utama, read-only, TIDAK menyentuh bridge:
  GET /api/candles?symbol=XAUUSDc&timeframe=M5&from=<ms>&to=<ms>
Respons (selalu 200):
  {
    "symbol": "XAUUSDc", "timeframe": "M5",
    "candles": [ {"time_msc": <epoch ms>, "o":.., "h":.., "l":.., "c":.., "v":..}, ... ],
    "missing": [[lo_ms, hi_ms], ...],   // rentang yang BELUM ter-cache
    "pending": true|false               // true jika fill sudah/otomatis diantrikan
  }
- Timestamp tetap epoch-ms. lightweight-charts butuh UNIX DETIK → BAGI 1000 di
  klien saat memberi data ke chart. (Server time; label waktu ke WIB saat display.)
- MODEL FILL ON-DEMAND (penting untuk UX Fase B): kalau `missing` tidak kosong,
  backend sudah MENGANTRIKAN fetch ke tabel `candle_requests`; yang MEMENUHINYA
  adalah proses `journal live` (satu request per siklus). Jadi:
    * Klien harus POLL ulang endpoint yang sama sampai `missing` kosong / `pending`
      false, lalu berhenti.
    * Jika `journal live` TIDAK berjalan, antrian tak pernah terpenuhi → chart
      tetap menampilkan yang ter-cache saja. Fase B butuh UX untuk keadaan ini
      (indikator "memuat data…" + hint "jalankan `journal live`"), bukan spinner
      selamanya. (Diskusikan UX ini saat brainstorming.)
    * Bridge mati = `missing` tetap ada, tanpa error. Bukan kondisi gagal keras.
- Timeframe valid: M1, M5, M15, H1, H4, D1. Symbol yang ada: XAUUSDc, BTCUSDc,
  EURUSDc (hanya suffix "c").
Endpoint terkait yang sudah ada: GET /api/live (posisi terbuka + SL/TP dari tabel
`open_positions`, diisi `journal live`), GET /api/commands, GET /api/dashboard,
/api/report, /api/weekly, /api/trades, dsb. CLI baru: `journal candles-warm
<symbol> <tf> --from --to` (pra-isi cache) dan `journal candles-coverage`.

== LINGKUP FASE B (dari spec, konfirmasi & pertajam saat brainstorm) ==
Halaman chart interaktif baru di SPA:
- Chart candlestick pakai library lightweight-charts (TradingView, MIT). Ini
  DEPENDENCY FRONTEND BARU yang sudah disetujui — tambahkan ke frontend/package.json.
- Geser kiri/kanan + zoom (lazy-load: saat pan ke area baru, minta window
  from/to yang lebih lebar; endpoint sudah punya cap max_bars, jadi paginasi
  lewat rentang waktu).
- Ganti symbol & timeframe.
- Overlay POSISI LIVE + garis SL/TP (price lines) + marker entry, dari /api/live.
  Hanya relevan saat melihat symbol & waktu "sekarang". (SL/TP: hormati rule 4 —
  0.0 = tak diset, NULL = tak diketahui; jangan gambar garis untuk itu.)
- Panel INFO symbol (harga terkini, dsb).
- Setting chart DASAR (mis. warna background, tema) — panel setting LENGKAP +
  persist preferensi adalah Fase C, jadi jaga tetap minimal di B.
Registrasi halaman: tambah page di `frontend/src/pages/`, route di router (lihat
`App.tsx`/`main.tsx`), dan entri sidebar (`components/Sidebar.tsx` / `AppShell.tsx`).
Pola data: `useApi()` di `frontend/src/lib/api.ts`, tipe di `lib/types.ts`.
Chart page ini HANYA display (+ nanti training di Fase D). TIDAK menempatkan
order asli — trading nyata sudah ada di M9 /live.

== ITEM TERTUNDA DARI FASE A (selesaikan di awal Fase B, sebagai task cleanup) ==
Review Fase A menyisakan beberapa hal non-blocking. Jadikan 1-2 task kecil di
awal plan Fase B (mis. migration 004 + tes), SEBELUM kerja UI:
1. Migration 004 (additive): tambah CHECK enum pada `candle_requests.status`
   (IN ('pending','claimed','done','failed')) — meniru pola `trade_commands`
   yang sudah pakai CHECK. Mirror ke schema.sql, bump SCHEMA_VERSION 3→4,
   update test_migrations.
2. Guard rentang terbalik di `candles_store.record_coverage` (tolak/skip
   from_ms > to_ms) — `missing_ranges` sudah punya guard serupa; samakan.
3. Tes yang MEMBUKTIKAN `ingest/candles.sync_candles` (jalur ingest lama) kini
   MENGISI `candle_coverage` (kontrak lintas-produser; kini hanya terbukti lewat
   inspeksi kode). Tes kecil di tests/test_candles.py.
4. Kosmetik: `cli.py` banner `candles-warm` adalah f-string tanpa interpolasi
   (F541) → jadikan string biasa.
5. (Opsional) Tes wiring untuk /api/candles: alias Query(alias="from"/"to") dan
   400-vs-422 tidak teruji end-to-end (test_web.py sengaja tanpa httpx). Bisa
   assert metadata alias di route object secara ringan, tanpa nambah dependency.
6. (Opsional) Tes cap `max_bars` di candles_payload (jalur truncation belum
   diuji). CATATAN: bug agregasi batas-bucket SUDAH diperbaiki di Fase A
   (bucket-aligned M1 read) — jangan ulang.
CATATAN kecil untuk UX: `pending` bisa `true` walau bar sudah tersaji via
agregasi M1 (karena coverage TF-native belum ada) — polling tetap jalan; itu
tidak berbahaya, cuma pertimbangkan agar UI tidak terlihat "loading" padahal
data sudah tampil.

== KONVENSI KERJA & GOTCHAS ==
- Test: `uv run pytest` (backend, harus hijau sebelum commit), `npm --prefix
  frontend test` / vitest (frontend), `npm --prefix frontend run build` (build
  harus 0 error). Definition of done: tes hijau + tempel output pytest + `uv run
  journal rebuild` masih sukses.
- Jangan tambah dependency backend tanpa izin. Dependency FRONTEND baru untuk
  Fase B = lightweight-charts saja (sudah disetujui); tanya kalau butuh lain.
- Setelah ubah kode: `graphify update .`
- GOTCHA: `journal serve` MENGABAIKAN env var JOURNAL_DB — untuk cek manual/live,
  WAJIB `uv run journal serve --db "<path>"`.
- M9 live-bridge smoke test masih menunggu run manusia (lihat docs/HANDOFF.md);
  Fase B bisa dikembangkan penuh dengan `adapter/fake.py` tanpa bridge hidup,
  tapi menguji overlay posisi live end-to-end butuh `journal live` + bridge.
- Commit message footer:
    Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  PR body footer:
    🤖 Generated with [Claude Code](https://claude.com/claude-code)
- Sesi ini berjalan sebagai background job, kerja IN-PLACE (bukan worktree)
  kecuali saya minta worktree.

== REFERENSI ==
- Spec Fase A: docs/superpowers/specs/2026-07-24-chart-phase-a-candle-store-design.md
  (bagian "Konteks: fitur lebih besar (4 fase)" merangkum roadmap penuh).
- Plan Fase A: docs/superpowers/plans/2026-07-24-chart-phase-a-candle-store.md
- Memory index proyek sudah mencatat roadmap 4-fase (chart-segment-phases).

Konfirmasikan dulu pemahamanmu + status PR #7 + basis branch, lalu MULAI
brainstorming Fase B dengan bertanya satu per satu.
```
