import type { ArchitectureEdge } from './diagram-schema';

export type ArchitectureEdgeKind = 'synchronous' | 'asynchronous' | 'data';
export type ArchitectureDirectionMarker = '→' | '←' | '↑' | '↓';

export interface ArchitectureEdgeFocusState {
	dimmed?: boolean;
	emphasized?: boolean;
}

export function getArchitectureEdgeKind(
	edge: ArchitectureEdge,
): ArchitectureEdgeKind {
	if (
		edge.flow === 'asynchronous'
		|| edge.protocol === 'queue'
		|| edge.protocol === 'event'
	) {
		return 'asynchronous';
	}

	if (
		edge.protocol === 'database'
		|| edge.protocol === 'storage'
		|| edge.protocol === 'file'
	) {
		return 'data';
	}

	return 'synchronous';
}

export function getArchitectureDirectionMarker({
	sourceX,
	sourceY,
	targetX,
	targetY,
	direction: _direction,
}: {
	sourceX: number;
	sourceY: number;
	targetX: number;
	targetY: number;
	direction: ArchitectureEdge['direction'];
}): ArchitectureDirectionMarker {
	const horizontal = Math.abs(targetX - sourceX) >= Math.abs(targetY - sourceY);

	if (horizontal) return targetX >= sourceX ? '→' : '←';
	return targetY >= sourceY ? '↓' : '↑';
}

/**
 * Pure class/marker contract shared by the renderer and dependency-free tests.
 * CSS supplies motion, while this contract guarantees that directionality and
 * focus state remain present when paths are recalculated.
 */
export function getArchitectureEdgeRenderContract(
	edge: ArchitectureEdge,
	focus: ArchitectureEdgeFocusState = {},
) {
	const kind = getArchitectureEdgeKind(edge);
	const isTwoWay = edge.direction === 'two-way';
	const stateClasses = [
		focus.dimmed ? 'architecture-edge--dimmed' : '',
		focus.emphasized ? 'architecture-edge--emphasized' : '',
		isTwoWay ? 'architecture-edge--two-way' : 'architecture-edge--one-way',
	]
		.filter(Boolean)
		.join(' ');

	return {
		kind,
		isTwoWay,
		hasSourceArrow: false,
		hasTargetArrow: true,
		baseClass: `architecture-edge architecture-edge--${kind} ${stateClasses}`.trim(),
		forwardMotionClass:
			`architecture-edge__motion architecture-edge__motion--forward architecture-edge__motion--${kind} ${stateClasses}`.trim(),
		reverseMotionClass: null,
	};
}
