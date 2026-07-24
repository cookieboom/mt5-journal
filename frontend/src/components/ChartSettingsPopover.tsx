import type { ChartSettings } from "../lib/chartPrefs";

export default function ChartSettingsPopover({
  settings, onChange, onClose,
}: {
  settings: ChartSettings;
  onChange: (s: ChartSettings) => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div className="glass absolute right-0 top-9 z-20 w-56 p-3 text-[12px]">
        <div className="mb-3">
          <div className="text-muted mb-1">Tema chart</div>
          <div className="flex gap-1">
            {(["dark", "light"] as const).map((t) => (
              <button
                key={t}
                onClick={() => onChange({ ...settings, theme: t })}
                className={
                  "px-2 py-1 rounded-md capitalize " +
                  (settings.theme === t
                    ? "bg-violet/25 ring-1 ring-inset ring-violet/35 text-ink"
                    : "text-muted hover:text-ink")
                }
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <label className="flex items-center justify-between">
          <span className="text-muted">Garis grid</span>
          <input
            type="checkbox"
            checked={settings.grid}
            onChange={(e) => onChange({ ...settings, grid: e.target.checked })}
          />
        </label>
      </div>
    </>
  );
}
