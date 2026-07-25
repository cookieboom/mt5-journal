import { wib } from "../lib/format";

export default function ReplayControls(props: {
  cursorMsc: number | null;
  playing: boolean;
  atEnd: boolean;
  onStep: () => void;
  onPlayPause: () => void;
  onJump: (n: number) => void;
  onReset: () => void;
  onExit: () => void;
}) {
  return (
    <div className="glass flex items-center gap-2 px-3 py-2 text-xs">
      <span className="rounded bg-cyan/20 px-2 py-0.5 text-cyan font-semibold">REPLAY</span>
      <button className="glass px-2 py-1" title="Reset ke awal" onClick={props.onReset}>|◀ Reset</button>
      <button className="glass px-2 py-1" onClick={props.onStep} disabled={props.atEnd}>▶| Step</button>
      <button className="glass px-2 py-1 text-cyan" onClick={props.onPlayPause} disabled={props.atEnd}>
        {props.playing ? "⏸ Pause" : "▶ Play"}
      </button>
      <button className="glass px-2 py-1" onClick={() => props.onJump(10)} disabled={props.atEnd}>⏩ +10</button>
      <span className="ml-auto text-muted">
        {props.cursorMsc ? wib(props.cursorMsc, 0) : "—"}{props.atEnd ? " · selesai" : ""}
      </span>
      <button className="glass px-2 py-1 text-neg" onClick={props.onExit}>Keluar</button>
    </div>
  );
}
