import type { XYPosition } from '@xyflow/react';

import type { ArchitectureDocument } from './diagram-schema';

export const ARCHITECTURE_LAYOUT_STORAGE_PREFIX =
	'manifest-docs:architecture-layout:v1:';
export const ARCHITECTURE_LAYOUT_VERSION = 1 as const;

export type ArchitectureLayoutPositions = Record<string, XYPosition>;

interface PersistedArchitectureLayout {
	version: typeof ARCHITECTURE_LAYOUT_VERSION;
	signature: string;
	positions: ArchitectureLayoutPositions;
}

function compareIds(left: string, right: string) {
	if (left === right) return 0;
	return left < right ? -1 : 1;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasExactKeys(
	value: Record<string, unknown>,
	expectedKeys: readonly string[],
) {
	const actualKeys = Object.keys(value).sort(compareIds);
	const sortedExpectedKeys = [...expectedKeys].sort(compareIds);
	return actualKeys.length === sortedExpectedKeys.length
		&& actualKeys.every((key, index) => key === sortedExpectedKeys[index]);
}

function normalizePositions(
	document: ArchitectureDocument,
	value: unknown,
): ArchitectureLayoutPositions | null {
	if (!isRecord(value)) return null;

	const nodeIds = document.nodes.map((node) => node.id).sort(compareIds);
	if (!hasExactKeys(value, nodeIds)) return null;

	const positions: ArchitectureLayoutPositions = {};
	for (const id of nodeIds) {
		const position = value[id];
		if (
			!isRecord(position)
			|| !hasExactKeys(position, ['x', 'y'])
			|| typeof position.x !== 'number'
			|| !Number.isFinite(position.x)
			|| typeof position.y !== 'number'
			|| !Number.isFinite(position.y)
		) {
			return null;
		}

		positions[id] = { x: position.x, y: position.y };
	}

	return positions;
}

export function getArchitectureLayoutStorageKey(documentId: string) {
	return `${ARCHITECTURE_LAYOUT_STORAGE_PREFIX}${documentId}`;
}

export function createArchitectureLayoutSignature(
	document: ArchitectureDocument,
) {
	const authoredNodes = document.nodes
		.map((node) => [
			node.id,
			node.position.column,
			node.position.row,
		] as const)
		.sort((left, right) => compareIds(left[0], right[0]));

	return JSON.stringify(authoredNodes);
}

/**
 * Produces a deterministic, storage-ready payload. Browser storage remains a
 * caller concern so serialization and validation stay pure and testable.
 */
export function serializeArchitectureLayout(
	document: ArchitectureDocument,
	positions: ArchitectureLayoutPositions,
) {
	const normalizedPositions = normalizePositions(document, positions);
	if (!normalizedPositions) {
		throw new TypeError(
			'Architecture layout positions must contain finite coordinates for the exact document node set.',
		);
	}

	const payload: PersistedArchitectureLayout = {
		version: ARCHITECTURE_LAYOUT_VERSION,
		signature: createArchitectureLayoutSignature(document),
		positions: normalizedPositions,
	};

	return JSON.stringify(payload);
}

/**
 * Restores only exact, current-document layouts. Corrupt, stale, incomplete,
 * or future-version payloads are ignored instead of partially repairing them.
 */
export function restoreArchitectureLayout(
	document: ArchitectureDocument,
	serialized: string | null | undefined,
): ArchitectureLayoutPositions | null {
	if (typeof serialized !== 'string') return null;

	let parsed: unknown;
	try {
		parsed = JSON.parse(serialized);
	} catch {
		return null;
	}

	if (
		!isRecord(parsed)
		|| !hasExactKeys(parsed, ['version', 'signature', 'positions'])
		|| parsed.version !== ARCHITECTURE_LAYOUT_VERSION
		|| parsed.signature !== createArchitectureLayoutSignature(document)
	) {
		return null;
	}

	return normalizePositions(document, parsed.positions);
}
