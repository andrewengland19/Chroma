import React from "react";

interface Props {
  onClick: () => void;
  children: React.ReactNode;
}

export function LinkButton({ onClick, children }: Props) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] text-fg-dim hover:underline hover:text-fg accent-transition"
    >
      {children}
    </button>
  );
}
