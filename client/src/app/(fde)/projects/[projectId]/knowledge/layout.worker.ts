// Knowledge Explorer layout off the main thread (E5.5): dagre over up to 2,000 nodes can take long
// enough to stutter pan and zoom, so it runs here.
import { layout, type Graph } from "./graph-model";

self.onmessage = (event: MessageEvent<{ id: number; graph: Graph }>) => {
  const { id, graph } = event.data;
  self.postMessage({ id, positions: layout(graph) });
};
