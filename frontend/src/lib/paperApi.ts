// Typed fetch wrappers for /api/paper/*. The backend is authoritative for every
// fill, every margin figure and every refusal — this file only carries them.
import { postJson, patchJson } from "./api";
import type { PaperAccount, PaperAccountView, PaperPosition } from "./types";

export async function listAccounts(status?: "active" | "archived"): Promise<PaperAccount[]> {
  const r = await fetch(`/api/paper/accounts${status ? `?status=${status}` : ""}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as PaperAccount[];
}

export function createAccount(body: {
  name: string; initial_balance: number; leverage: number; stopout_pct: number;
}) {
  return postJson<PaperAccount>("/api/paper/accounts", body);
}

export function archiveAccount(id: number) {
  return postJson<PaperAccount>(`/api/paper/accounts/${id}/archive`, {});
}

export async function getAccount(id: number): Promise<PaperAccountView | null> {
  const r = await fetch(`/api/paper/accounts/${id}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error((await r.json()).error ?? `HTTP ${r.status}`);
  return (await r.json()) as PaperAccountView;
}

export function placeOrder(accountId: number, body: {
  symbol: string; direction: "buy" | "sell";
  kind?: "market" | "limit" | "stop";
  volume?: number | null; risk_pct?: number | null; price?: number | null;
  sl?: number; tp?: number; expires_msc?: number | null;
}) {
  return postJson<PaperPosition>(`/api/paper/accounts/${accountId}/orders`, body);
}

export function modifySltp(positionId: number, body: { sl?: number | null; tp?: number | null }) {
  return patchJson<PaperPosition>(`/api/paper/positions/${positionId}`, body);
}

export function closePosition(positionId: number, volume?: number) {
  return postJson<PaperPosition>(`/api/paper/positions/${positionId}/close`,
    volume === undefined ? {} : { volume });
}

export function reversePosition(positionId: number) {
  return postJson<PaperPosition>(`/api/paper/positions/${positionId}/reverse`, {});
}

export async function cancelPending(positionId: number): Promise<boolean> {
  const r = await fetch(`/api/paper/positions/${positionId}`, { method: "DELETE" });
  return r.ok;
}

export function closeAll(accountId: number) {
  return postJson<{ closed: number[]; cancelled: number[] }>(
    `/api/paper/accounts/${accountId}/close_all`, {});
}
