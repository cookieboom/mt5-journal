export default function KpiCard({
  label, value, sub, tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg";
}) {
  const color = tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-ink";
  return (
    <div className="glass p-3.5">
      <div className="text-[9.5px] tracking-[0.09em] uppercase text-muted">{label}</div>
      <div className={`num text-[23px] font-bold tracking-tight mt-1 ${color}`}>{value}</div>
      {sub && <div className="text-[10.5px] text-muted mt-0.5">{sub}</div>}
    </div>
  );
}
