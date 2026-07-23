import { ReactNode } from "react";
import Sidebar from "./Sidebar";

export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen grid grid-cols-1 md:grid-cols-[186px_1fr]">
      <Sidebar />
      <main className="p-5 md:p-6 overflow-hidden">{children}</main>
    </div>
  );
}
