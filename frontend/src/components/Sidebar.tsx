import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/live", label: "Live" },
  { to: "/chart", label: "Chart" },
  { to: "/trades", label: "Trades" },
  { to: "/report", label: "Report" },
  { to: "/weekly", label: "Weekly" },
  { to: "/commands", label: "Commands" },
];

export default function Sidebar() {
  return (
    <aside className="w-[186px] shrink-0 border-r border-panel-border p-4 hidden md:flex md:flex-col gap-1">
      <div className="flex items-center gap-2 mb-5 font-bold text-[14px]">
        <span className="w-6 h-6 rounded-lg bg-gradient-to-br from-violet to-cyan" />
        mt5-journal
      </div>
      {LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) =>
            "px-3 py-2 rounded-lg text-[13px] transition " +
            (isActive
              ? "text-white bg-gradient-to-r from-violet/25 to-cyan/5 ring-1 ring-inset ring-violet/35"
              : "text-muted hover:text-ink")
          }
        >
          {l.label}
        </NavLink>
      ))}
    </aside>
  );
}
