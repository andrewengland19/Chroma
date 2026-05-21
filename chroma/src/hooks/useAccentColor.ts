import { useEffect } from "react";

function hexToRgb(hex: string): [number, number, number] | null {
  const m = hex.replace("#", "");
  if (m.length !== 6) return null;
  const n = parseInt(m, 16);
  if (Number.isNaN(n)) return null;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function useAccentColor(color: string | undefined) {
  useEffect(() => {
    const root = document.documentElement;
    if (!color) {
      root.style.setProperty("--accent", "#888888");
      root.style.setProperty("--accent-muted", "rgba(136,136,136,0.2)");
      return;
    }
    const rgb = hexToRgb(color);
    if (!rgb) return;
    root.style.setProperty("--accent", color);
    root.style.setProperty(
      "--accent-muted",
      `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.2)`,
    );
  }, [color]);
}
