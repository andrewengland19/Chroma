import { useEffect, useState } from "react";
import { createBackend } from "./ipc";
import { Shell } from "./Shell";
import { ChromaStore, StoreContext } from "./store";

export default function App() {
  const [store, setStore] = useState<ChromaStore | null>(null);
  useEffect(() => {
    let stop: (() => void) | undefined;
    let dispose: (() => void) | undefined;
    let cancelled = false;
    void createBackend().then((b) => {
      dispose = b.dispose;
      if (cancelled) return b.dispose?.();
      const s = new ChromaStore(b.ipc, b.artSrc, b.freeze);
      stop = s.start();
      setStore(s);
    });
    return () => {
      cancelled = true;
      stop?.();
      dispose?.();
    };
  }, []);
  if (!store) return <main className="h-full bg-bg" data-testid="app-root" />;
  return (
    <StoreContext.Provider value={store}>
      <main className="h-full" data-testid="app-root">
        <Shell />
      </main>
    </StoreContext.Provider>
  );
}
