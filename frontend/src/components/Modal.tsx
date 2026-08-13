import { ReactNode, useEffect, useRef } from "react";

// The centred blocking layer, once. Two of the three call sites gate a command
// queued against the live account, so the dialog affordances are not optional:
// Escape, a focus trap, focus restored to whatever opened it, and the page
// behind it inert.
//
// All of that is `<dialog>.showModal()` — the platform already implements it,
// including the implicit role and aria-modal. A hand-rolled div would be a
// focus trap we maintain and a role we can forget; `Sheet` is the drawer
// equivalent of this file for the right-hand layer.
export default function Modal({
  label, width, onClose, children,
}: {
  label: string;
  width: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    // jsdom 29 ships the HTMLDialogElement interface without showModal, so the
    // open attribute is what makes the content queryable under test. The
    // browser always takes the first branch and gets the top layer with it.
    if (d.showModal) d.showModal();
    else d.setAttribute("open", "");
  }, []);

  return (
    <dialog
      ref={ref}
      aria-label={label}
      // Escape fires `cancel`; let the caller close so React state stays the
      // one source of truth for whether the dialog exists at all.
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      // In the top layer the backdrop is not a child, so a click that lands on
      // the dialog element itself is a click on the scrim.
      onClick={(e) => { if (e.target === ref.current) onClose(); }}
      className={`glass p-5 text-ink backdrop:bg-black/60
                  shadow-[0_12px_32px_rgba(0,0,0,0.45)] ${width}`}
    >
      {children}
    </dialog>
  );
}
