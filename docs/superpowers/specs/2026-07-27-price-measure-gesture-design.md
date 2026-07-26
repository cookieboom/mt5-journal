# Spec B — Gestur ukur harga (price-measurement gesture)

Design doc, 2026-07-27. Ide #4 dari `docs/ROADMAP-trade-chart-features.md`.
Digarap setelah Spec A selesai. Urutan roadmap: A → **B** → C.

## Ringkasan

Gestur **double-click lalu tahan** pada chart untuk mengukur selisih harga
antara titik jangkar (anchor) dan posisi kursor, seperti alat ukur TradingView.
Readout diperbarui live selama menahan, lalu **menetap** setelah dilepas sampai
pengguna menekan `Esc` atau klik biasa.

Fitur ini **murni front-end**. Tidak ada perubahan backend, tidak ada dependency
baru. Dibangun di dalam `CandleChart.tsx` sehingga **berlaku untuk semua chart**
yang memakai komponen itu (chart utama, TradeView Spec A, dan monitor realtime
Spec C nanti) secara otomatis.

## Keputusan yang sudah disepakati (2026-07-27)

| Pertanyaan | Keputusan |
|---|---|
| Isi readout | **Δharga + % + waktu/bar** (semua murni FE, tanpa backend) |
| Snap anchor/kursor | **Bebas / exact** — harga persis di posisi kursor, tanpa snap ke OHLC |
| Akhir gestur (saat lepas) | **Menetap** sampai `Esc` atau klik lain |
| Cakupan chart | **Semua** chart via `CandleChart` |
| Pendekatan render | **Pendekatan 1** — overlay DOM/SVG di atas chart (bukan primitive canvas) |

`ticks/pips` **tidak** ditampilkan: butuh `tick_size` yang saat ini tidak ada di
payload FE (`CandlesResponse`), dan menambahkannya akan melanggar batasan
"FE-only / Kecil". Δharga, %, dan waktu/bar semuanya bisa dihitung murni dari dua
titik (harga, waktu) tanpa data tambahan.

## Arsitektur

Tiga bagian, dua di antaranya murni & dapat diunit-test tanpa chart:

```
frontend/src/lib/measure.ts          <- helper MURNI: metrik + reducer state machine
frontend/src/components/MeasureOverlay.tsx  <- SVG presentational (garis/box/marker/label)
frontend/src/components/CandleChart.tsx     <- wiring: pointer events + proyeksi koordinat
```

Prinsip: semua logika keputusan (transisi state, perhitungan metrik) hidup di
`measure.ts` sebagai fungsi murni. `CandleChart` hanya menerjemahkan event DOM →
koordinat data, memanggil reducer, dan memproyeksikan koordinat data → piksel
untuk `MeasureOverlay`. Wiring di `CandleChart` dijaga tipis.

### Kenapa Pendekatan 1 (DOM/SVG), bukan primitive canvas

Endpoint disimpan sebagai **koordinat data** `{ price, barTimeMs, logical }`, lalu
diproyeksikan ke piksel via API lightweight-charts (`series.priceToCoordinate`,
`timeScale().timeToCoordinate` / `logicalToCoordinate`). SVG absolute-position
menutupi pane dengan `pointer-events: none` agar tidak menghalangi interaksi
chart. Styling garis/box/label trivial dengan SVG/CSS. Series-primitive v5 akan
menggambar di canvas & auto-ikut viewport, tapi menggambar kotak-teks berlatar di
canvas ribet dan berat untuk fitur "Kecil". Trade-off yang diterima: kita
re-proyeksi manual saat viewport berubah (hook ke subscription — sederhana).

## State machine

Tiga state, disimpan sebagai **reducer murni** di `measure.ts`:

```
idle  --(double-click-hold terdeteksi)-->  measuring
measuring  --(pointermove)-->  measuring        (update titik kursor)
measuring  --(pointerup)-->  frozen             (pertahankan anchor + kursor terakhir)
frozen  --(Esc | klik biasa)-->  idle
frozen | measuring  --(double-click-hold baru)-->  measuring   (ganti ukuran lama)
* (any)  --(ganti symbol/timeframe/chartType)-->  idle          (auto-clear)
```

Tipe (indikatif):

```ts
type Point = { price: number; barTimeMs: number; logical: number };
type MeasureState =
  | { phase: "idle" }
  | { phase: "measuring"; anchor: Point; cursor: Point }
  | { phase: "frozen"; anchor: Point; cursor: Point };

type MeasureEvent =
  | { t: "start"; anchor: Point }         // double-click-hold terdeteksi
  | { t: "move"; cursor: Point }          // hanya berlaku saat measuring
  | { t: "release" }                      // pointerup saat measuring
  | { t: "clear" };                       // Esc / klik biasa / ganti symbol|tf|type

function measureReducer(s: MeasureState, e: MeasureEvent): MeasureState;
```

Reducer murni & lengkap: `move`/`release` di state `idle` adalah no-op; `start`
dari `frozen` mengganti dengan ukuran baru; `clear` selalu → `idle`.

### Deteksi "double-click lalu tahan"

Wiring di `CandleChart` menyimpan `lastUpMs` dan `lastUpXY` dari `pointerup`
terakhir. Saat `pointerdown`:

- Jika `now - lastUpMs <= DBLCLICK_MS` (≈ 350 ms) **dan** jarak ke `lastUpXY`
  `< DBLCLICK_PX` (≈ 5 px) → ini tekan kedua dari sebuah double-click yang
  ditahan. Kirim event `start` dengan anchor = koordinat data di posisi pointer,
  dan **matikan `handleScroll` + `handleScale`** chart agar drag tidak nge-pan.
- Selain itu → tekan biasa. Jika state `frozen`, kirim `clear`.

Saat `measuring`, `pointermove` → event `move`. `pointerup` → event `release` dan
**pulihkan `handleScroll`/`handleScale`**. `Esc` (keydown) → `clear`.

Konstanta `DBLCLICK_MS`, `DBLCLICK_PX` di `measure.ts` (bisa diuji).

## Metrik (fungsi murni di `measure.ts`)

Dari `anchor` dan `cursor`:

- `dPrice = cursor.price - anchor.price` — harga **exact** di kursor (tanpa snap).
- `pct = anchor.price !== 0 ? dPrice / anchor.price * 100 : null` — guard bagi-nol
  → tampilkan `—`.
- `bars = Math.round(Math.abs(cursor.logical - anchor.logical))`.
- `dTimeMs = Math.abs(cursor.barTimeMs - anchor.barTimeMs)` — dari timestamp bar
  **asli** (gap-aware; **bukan** `bars × tf` yang salah menyeberangi penutupan
  pasar). Diformat "2h 15m" / "45m" / "3d 4h" via helper `fmtDuration`.
- Arah/warna: `dPrice >= 0` → warna `up` tema (hijau), else `down` (merah).

Catatan: harga **exact** di kursor, tapi komponen **waktu/bar** memakai bar yang
berada di bawah pointer (tidak ada wall-clock sub-bar yang bermakna, dan ini yang
membuat Δwaktu gap-aware & benar). `logical` diperoleh dari
`timeScale().coordinateToLogical(x)` (pecahan), `barTimeMs` dari candle pada
indeks bar terdekat.

Output metrik sebagai objek murni; format string ditangani presentational.

## Render — `MeasureOverlay.tsx` (SVG)

Menerima endpoint yang **sudah diproyeksikan** ke piksel `{ x, y }` untuk anchor &
cursor plus objek metrik, lalu menggambar:

- **Box terarsir** persegi antara dua titik (gaya ukur TradingView) — isian
  semi-transparan warna arah.
- **Garis** penghubung anchor→cursor.
- **Dua marker** kecil (lingkaran) di tiap endpoint.
- **Label box** metrik (`Δharga`, `%`, `⏱ Δwaktu · N bars`) dekat endpoint kursor,
  dengan background agar terbaca di atas candle.

`<svg>` absolute menutupi pane, `pointer-events: none`. Proyeksi endpoint dihitung
di `CandleChart` (butuh akses `series`/`timeScale`) dan diberikan sebagai props.

### Re-proyeksi & off-screen

Endpoint disimpan sebagai koordinat data, jadi harus diproyeksi ulang saat
viewport berubah. `CandleChart` memproyeksi ulang saat: `pointermove` (selama
measuring) dan pada `subscribeVisibleTimeRangeChange` / perubahan ukuran (untuk
state `frozen`). Jika `priceToCoordinate`/`timeToCoordinate` mengembalikan `null`
(endpoint keluar layar) → overlay disembunyikan **halus** tapi **state tetap**;
muncul kembali saat di-scroll balik ke view.

## Auto-clear

Ukuran dihapus (`clear` → `idle`) saat: **ganti symbol**, **ganti timeframe**,
atau **chart-type recreate** — karena data/koordinat mungkin tak lagi selaras.
Di-hook ke effect yang sama yang sudah menangani perubahan itu di `CandleChart`.

## Edge cases

- **Bagi-nol** (`anchor.price === 0`, mustahil untuk harga nyata) → `pct = null` →
  tampilkan `—`.
- **Endpoint keluar layar** → overlay hidden, state tetap (lihat di atas).
- **Satu ukuran saja**: double-click-hold baru mengganti yang lama.
- **Replay / TradeView / live**: berlaku sama; hanya chart biasa, tidak ada
  perlakuan khusus.
- **Crosshair** dibiarkan bergerak normal selama measuring (tidak disembunyikan).
- **Klik biasa saat idle** = no-op (tidak memulai apa pun).

## Testing (vitest)

Karena logika chart-agnostik hidup di `measure.ts`, target uji:

- **Metrik**: `dPrice`, `pct` (termasuk guard bagi-nol → `null`), `bars`, `dTimeMs`,
  arah/warna. Termasuk kasus cursor < anchor (nilai negatif) dan `abs`.
- **`fmtDuration`**: menit, jam+menit, hari+jam.
- **`measureReducer`**: setiap transisi — `start` dari idle/frozen (ganti),
  `move`/`release` no-op di idle, `release` idle→(tetap idle), `clear` selalu idle.
- **Deteksi double-click** (fungsi murni ambang): dalam/luar `DBLCLICK_MS`, dalam/
  luar `DBLCLICK_PX`.

Wiring pointer/koordinat di `CandleChart` tidak diuji unit (butuh chart nyata &
jsdom tak punya geometri); dijaga tipis dan diverifikasi manual di browser (bagian
"Definition of done" proyek: pass test + rebuild OK + visual pass manusia).

## Yang TIDAK dikerjakan (YAGNI)

- Tidak ada ticks/pips (butuh backend).
- Tidak ada penyimpanan ukuran lintas sesi / persist ke DB (murni ephemeral FE).
- Tidak ada multi-ukuran simultan.
- Tidak ada penggambaran anotasi permanen (itu ranah fitur annotate terpisah).
- Tidak ada perubahan `app_prefs` / setting (gestur selalu aktif; tak ada toggle).

## Definition of done

- `frontend`: `vitest` hijau (termasuk test baru `measure.test.ts`), `tsc`/build 0
  error, lint bersih.
- Backend tak tersentuh; `uv run pytest` tetap hijau; `journal rebuild` tetap OK.
- Update kolom Status di `docs/ROADMAP-trade-chart-features.md` (Spec B → selesai).
- `graphify update .`.
- **Pending manusia**: visual pass di browser (chart utama + TradeView), verifikasi
  gestur double-click-hold, menetap, `Esc`/klik-hapus, dan re-proyeksi saat
  pan/zoom.
