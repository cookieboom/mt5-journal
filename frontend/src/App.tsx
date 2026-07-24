import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppShell from "./components/AppShell";
import Placeholder from "./pages/Placeholder";
import Dashboard from "./pages/Dashboard";
import Live from "./pages/Live";
import Commands from "./pages/Commands";
import Trades from "./pages/Trades";

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/live" element={<Live />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/trades/:id" element={<Placeholder name="Trade detail" />} />
          <Route path="/report" element={<Placeholder name="Report" />} />
          <Route path="/weekly" element={<Placeholder name="Weekly" />} />
          <Route path="/commands" element={<Commands />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
