// Minimal Setup "Board" card (not in the mockups — flagged for review): board link status, and
// pairing. "Pair board" creates ~/.chroma/board.key and turns the UDP listener on; the key and
// address are shown once for the board's firmware config (never logged or toasted).
import { useState } from "react";
import { useSelect, useStore } from "../../store";

type Pairing = { key: string; port: number; hosts: string[]; created: boolean };

export function BoardCard() {
  const store = useStore();
  const link = useSelect((s) => s.state?.board_link);
  const health = useSelect((s) => s.state?.health.board);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [error, setError] = useState("");
  if (!link) return null;
  const pair = async () => {
    const r = await store.ipc.request({ op: "board.pairing" });
    if (r.ok) { setPairing(r as unknown as Pairing); setError(""); }
    else setError(r.error || "could not pair");
  };
  const status = !link.paired ? "not paired" : health === "online" || health === "virtual"
    ? `${health}${link.id ? ` · ${link.id}` : ""}` : health === "offline" ? "offline" : "waiting for the board";
  const btn = "rounded-[5px] border border-ctl-line bg-ctl font-semibold text-fg";
  return (
    <div data-testid="board-card" className="absolute" style={{ left: 340, top: 6, zIndex: 5 }}>
      <div className="flex items-center rounded-[6px] border border-line bg-panel" style={{ height: 30, padding: "0 10px", gap: 10, fontSize: 10 }}>
        <span className="label">Board</span>
        <span data-testid="board-status" className="text-dim">{status}</span>
        <span className="font-mono text-label">{`UDP ${link.port}`}</span>
        <button data-testid="board-pair" onClick={() => void pair()} className={btn} style={{ height: 20, padding: "0 8px", fontSize: 9.5 }}>
          {link.paired ? "Show key" : "Pair board"}
        </button>
      </div>
      {error && <div role="alert" className="text-red" style={{ fontSize: 9, marginTop: 3 }}>{error}</div>}
      {pairing && (
        <div role="dialog" aria-label="Board pairing" data-testid="board-pairing"
          className="rounded-[8px] border border-line bg-panel shadow-xl" style={{ marginTop: 4, padding: 12, width: 520, fontSize: 10 }}>
          <div className="font-semibold" style={{ fontSize: 11 }}>{pairing.created ? "Board paired" : "Board key"}</div>
          <div className="text-dim" style={{ margin: "6px 0 4px" }}>
            Put this key and address in the board's config. Anyone with the key can drive the lights.
          </div>
          <div className="font-mono break-all rounded-[5px] border border-ctl-line bg-ctl" data-testid="board-key" style={{ padding: 6, fontSize: 9 }}>
            {pairing.key}
          </div>
          <div className="font-mono text-dim" style={{ marginTop: 6 }}>
            {pairing.hosts.length ? pairing.hosts.map((h) => `${h}:${pairing.port}`).join("  ") : `UDP port ${pairing.port}`}
          </div>
          <div className="flex justify-end" style={{ gap: 6, marginTop: 8 }}>
            <button onClick={() => void navigator.clipboard?.writeText(pairing.key)} className={btn} style={{ height: 22, padding: "0 10px" }}>Copy key</button>
            <button onClick={() => setPairing(null)} className={btn} style={{ height: 22, padding: "0 10px" }}>Done</button>
          </div>
        </div>
      )}
    </div>
  );
}
