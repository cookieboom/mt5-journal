import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppShell from "./components/AppShell";
import Dashboard from "./pages/Dashboard";
import Live from "./pages/Live";
import Chart from "./pages/Chart";
import Commands from "./pages/Commands";
import Trades from "./pages/Trades";
import TradeDetail from "./pages/TradeDetail";
import TradeView from "./pages/TradeView";
import Report from "./pages/Report";
import Weekly from "./pages/Weekly";
import StoragePage from "./pages/StoragePage";

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
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
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
