import { ReactNode, useEffect } from "react";
import { useLocation } from "react-router-dom";
import Sidebar, { MobileNav, LINKS } from "./Sidebar";

// A route change in an SPA is silent: the tab, the history entry and the
// screen reader all keep saying whatever index.html said. The nav labels are
// already the route names, so they are the titles too — longest prefix wins,
// which puts /trades/42 under "Trades" without a second table to maintain.
function titleFor(pathname: string): string {
  const hit = LINKS.filter((l) => (l.end ? pathname === l.to : pathname.startsWith(l.to)))
    .sort((a, b) => b.to.length - a.to.length)[0];
  return hit ? `${hit.label} · mt5-journal` : "mt5-journal";
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  useEffect(() => { document.title = titleFor(pathname); }, [pathname]);

  return (
    <div className="min-h-screen grid grid-cols-1 md:grid-cols-[186px_1fr]">
      {/* Nine nav items sit before the content in the tab order on every
          route. Invisible until focused, then a normal panel. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50
                   focus:rounded focus:bg-bg focus:px-3 focus:py-2 focus:text-title
                   focus:text-ink focus:ring-1 focus:ring-cyan"
      >
        Lewati ke konten
      </a>
      <Sidebar />
      {/* The bar is fixed, so the page has to reserve its height itself or the
          last row of every table sits underneath it. */}
      <main id="main" tabIndex={-1} className="p-5 md:p-6 pb-[76px] md:pb-6 overflow-hidden">
        {children}
      </main>
      <MobileNav />
    </div>
  );
}
