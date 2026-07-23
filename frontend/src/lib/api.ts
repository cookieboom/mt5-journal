import { useEffect, useState } from "react";

export function useApi<T>(path: string, intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(path);
        const body = await r.json();
        if (!alive) return;
        if (!r.ok) setError(body.error ?? `HTTP ${r.status}`);
        else { setData(body as T); setError(null); }
      } catch (e) {
        if (alive) setError(String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const id = intervalMs ? setInterval(load, intervalMs) : undefined;
    return () => { alive = false; if (id) clearInterval(id); };
  }, [path, intervalMs]);

  return { data, error, loading };
}

export async function postJson<T>(
  path: string,
  body: unknown,
): Promise<{ ok: boolean; data?: T; error?: string }> {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) return { ok: false, error: (j && j.error) ?? `HTTP ${r.status}` };
    return { ok: true, data: j as T };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}
