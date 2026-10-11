import { useSelect } from "../store";
import { Panel } from "./ui";

// Canvas of the Room panel (content-local px), from gui-1.
const W = 300;
const H = 162;

export function RoomView() {
  const room = useSelect((s) => s.state?.room) ?? [];
  const lit = room.filter((r) => r.hex).length;
  return (
    <Panel style={{ left: 19, top: 109, width: 324, height: 195 }}>
      <div className="label absolute" style={{ left: 11, top: 8 }}>
        Room <span style={{ margin: "0 3px" }}>·</span> <span className="normal-case">live</span>
      </div>
      <div data-testid="room-canvas" className="absolute overflow-hidden rounded-[7px] border border-line bg-well"
        style={{ left: 11, top: 21, width: W, height: H }}>
        {room.map((r) => {
          const x = r.x * W;
          const y = r.y * H;
          const c = r.hex ?? "#3a3a42";
          return (
            <div key={r.fixture_id} data-testid={`room-${r.fixture_id}`}>
              {r.hex && (
                <div className="pointer-events-none absolute rounded-full"
                  style={{ left: x - 38, top: y - 38, width: 76, height: 76,
                    background: `radial-gradient(circle, ${c}80 0%, ${c}38 38%, transparent 70%)` }} />
              )}
              <div className="absolute rounded-full" title={r.label}
                style={{ left: x - 7.5, top: y - 7.5, width: 15, height: 15, background: c, border: "1.3px solid #f3f3f6" }} />
              <div className="absolute whitespace-nowrap text-dim" style={{ left: x, top: y + 10, fontSize: 8, transform: "translateX(-50%)" }}>
                {r.label}
              </div>
            </div>
          );
        })}
        <div className="absolute text-dim" style={{ right: 5, bottom: 2, fontSize: 7.5 }}>{`${lit} / ${room.length} lights`}</div>
      </div>
    </Panel>
  );
}
