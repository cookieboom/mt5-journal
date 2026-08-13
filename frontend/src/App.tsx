import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppShell from "./components/AppShell";

// Every page is a separate chunk. One eager import of Chart or TradeView drags
// lightweight-charts and the 38KB CandleChart into the entry bundle, which is
// what made /report cost 544KB to open. Uniformly lazy, so no page is a special
// case someone has to remember when adding the next one.
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Live = lazy(() => import("./pages/Live"));
const Chart = lazy(() => import("./pages/Chart"));
const Commands = lazy(() => import("./pages/Commands"));
const Trades = lazy(() => import("./pages/Trades"));
const TradeDetail = lazy(() => import("./pages/TradeDetail"));
const TradeView = lazy(() => import("./pages/TradeView"));
const Report = lazy(() => import("./pages/Report"));
const Weekly = lazy(() => import("./pages/Weekly"));
const StoragePage = lazy(() => import("./pages/StoragePage"));
const Lab = lazy(() => import("./pages/Lab"));

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        {/* Inside the shell, so the nav stays painted while a chunk arrives.
            Same copy the pages use for their own data waits. */}
        <Suspense fallback={<div role="status" className="text-muted p-6">Memuat…</div>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/live" element={<Live />} />
            <Route path="/chart" element={<Chart />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/trades/:id" element={<TradeDetail />} />
            <Route path="/trades/:id/view" element={<TradeView />} />
            <Route path="/report" element={<Report />} />
            <Route path="/weekly" element={<Weekly />} />
            <Route path="/weekly/:week" element={<Weekly />} />
            <Route path="/commands" element={<Commands />} />
            <Route path="/storage" element={<StoragePage />} />
            <Route path="/lab" element={<Lab />} />
          </Routes>
        </Suspense>
      </AppShell>
    </BrowserRouter>
  );
}
