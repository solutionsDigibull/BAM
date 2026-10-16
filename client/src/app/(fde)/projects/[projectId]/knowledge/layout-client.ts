// Runs `layout` in a web worker; where there is none (tests, old browsers) on the main thread.
import { layout, type Graph, type Positions } from "./graph-model";

let worker: Worker | null | undefined;
let next = 0;
const waiting = new Map<number, (positions: Positions) => void>();

function getWorker(): Worker | null {
  if (worker !== undefined) return worker;
  try {
    worker =
      typeof Worker === "undefined"
        ? null
        : new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
  } catch {
    worker = null;
  }
  worker?.addEventListener(
    "message",
    (event: MessageEvent<{ id: number; positions: Positions }>) => {
      waiting.get(event.data.id)?.(event.data.positions);
      waiting.delete(event.data.id);
    },
  );
  return worker;
}

export function layoutAsync(graph: Graph): Promise<Positions> {
  const w = getWorker();
  if (!w) return Promise.resolve(layout(graph));
  const id = ++next;
  return new Promise((resolve) => {
    waiting.set(id, resolve);
    w.postMessage({ id, graph });
  });
}
