import { useRef, useState } from "react";
import { postJson } from "../lib/api";
import type { ActionKind, CommandBody, PreviewResult } from "../lib/types";

// Two-step live trade command: preview writes nothing (server re-validates),
// confirm is the only write. Extracted from Live.tsx so Chart.tsx (SL/TP
// drag) can reuse the exact same safety flow instead of duplicating it.
export function useLiveCommand() {
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [pending, setPending] = useState<
    { action: ActionKind; body: CommandBody; position_id: number | null } | null
  >(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const enqueuing = useRef(false);

  // An `open` has no position yet, so the URL is keyed on the action alone.
  // Everything else — preview writes nothing, confirm is the only write — is
  // unchanged, which is the point: an open gets exactly the same two-step
  // confirmation an SL/TP edit does.
  const urlFor = (position_id: number | null, action: ActionKind, suffix = "") =>
    position_id === null
      ? `/api/live/${action}${suffix}`
      : `/api/live/${position_id}/${action}${suffix}`;

  const request = async (
    position_id: number | null, action: ActionKind, body: CommandBody,
  ) => {
    setError(null);
    const r = await postJson<PreviewResult>(urlFor(position_id, action, "/preview"), body);
    if (!r.ok) { setToast(null); setError(r.error ?? "gagal"); setPreview(null); return; }
    setPending({ action, body, position_id });
    setPreview(r.data ?? null);
  };

  const confirm = async () => {
    if (!preview || !pending) return;
    if (enqueuing.current) return;
    enqueuing.current = true;
    setSubmitting(true);
    const r = await postJson<{ ok: boolean; command_id: number }>(
      urlFor(pending.position_id, pending.action), pending.body);
    setSubmitting(false);
    if (!r.ok) { setError(r.error ?? "gagal"); enqueuing.current = false; return; }
    setPreview(null); setPending(null); setError(null);
    setToast(`Perintah #${r.data?.command_id} masuk antrean — journal live akan mengeksekusi.`);
    enqueuing.current = false;
  };

  const cancel = () => { setPreview(null); setPending(null); setError(null); enqueuing.current = false; };

  return { preview, error, submitting, toast, request, confirm, cancel };
}
