import { useApi } from "../lib/api";
import { CommandsData } from "../lib/types";
import CommandsTable from "../components/CommandsTable";

export default function Commands() {
  const { data, error, loading } = useApi<CommandsData>("/api/commands", 5000);
  if (loading) return <div className="text-muted p-6">Memuat…</div>;
  if (error) return <div className="glass p-6 text-neg">Gagal memuat: {error}</div>;
  if (!data) return null;
  return (
    <div>
      <h1 className="text-[18px] font-bold tracking-tight mb-1">Log perintah</h1>
      <div className="text-[12px] text-muted mb-4">audit — apa yang diminta vs apa yang terjadi</div>
      <div className="glass p-4">
        <CommandsTable rows={data.commands} offsetS={data.header.offset_s} />
      </div>
    </div>
  );
}
