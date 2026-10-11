export function PendingScreen({ name, milestone }: { name: string; milestone: string }) {
  return (
    <div data-testid={`${name.toLowerCase()}-screen`} className="absolute flex flex-col items-center justify-center text-dim"
      style={{ left: 19, top: 42, width: 1140, height: 633, fontSize: 11 }}>
      <div className="font-semibold text-fg" style={{ fontSize: 13 }}>{name}</div>
      <div>arrives in {milestone}</div>
    </div>
  );
}
