// Client mirror of web/format.py. Money always carries its currency (USC);
// null is "n/a", never 0 (rule 4). *_msc are broker SERVER time; WIB = UTC+7 at
// display only (rule 3). R is unit-free.

export function money(
  x: number | null,
  ccy: string,
  opts: { sign?: boolean } = {},
): string {
  if (x === null || x === undefined) return "n/a";
  const s = x.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: opts.sign ? "always" : "auto",
  });
  return `${s} ${ccy}`.trim();
}

export function rmult(x: number | null): string {
  return x === null || x === undefined ? "n/a" : `${x.toFixed(2)}R`;
}

export function pct(x: number | null): string {
  return x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(1)}%`;
}

export function wib(serverMsc: number | null, offsetS = 0): string {
  if (serverMsc === null || serverMsc === undefined) return "—";
  // true UTC = server - offset; then shift +7h for WIB and read UTC fields.
  const wibMs = serverMsc - offsetS * 1000 + 7 * 3600 * 1000;
  const d = new Date(wibMs);
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())} WIB`
  );
}

export function isGated(n: number, avg: number | null): boolean {
  return (avg === null || avg === undefined) && n < 20;
}

export function price(x: number | null): string {
  // rule 4: null = unknown, never 0. A genuine 0.0 ("none set") shows as "0".
  // Deliberately shows FULL precision (String(x)), NOT web/format.py's `%g`
  // 6-significant-figure truncation — a trading journal must not drop a price
  // digit (XAUUSDc tick size is 0.001, so 4010.123 must stay 4010.123). This is
  // an intentional, approved divergence from the legacy Jinja formatter.
  if (x === null || x === undefined) return "unknown";
  return String(x);
}

export function dur(seconds: number | null): string {
  // Mirrors web/format.py:dur. null = unknown → "—".
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s ? `${m}m${String(s).padStart(2, "0")}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h${String(mm).padStart(2, "0")}m`;
}
