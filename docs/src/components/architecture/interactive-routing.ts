import { Position, type XYPosition } from '@xyflow/react';

import type {
	ArchitectureDocument,
	ArchitectureEdge,
} from './diagram-schema';

export const INTERACTIVE_NODE_WIDTH = 180;
export const INTERACTIVE_NODE_HEIGHT = 122;
export const INTERACTIVE_COLUMN_GAP = 70;
export const INTERACTIVE_ROW_GAP = 68;
export const INTERACTIVE_CANVAS_PADDING = 54;

export type ArchitectureHandleKind = 'source' | 'target';
export type InteractiveRouteOrientation = 'horizontal' | 'vertical';
export type DirectionMarker = '→' | '←' | '↑' | '↓';

export interface InteractiveNodeGeometry {
	id: string;
	position: XYPosition;
	positionAbsolute?: XYPosition;
	width?: number;
	height?: number;
	measured?: {
		width?: number;
		height?: number;
	};
}

export interface InteractiveHandleSelection {
	orientation: InteractiveRouteOrientation;
	sourcePosition: Position;
	targetPosition: Position;
	sourceHandleId: string;
	targetHandleId: string;
}

export interface InteractiveEdgeRoute extends InteractiveHandleSelection {
	edgeId: string;
	/**
	 * Zero uses the direct nearest-side route. Non-zero values identify an
	 * exterior lane; the sign selects the exterior side and the magnitude
	 * provides a stable lane number for the edge renderer.
	 */
	routeSlot: number;
	marker: DirectionMarker;
}

function compareIds(left: string, right: string) {
	if (left === right) return 0;
	return left < right ? -1 : 1;
}

function unorderedPairKey(source: string, target: string) {
	return JSON.stringify([source, target].sort(compareIds));
}

function routeSlotForIndex(index: number) {
	if (index === 0) return 0;
	const magnitude = Math.ceil(index / 2);
	return index % 2 === 1 ? magnitude : -magnitude;
}

function dimensionsForNode(node: InteractiveNodeGeometry) {
	return {
		width: node.measured?.width ?? node.width ?? INTERACTIVE_NODE_WIDTH,
		height: node.measured?.height ?? node.height ?? INTERACTIVE_NODE_HEIGHT,
	};
}

export function buildInitialNodePositions(
	document: ArchitectureDocument,
): Record<string, XYPosition> {
	return Object.fromEntries(document.nodes.map((node) => [
		node.id,
		{
			x: INTERACTIVE_CANVAS_PADDING
				+ node.position.column * (INTERACTIVE_NODE_WIDTH + INTERACTIVE_COLUMN_GAP),
			y: INTERACTIVE_CANVAS_PADDING
				+ node.position.row * (INTERACTIVE_NODE_HEIGHT + INTERACTIVE_ROW_GAP),
		},
	]));
}

export function getInteractiveNodeCenter(
	node: InteractiveNodeGeometry,
): XYPosition {
	const position = node.positionAbsolute ?? node.position;
	const dimensions = dimensionsForNode(node);

	return {
		x: position.x + dimensions.width / 2,
		y: position.y + dimensions.height / 2,
	};
}

export function getArchitectureHandleId(
	kind: ArchitectureHandleKind,
	position: Position,
) {
	return `${kind}-${position}`;
}

function orientationBetween(
	sourceCenter: XYPosition,
	targetCenter: XYPosition,
): InteractiveRouteOrientation {
	const horizontalDistance = Math.abs(targetCenter.x - sourceCenter.x);
	const verticalDistance = Math.abs(targetCenter.y - sourceCenter.y);
	return horizontalDistance >= verticalDistance ? 'horizontal' : 'vertical';
}

function handleSelection(
	orientation: InteractiveRouteOrientation,
	sourcePosition: Position,
	targetPosition: Position,
): InteractiveHandleSelection {
	return {
		orientation,
		sourcePosition,
		targetPosition,
		sourceHandleId: getArchitectureHandleId('source', sourcePosition),
		targetHandleId: getArchitectureHandleId('target', targetPosition),
	};
}

export function chooseNearestHandles(
	source: InteractiveNodeGeometry,
	target: InteractiveNodeGeometry,
): InteractiveHandleSelection {
	const sourceCenter = getInteractiveNodeCenter(source);
	const targetCenter = getInteractiveNodeCenter(target);
	const orientation = orientationBetween(sourceCenter, targetCenter);

	if (orientation === 'horizontal') {
		return targetCenter.x >= sourceCenter.x
			? handleSelection(orientation, Position.Right, Position.Left)
			: handleSelection(orientation, Position.Left, Position.Right);
	}

	return targetCenter.y >= sourceCenter.y
		? handleSelection(orientation, Position.Bottom, Position.Top)
		: handleSelection(orientation, Position.Top, Position.Bottom);
}

function chooseExteriorHandles(
	source: InteractiveNodeGeometry,
	target: InteractiveNodeGeometry,
	routeSlot: number,
): InteractiveHandleSelection {
	const sourceCenter = getInteractiveNodeCenter(source);
	const targetCenter = getInteractiveNodeCenter(target);
	const orientation = orientationBetween(sourceCenter, targetCenter);

	if (orientation === 'horizontal') {
		const position = routeSlot > 0 ? Position.Bottom : Position.Top;
		return handleSelection(orientation, position, position);
	}

	const position = routeSlot > 0 ? Position.Right : Position.Left;
	return handleSelection(orientation, position, position);
}

export function getDirectionMarker(
	sourceCenter: XYPosition,
	targetCenter: XYPosition,
	_direction: ArchitectureEdge['direction'] = 'one-way',
): DirectionMarker {
	const orientation = orientationBetween(sourceCenter, targetCenter);

	if (orientation === 'horizontal') {
		return targetCenter.x >= sourceCenter.x ? '→' : '←';
	}

	return targetCenter.y >= sourceCenter.y ? '↓' : '↑';
}

/**
 * Assigns each relationship a live handle pair and a stable route slot.
 * Relationships between the same unordered node pair share a group, so a
 * reverse edge cannot collapse onto the direct path of the first edge.
 */
export function assignInteractiveEdgeRoutes(
	edges: readonly ArchitectureEdge[],
	nodes: readonly InteractiveNodeGeometry[],
): Map<string, InteractiveEdgeRoute> {
	const nodesById = new Map(nodes.map((node) => [node.id, node]));
	const pairIndexes = new Map<string, number>();
	const routes = new Map<string, InteractiveEdgeRoute>();

	edges.forEach((edge) => {
		const source = nodesById.get(edge.source);
		const target = nodesById.get(edge.target);

		if (!source || !target) {
			throw new Error(
				`Cannot route edge "${edge.id}" without both "${edge.source}" and "${edge.target}" node geometry.`,
			);
		}

		const pairKey = unorderedPairKey(edge.source, edge.target);
		const pairIndex = pairIndexes.get(pairKey) ?? 0;
		const routeSlot = routeSlotForIndex(pairIndex);
		pairIndexes.set(pairKey, pairIndex + 1);

		const handles = routeSlot === 0
			? chooseNearestHandles(source, target)
			: chooseExteriorHandles(source, target, routeSlot);
		const sourceCenter = getInteractiveNodeCenter(source);
		const targetCenter = getInteractiveNodeCenter(target);

		routes.set(edge.id, {
			edgeId: edge.id,
			routeSlot,
			...handles,
			marker: getDirectionMarker(sourceCenter, targetCenter, edge.direction),
		});
	});

	return routes;
}
