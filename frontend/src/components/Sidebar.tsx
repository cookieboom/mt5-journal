import { useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";

export const LINKS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/live", label: "Live" },
  { to: "/chart", label: "Chart" },
  { to: "/trades", label: "Trades" },
  { to: "/report", label: "Report" },
  { to: "/weekly", label: "Weekly" },
  { to: "/commands", label: "Commands" },
  { to: "/storage", label: "Storage" },
  { to: "/lab", label: "Lab" },
];

// Same item in both containers — the rail packs it vertically, the bar packs it
// horizontally at a 44px touch height. Only the box changes; the active
// treatment (the one place the two accents mix) does not.
const item = (box: string) => ({ isActive }: { isActive: boolean }) =>
  box + " rounded-lg text-[13px] transition " +
  (isActive
    ? "text-white bg-gradient-to-r from-violet/25 to-cyan/5 ring-1 ring-inset ring-violet/35"
    : "text-muted hover:text-ink");

export default function Sidebar() {
  return (
    <nav
      aria-label="Navigasi utama"
      className="w-[186px] shrink-0 border-r border-panel-border p-4 hidden md:flex md:flex-col gap-1"
    >
      <div className="flex items-center gap-2 mb-5 font-bold text-[14px]">
        <span className="w-6 h-6 rounded-lg bg-gradient-to-br from-violet to-cyan" />
        mt5-journal
      </div>
      {LINKS.map((l) => (
        <NavLink key={l.to} to={l.to} end={l.end} className={item("px-3 py-2")}>
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}

// Below 768px the rail is gone, so this is the only way between routes — the
// Rail-Optional Rule, honoured rather than assumed. Nine routes overflow a
// phone, so the bar scrolls sideways and re-centres itself on the route you
// are actually on; landing on /lab with the active item off-screen reads as
// "nothing is selected".
export function MobileNav() {
  const { pathname } = useLocation();
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    // Optional call: jsdom has no scrollIntoView, and re-centring is a nicety
    // that must never be the reason navigation throws.
    ref.current
      ?.querySelector('[aria-current="page"]')
      ?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [pathname]);

  return (
    <nav
      ref={ref}
      aria-label="Navigasi utama"
      className="md:hidden fixed inset-x-0 bottom-0 z-40 flex gap-1 overflow-x-auto
                 border-t border-panel-border bg-panel backdrop-blur-[8px]
                 px-2 pt-1.5 pb-[max(6px,env(safe-area-inset-bottom))]"
    >
      {LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={item("px-3 min-h-[44px] flex items-center whitespace-nowrap")}
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
