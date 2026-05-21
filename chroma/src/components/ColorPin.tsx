import React from "react";
import { Pin } from "../types";

interface Props {
  pin: Pin;
  onRemove: (id: string) => void;
}

export function ColorPin({ pin, onRemove }: Props) {
  return (
    <div
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onRemove(pin.id);
      }}
      title={pin.color.toUpperCase()}
      className="absolute pointer-events-auto"
      style={{
        left: `${pin.x * 100}%`,
        top: `${pin.y * 100}%`,
        width: 12,
        height: 12,
        marginLeft: -6,
        marginTop: -6,
        borderRadius: "50%",
        background: pin.color,
        border: "2px solid #fff",
        boxShadow: "0 1px 3px rgba(0,0,0,0.55)",
        animation: "chroma-pin-in 120ms ease-out",
      }}
    />
  );
}

// keyframes injected globally
const style = document.createElement("style");
style.textContent = `@keyframes chroma-pin-in { from { transform: scale(0); } to { transform: scale(1); } }`;
if (!document.head.querySelector("style[data-chroma-pin]")) {
  style.setAttribute("data-chroma-pin", "");
  document.head.appendChild(style);
}
