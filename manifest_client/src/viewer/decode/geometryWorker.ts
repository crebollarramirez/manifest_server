import { decodeGlb } from "./glb";
import { decodeStl } from "./stl";
import type { DecodeRequest, DecodeResponse } from "./types";

/**
 * Geometry decode worker: STL/GLB parsing, normal computation, and bounds all
 * happen here, off the main thread (performance budget: no main-thread task
 * over 50ms during load or edit-swap). Buffers travel both directions as
 * transferables.
 */

self.onmessage = (event: MessageEvent<DecodeRequest>) => {
  const { id, kind, buffer } = event.data;
  try {
    const geometry = kind === "stl" ? decodeStl(buffer) : decodeGlb(buffer);
    const transfer: Transferable[] = [
      geometry.positions.buffer,
      geometry.normals.buffer,
    ];
    if (geometry.indices) transfer.push(geometry.indices.buffer);
    const response: DecodeResponse = { id, ok: true, geometry };
    (self as unknown as Worker).postMessage(response, transfer);
  } catch (error) {
    const response: DecodeResponse = {
      id,
      ok: false,
      error: error instanceof Error ? error.message : "Unknown decode error.",
    };
    (self as unknown as Worker).postMessage(response);
  }
};
