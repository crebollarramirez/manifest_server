import type { DecodedGeometry, DecodeResponse, GeometryKind } from "./types";

/**
 * Promise wrapper around the geometry worker. One shared worker instance;
 * requests are matched by id. The input ArrayBuffer is transferred (not
 * copied) into the worker and must not be reused by the caller.
 */

type Pending = {
  resolve: (geometry: DecodedGeometry) => void;
  reject: (error: Error) => void;
};

let worker: Worker | null = null;
let nextRequestId = 1;
const pending = new Map<number, Pending>();

function ensureWorker(): Worker {
  if (worker) return worker;
  worker = new Worker(new URL("./geometryWorker.ts", import.meta.url), {
    type: "module",
  });
  worker.onmessage = (event: MessageEvent<DecodeResponse>) => {
    const response = event.data;
    const entry = pending.get(response.id);
    if (!entry) return;
    pending.delete(response.id);
    if (response.ok) {
      entry.resolve(response.geometry);
    } else {
      entry.reject(new Error(response.error));
    }
  };
  worker.onerror = () => {
    const error = new Error("Geometry worker crashed.");
    for (const entry of pending.values()) entry.reject(error);
    pending.clear();
    worker?.terminate();
    worker = null;
  };
  return worker;
}

export function decodeGeometry(
  kind: GeometryKind,
  buffer: ArrayBuffer,
): Promise<DecodedGeometry> {
  const id = nextRequestId;
  nextRequestId += 1;
  return new Promise<DecodedGeometry>((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ensureWorker().postMessage({ id, kind, buffer }, [buffer]);
  });
}
