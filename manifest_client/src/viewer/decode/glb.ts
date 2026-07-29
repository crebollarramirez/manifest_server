import {
  computeBounds,
  type AuthoredMaterial,
  type DecodedGeometry,
} from "./types";

/**
 * Minimal GLB (glTF 2.0 binary) decoder. Runs in the geometry worker.
 *
 * Scope: geometry + authored PBR factors for Blender-exported scenes —
 * multiple nodes/meshes/primitives with node transforms (matrix or TRS)
 * merged into one geometry. Deliberately unsupported (throws loudly rather
 * than rendering wrong): interleaved bufferViews (byteStride), non-triangle
 * primitive modes, Draco/extensions requiring decode. Textures are ignored;
 * only pbrMetallicRoughness factors are surfaced (Phase 2 uses them and never
 * overrides them).
 */

const GLB_MAGIC = 0x46546c67; // 'glTF'
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;
const COMPONENT_FLOAT = 5126;

type GltfAccessor = {
  bufferView?: number;
  byteOffset?: number;
  componentType: number;
  count: number;
  type: string;
};

type GltfBufferView = {
  buffer: number;
  byteOffset?: number;
  byteLength: number;
  byteStride?: number;
};

type GltfPrimitive = {
  attributes: Record<string, number>;
  indices?: number;
  material?: number;
  mode?: number;
};

type GltfNode = {
  mesh?: number;
  children?: number[];
  matrix?: number[];
  translation?: number[];
  rotation?: number[];
  scale?: number[];
};

type GltfJson = {
  asset?: { version?: string };
  scene?: number;
  scenes?: Array<{ nodes?: number[] }>;
  nodes?: GltfNode[];
  meshes?: Array<{ primitives: GltfPrimitive[] }>;
  materials?: Array<{
    pbrMetallicRoughness?: {
      baseColorFactor?: number[];
      metallicFactor?: number;
      roughnessFactor?: number;
    };
  }>;
  accessors?: GltfAccessor[];
  bufferViews?: GltfBufferView[];
};

// --- column-major 4x4 matrix helpers (glTF convention) ---------------------

type Mat4 = number[];

const IDENTITY: Mat4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

function multiply(a: Mat4, b: Mat4): Mat4 {
  const out = new Array<number>(16);
  for (let column = 0; column < 4; column += 1) {
    for (let row = 0; row < 4; row += 1) {
      let sum = 0;
      for (let k = 0; k < 4; k += 1) {
        sum += a[k * 4 + row]! * b[column * 4 + k]!;
      }
      out[column * 4 + row] = sum;
    }
  }
  return out;
}

function fromTrs(node: GltfNode): Mat4 {
  if (node.matrix) return node.matrix as Mat4;
  const [tx, ty, tz] = node.translation ?? [0, 0, 0];
  const [qx, qy, qz, qw] = node.rotation ?? [0, 0, 0, 1];
  const [sx, sy, sz] = node.scale ?? [1, 1, 1];
  // Standard quaternion -> rotation matrix, then scale columns, set translation.
  const x2 = qx! + qx!, y2 = qy! + qy!, z2 = qz! + qz!;
  const xx = qx! * x2, xy = qx! * y2, xz = qx! * z2;
  const yy = qy! * y2, yz = qy! * z2, zz = qz! * z2;
  const wx = qw! * x2, wy = qw! * y2, wz = qw! * z2;
  return [
    (1 - (yy + zz)) * sx!, (xy + wz) * sx!, (xz - wy) * sx!, 0,
    (xy - wz) * sy!, (1 - (xx + zz)) * sy!, (yz + wx) * sy!, 0,
    (xz + wy) * sz!, (yz - wx) * sz!, (1 - (xx + yy)) * sz!, 0,
    tx!, ty!, tz!, 1,
  ];
}

function transformPoint(m: Mat4, x: number, y: number, z: number): [number, number, number] {
  return [
    m[0]! * x + m[4]! * y + m[8]! * z + m[12]!,
    m[1]! * x + m[5]! * y + m[9]! * z + m[13]!,
    m[2]! * x + m[6]! * y + m[10]! * z + m[14]!,
  ];
}

function transformDirection(m: Mat4, x: number, y: number, z: number): [number, number, number] {
  const dx = m[0]! * x + m[4]! * y + m[8]! * z;
  const dy = m[1]! * x + m[5]! * y + m[9]! * z;
  const dz = m[2]! * x + m[6]! * y + m[10]! * z;
  const length = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
  return [dx / length, dy / length, dz / length];
}

// --- container parsing -----------------------------------------------------

export function decodeGlb(buffer: ArrayBuffer): DecodedGeometry {
  const view = new DataView(buffer);
  if (buffer.byteLength < 12 || view.getUint32(0, true) !== GLB_MAGIC) {
    throw new Error("Not a GLB container (bad magic).");
  }
  if (view.getUint32(4, true) !== 2) {
    throw new Error("Unsupported GLB version (expected 2).");
  }

  let json: GltfJson | null = null;
  let bin: Uint8Array | null = null;
  let offset = 12;
  while (offset + 8 <= buffer.byteLength) {
    const chunkLength = view.getUint32(offset, true);
    const chunkType = view.getUint32(offset + 4, true);
    const start = offset + 8;
    if (start + chunkLength > buffer.byteLength) {
      throw new Error("Truncated GLB chunk.");
    }
    if (chunkType === CHUNK_JSON) {
      json = JSON.parse(
        new TextDecoder().decode(new Uint8Array(buffer, start, chunkLength)),
      ) as GltfJson;
    } else if (chunkType === CHUNK_BIN) {
      bin = new Uint8Array(buffer, start, chunkLength);
    }
    offset = start + chunkLength + ((4 - (chunkLength % 4)) % 4);
  }
  if (!json) throw new Error("GLB has no JSON chunk.");

  const merged = mergePrimitives(json, bin);
  return merged;
}

function readAccessor(
  json: GltfJson,
  bin: Uint8Array | null,
  accessorIndex: number,
  expectedType: string,
): { data: ArrayBufferView; componentType: number; count: number } {
  const accessor = json.accessors?.[accessorIndex];
  if (!accessor) throw new Error(`Missing accessor ${accessorIndex}.`);
  if (accessor.type !== expectedType) {
    throw new Error(
      `Accessor ${accessorIndex} is ${accessor.type}; expected ${expectedType}.`,
    );
  }
  if (accessor.bufferView === undefined) {
    throw new Error("Sparse/zero-filled accessors are not supported.");
  }
  const bufferView = json.bufferViews?.[accessor.bufferView];
  if (!bufferView) throw new Error(`Missing bufferView ${accessor.bufferView}.`);
  if (bufferView.byteStride !== undefined) {
    const packed = expectedType === "VEC3" ? 12 : componentBytes(accessor.componentType);
    if (bufferView.byteStride !== packed) {
      throw new Error("Interleaved bufferViews (byteStride) are not supported.");
    }
  }
  if (!bin) throw new Error("GLB has no BIN chunk but accessors reference it.");

  const componentCount = expectedType === "VEC3" ? 3 : 1;
  const byteOffset =
    bin.byteOffset + (bufferView.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
  const length = accessor.count * componentCount;
  let data: ArrayBufferView;
  switch (accessor.componentType) {
    case COMPONENT_FLOAT:
      data = new Float32Array(bin.buffer, byteOffset, length);
      break;
    case 5125:
      data = new Uint32Array(bin.buffer, byteOffset, length);
      break;
    case 5123:
      data = new Uint16Array(bin.buffer, byteOffset, length);
      break;
    case 5121:
      data = new Uint8Array(bin.buffer, byteOffset, length);
      break;
    default:
      throw new Error(`Unsupported componentType ${accessor.componentType}.`);
  }
  return { data, componentType: accessor.componentType, count: accessor.count };
}

function componentBytes(componentType: number): number {
  return componentType === 5121 ? 1 : componentType === 5123 ? 2 : 4;
}

type PrimitiveInstance = { primitive: GltfPrimitive; worldMatrix: Mat4 };

function collectPrimitives(json: GltfJson): PrimitiveInstance[] {
  const instances: PrimitiveInstance[] = [];
  const sceneNodes = json.scenes?.[json.scene ?? 0]?.nodes;
  const visit = (nodeIndex: number, parent: Mat4): void => {
    const node = json.nodes?.[nodeIndex];
    if (!node) return;
    const world = multiply(parent, fromTrs(node));
    if (node.mesh !== undefined) {
      for (const primitive of json.meshes?.[node.mesh]?.primitives ?? []) {
        instances.push({ primitive, worldMatrix: world });
      }
    }
    for (const child of node.children ?? []) visit(child, world);
  };
  if (sceneNodes && sceneNodes.length > 0) {
    for (const nodeIndex of sceneNodes) visit(nodeIndex, IDENTITY);
  } else {
    // No scene: fall back to every mesh on every node.
    (json.nodes ?? []).forEach((_, index) => visit(index, IDENTITY));
  }
  return instances;
}

function mergePrimitives(json: GltfJson, bin: Uint8Array | null): DecodedGeometry {
  const instances = collectPrimitives(json);
  if (instances.length === 0) throw new Error("GLB contains no mesh primitives.");

  const positionChunks: Float32Array[] = [];
  const normalChunks: Float32Array[] = [];
  const indexChunks: Uint32Array[] = [];
  let vertexOffset = 0;
  let authoredMaterial: AuthoredMaterial | null = null;

  for (const { primitive, worldMatrix } of instances) {
    if ((primitive.mode ?? 4) !== 4) {
      throw new Error("Only TRIANGLES primitives are supported.");
    }
    const positionAccessor = primitive.attributes["POSITION"];
    if (positionAccessor === undefined) {
      throw new Error("Primitive is missing POSITION.");
    }
    const positionRead = readAccessor(json, bin, positionAccessor, "VEC3");
    if (positionRead.componentType !== COMPONENT_FLOAT) {
      throw new Error("POSITION must be float32.");
    }
    const rawPositions = positionRead.data as Float32Array;
    const vertexCount = positionRead.count;

    // Indices (or synthesize sequential ones), rebased onto the merged buffer.
    let indices: Uint32Array;
    if (primitive.indices !== undefined) {
      const indexRead = readAccessor(json, bin, primitive.indices, "SCALAR");
      const raw = indexRead.data as Uint32Array | Uint16Array | Uint8Array;
      indices = new Uint32Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) indices[i] = raw[i]! + vertexOffset;
    } else {
      indices = new Uint32Array(vertexCount);
      for (let i = 0; i < vertexCount; i += 1) indices[i] = i + vertexOffset;
    }

    // Normals: read or compute (indexed accumulation), then rotate into world.
    const normalAccessor = primitive.attributes["NORMAL"];
    const rawNormals =
      normalAccessor !== undefined
        ? (readAccessor(json, bin, normalAccessor, "VEC3").data as Float32Array)
        : computeSmoothNormals(rawPositions, indices, vertexOffset, vertexCount);

    const positions = new Float32Array(vertexCount * 3);
    const normals = new Float32Array(vertexCount * 3);
    for (let v = 0; v < vertexCount; v += 1) {
      const p = transformPoint(
        worldMatrix,
        rawPositions[v * 3]!,
        rawPositions[v * 3 + 1]!,
        rawPositions[v * 3 + 2]!,
      );
      positions[v * 3] = p[0];
      positions[v * 3 + 1] = p[1];
      positions[v * 3 + 2] = p[2];
      const n = transformDirection(
        worldMatrix,
        rawNormals[v * 3]!,
        rawNormals[v * 3 + 1]!,
        rawNormals[v * 3 + 2]!,
      );
      normals[v * 3] = n[0];
      normals[v * 3 + 1] = n[1];
      normals[v * 3 + 2] = n[2];
    }

    positionChunks.push(positions);
    normalChunks.push(normals);
    indexChunks.push(indices);
    vertexOffset += vertexCount;

    if (!authoredMaterial && primitive.material !== undefined) {
      const pbr = json.materials?.[primitive.material]?.pbrMetallicRoughness;
      if (pbr) {
        const [r, g, b, a] = pbr.baseColorFactor ?? [1, 1, 1, 1];
        authoredMaterial = {
          baseColorFactor: [r ?? 1, g ?? 1, b ?? 1, a ?? 1],
          metallicFactor: pbr.metallicFactor ?? 1,
          roughnessFactor: pbr.roughnessFactor ?? 1,
        };
      }
    }
  }

  const positions = concat(Float32Array, positionChunks);
  const normals = concat(Float32Array, normalChunks);
  const indices = concat(Uint32Array, indexChunks);
  return {
    positions,
    normals,
    indices,
    bounds: computeBounds(positions),
    triangleCount: indices.length / 3,
    authoredMaterial,
  };
}

/** Smooth normals for one primitive; indices are already rebased, so subtract the offset. */
function computeSmoothNormals(
  positions: Float32Array,
  rebasedIndices: Uint32Array,
  vertexOffset: number,
  vertexCount: number,
): Float32Array {
  const normals = new Float32Array(vertexCount * 3);
  for (let i = 0; i < rebasedIndices.length; i += 3) {
    const a = rebasedIndices[i]! - vertexOffset;
    const b = rebasedIndices[i + 1]! - vertexOffset;
    const c = rebasedIndices[i + 2]! - vertexOffset;
    const ax = positions[a * 3]!, ay = positions[a * 3 + 1]!, az = positions[a * 3 + 2]!;
    const ux = positions[b * 3]! - ax, uy = positions[b * 3 + 1]! - ay, uz = positions[b * 3 + 2]! - az;
    const vx = positions[c * 3]! - ax, vy = positions[c * 3 + 1]! - ay, vz = positions[c * 3 + 2]! - az;
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    for (const vertex of [a, b, c]) {
      normals[vertex * 3] = normals[vertex * 3]! + nx;
      normals[vertex * 3 + 1] = normals[vertex * 3 + 1]! + ny;
      normals[vertex * 3 + 2] = normals[vertex * 3 + 2]! + nz;
    }
  }
  for (let v = 0; v < vertexCount; v += 1) {
    const x = normals[v * 3]!, y = normals[v * 3 + 1]!, z = normals[v * 3 + 2]!;
    const length = Math.sqrt(x * x + y * y + z * z) || 1;
    normals[v * 3] = x / length;
    normals[v * 3 + 1] = y / length;
    normals[v * 3 + 2] = z / length;
  }
  return normals;
}

function concat<T extends Float32Array | Uint32Array>(
  Ctor: new (length: number) => T,
  chunks: T[],
): T {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Ctor(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk as never, offset);
    offset += chunk.length;
  }
  return out;
}
