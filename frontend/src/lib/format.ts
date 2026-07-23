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
  if (x === null || x === undefined) return "unknown";
  return String(x);
}
