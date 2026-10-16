// Tap vs hold on one control: `onTap` on a quick release, `onHold` once the press lasts
// HOLD_MS (D12: 600 ms). Shift+click counts as a hold (the mockup's "shift + tap").
import { useRef } from "react";

export const HOLD_MS = 600;

export function useHold(onTap: () => void, onHold: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const held = useRef(false);
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      held.current = false;
      if (e.shiftKey) {
        held.current = true;
        onHold();
        return;
      }
      clear();
      timer.current = setTimeout(() => {
        held.current = true;
        onHold();
      }, HOLD_MS);
    },
    onPointerUp: () => {
      if (timer.current && !held.current) {
        clear();
        onTap();
      }
      clear();
    },
    onPointerLeave: clear,
    onPointerCancel: clear,
  };
}
