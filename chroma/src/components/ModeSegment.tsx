import React from "react";

interface Props {
  value: "single" | "palette";
  onChange: (v: "single" | "palette") => void;
}

export function ModeSegment({ value, onChange }: Props) {
  const opt = (key: "single" | "palette", label: string) => {
    const selected = value === key;
    return (
      <button
        key={key}
        onClick={() => onChange(key)}
        className="flex-1 h-7 text-[11px] font-body accent-transition rounded-[5px]"
        style={{
          background: selected ? "var(--accent)" : "transparent",
          color: selected ? "#0e0e0e" : "var(--fg-dim)",
          fontWeight: selected ? 600 : 400,
        }}
      >
        {label}
      </button>
    );
  };
  return (
    <div
      className="flex p-[2px] rounded-md"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      {opt("single", "Single")}
      {opt("palette", "Palette")}
    </div>
  );
}
