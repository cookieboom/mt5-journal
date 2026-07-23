export default function Placeholder({ name }: { name: string }) {
  return (
    <div className="glass p-6">
      <h1 className="text-lg font-bold mb-1">{name}</h1>
      <p className="text-muted text-sm">Halaman ini dibangun di fase berikutnya.</p>
    </div>
  );
}
