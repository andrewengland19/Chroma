// gui-3 BANKS: which group each fader controls. Drag a group (the GROUP picker or another
// cell) onto a cell; dragging cell→cell swaps. Keyboard: Enter assigns the selected group
// to the focused cell, Delete/Backspace empties it. Clicking a cell selects its group.
import type { BankName } from "../../contracts";
import { useSelect, useStore } from "../../store";
import { Panel } from "../ui";

const BANK_NAMES: BankName[] = ["A", "B", "C"];
const MIME_GROUP = "application/x-chroma-group";
const MIME_CELL = "application/x-chroma-cell";

export function BanksPanel({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const store = useStore();
  const rig = useSelect((s) => s.rig);
  const current = useSelect((s) => s.state?.bank);
  if (!rig) return <Panel style={{ left: 19, top: 459, width: 1140, height: 216 }} />;
  const group = (id: string | null) => rig.groups.find((g) => g.id === id) ?? null;
  const set = (bank: BankName, ch: number, gid: string | null) =>
    store.op("banks.set", { bank, channel: ch, group_id: gid });

  const drop = async (e: React.DragEvent, bank: BankName, ch: number) => {
    e.preventDefault();
    const cell = e.dataTransfer.getData(MIME_CELL);
    if (cell) {
      const [fb, fch] = [cell[0] as BankName, Number(cell.slice(1))];
      if (fb === bank && fch === ch) return;
      const moving = rig.banks[fb][fch - 1];
      const here = rig.banks[bank][ch - 1];
      await set(bank, ch, moving);
      await set(fb, fch, here);               // swap
      return;
    }
    const gid = e.dataTransfer.getData(MIME_GROUP);
    if (gid) await set(bank, ch, gid);
  };

  return (
    <Panel style={{ left: 19, top: 459, width: 1140, height: 216 }}>
      <div className="label absolute" style={{ left: 15, top: 12 }}>
        Banks<span style={{ margin: "0 5px" }}>·</span>
        <span className="normal-case">which group each fader controls&nbsp;&nbsp; (drag a group onto a cell)</span>
      </div>
      {BANK_NAMES.map((b, r) => (
        <div key={b}>
          <div className={`absolute flex items-center justify-center rounded-[6px] font-bold ${b === current ? "bg-accent text-white" : "border border-ctl-line bg-ctl text-dim"}`}
            style={{ left: 15, top: 35 + r * 55.3, width: 40, height: 45.5, fontSize: 12.5 }}>{b}</div>
          {rig.banks[b].map((gid, i) => {
            const g = group(gid);
            const ch = i + 1;
            return (
              <div key={ch} role="button" tabIndex={0} data-testid={`cell-${b}${ch}`}
                aria-label={`Bank ${b} channel ${ch}: ${g ? g.name : "empty"}`}
                draggable={!!g}
                onDragStart={(e) => { e.dataTransfer.setData(MIME_CELL, `${b}${ch}`); }}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => void drop(e, b, ch)}
                onClick={() => g && onSelect(g.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && selected) void set(b, ch, selected);
                  if (e.key === "Delete" || e.key === "Backspace") void set(b, ch, null);
                }}
                className={`absolute rounded-[6px] border outline-none focus:border-accent ${g ? "border-ctl-line bg-ctl" : "border-ctl-line"}`}
                style={{ left: 65.5 + i * 130.2, top: 35 + r * 55.3, width: 124.4, height: 45.5 }}>
                {g ? (
                  <>
                    <span className="absolute rounded-full" style={{ left: 8, top: 8, width: 3.5, height: 29, background: g.color }} />
                    <span className="absolute font-mono text-label" style={{ left: 20, top: 12, fontSize: 7.5 }}>{`CH ${ch}`}</span>
                    <span className="absolute truncate font-bold" style={{ left: 20, top: 23.5, right: 6, fontSize: 11 }}>{g.name}</span>
                  </>
                ) : (
                  <span className="absolute inset-0 flex items-center justify-center text-label" style={{ fontSize: 8.5 }}>empty</span>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </Panel>
  );
}
