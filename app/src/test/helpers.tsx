import { render } from "@testing-library/react";
import { MockEngine, type Scenario } from "../mock/engine";
import { Shell } from "../Shell";
import { ChromaStore, StoreContext } from "../store";

export async function renderApp(scenario: Scenario = "mockup-console", freeze = true) {
  const engine = new MockEngine(scenario);
  const store = new ChromaStore(engine, (u) => engine.artSrc(u), freeze);
  store.start();
  await store.refreshState();
  const utils = render(
    <StoreContext.Provider value={store}>
      <Shell />
    </StoreContext.Provider>,
  );
  return { engine, store, ...utils };
}
