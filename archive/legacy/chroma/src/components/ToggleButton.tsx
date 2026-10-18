import React from "react";

interface Props {
  active: boolean;
  onToggle: () => void;
}

export function ToggleButton({ active, onToggle }: Props) {
  return (
    <button
      onClick={onToggle}
      className="w-full h-9 rounded-full font-display text-[12px] font-semibold accent-transition flex items-center justify-center"
      style={{
        background: active ? "var(--accent)" : "var(--surface)",
        color: active ? "#0e0e0e" : "var(--fg)",
        border: "1px solid var(--border)",
      }}
    >
      {active ? "■  Stop" : "▶  Start tracking"}
    </button>
  );
}
