import type { Edge, Node, XYPosition } from '@xyflow/react';

import type {
	ArchitectureDocument,
	ArchitectureBoundary,
	ArchitectureEdge,
	ArchitectureNode,
	ArchitectureNodeType,
} from './diagram-schema';
import {
	assignInteractiveEdgeRoutes,
	buildInitialNodePositions,
	INTERACTIVE_CANVAS_PADDING,
	INTERACTIVE_NODE_HEIGHT,
	INTERACTIVE_NODE_WIDTH,
	type DirectionMarker,
} from './interactive-routing.ts';
import type { ArchitectureLayoutPositions } from './layout-persistence';

export interface NodeDimensions {
	width: number;
	height: number;
}

/**
 * All cards currently share one readable systems-console footprint. Keeping
 * the dimensions keyed by semantic type leaves room for a future type that
 * genuinely needs a different authored size without adding page CSS.
 */
export const NODE_DIMENSIONS: Readonly<
	Record<ArchitectureNodeType, NodeDimensions>
> = {
	layer: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	client: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	api: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	function: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	service: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	worker: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	database: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	queue: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
	storage: { width: INTERACTIVE_NODE_WIDTH, height: 152 },
	external: { width: INTERACTIVE_NODE_WIDTH, height: INTERACTIVE_NODE_HEIGHT },
};

export type ArchitectureNodeData = {
	node: ArchitectureNode;
	onInspect?: (nodeId: string) => void;
};

export type ArchitectureBoundaryData = {
	boundary: ArchitectureBoundary;
};

/**
 * Edges carry only architecture meaning and live presentation state. React
 * Flow calculates the orthogonal path from the current node positions.
 */
export type ArchitectureEdgeData = {
	edge: ArchitectureEdge;
	routeSlot: number;
	marker: DirectionMarker;
	dimmed?: boolean;
	emphasized?: boolean;
	selected?: boolean;
};

export type ArchitectureComponentFlowNode = Node<
	ArchitectureNodeData,
	'architectureNode'
>;

export type ArchitectureBoundaryFlowNode = Node<
	ArchitectureBoundaryData,
	'architectureBoundary'
>;

export type ArchitectureFlowNode =
	| ArchitectureComponentFlowNode
	| ArchitectureBoundaryFlowNode;

export type ArchitectureFlowEdge = Edge<
	ArchitectureEdgeData,
	'architectureEdge'
>;

export interface LayoutGraph {
	nodes: ArchitectureFlowNode[];
	edges: ArchitectureFlowEdge[];
	width: number;
	height: number;
}

export function formatArchitectureEdgeLabel(edge: ArchitectureEdge): string {
	const protocol = edge.protocol === 'http' ? 'HTTP' : edge.protocol;
	const metadata = [protocol, edge.flow].filter(
		(value): value is string => value !== undefined,
	);

	return [edge.label, ...metadata]
		.filter((value): value is string => value !== undefined)
		.join(' · ');
}

function makeFlowNode(
	node: ArchitectureNode,
	position: XYPosition,
	onInspect?: (nodeId: string) => void,
): ArchitectureComponentFlowNode {
	const dimensions = NODE_DIMENSIONS[node.type];

	return {
		id: node.id,
		type: 'architectureNode',
		position,
		data: { node, onInspect },
		width: dimensions.width,
		height: dimensions.height,
		initialWidth: dimensions.width,
		initialHeight: dimensions.height,
		style: { width: dimensions.width, height: dimensions.height },
		draggable: true,
		selectable: false,
		connectable: false,
		deletable: false,
		focusable: false,
	};
}

export function createArchitectureFlowNodes(
	document: ArchitectureDocument,
	positions: ArchitectureLayoutPositions = buildInitialNodePositions(document),
	onInspect?: (nodeId: string) => void,
): ArchitectureComponentFlowNode[] {
	return document.nodes.map((node) => {
		const position = positions[node.id];
		if (!position) {
			throw new Error(
				`Cannot create architecture node "${node.id}" without a position.`,
			);
		}

		return makeFlowNode(node, position, onInspect);
	});
}

const BOUNDARY_PADDING = 32;
const BOUNDARY_HEADER = 34;

export function createArchitectureBoundaryNodes(
	document: ArchitectureDocument,
	nodes: readonly ArchitectureComponentFlowNode[],
): ArchitectureBoundaryFlowNode[] {
	const nodesById = new Map(nodes.map((node) => [node.id, node]));

	return (document.boundaries ?? []).map((boundary) => {
		const members = boundary.nodeIds
			.map((nodeId) => nodesById.get(nodeId))
			.filter((node): node is ArchitectureComponentFlowNode => node !== undefined);
		const left = Math.min(...members.map((node) => node.position.x));
		const top = Math.min(...members.map((node) => node.position.y));
		const right = Math.max(...members.map(
			(node) => node.position.x + NODE_DIMENSIONS[node.data.node.type].width,
		));
		const bottom = Math.max(...members.map(
			(node) => node.position.y + NODE_DIMENSIONS[node.data.node.type].height,
		));
		const nested = boundary.parentId !== undefined;
		const padding = nested ? 18 : BOUNDARY_PADDING;
		const header = nested ? 26 : BOUNDARY_HEADER;
		const width = right - left + padding * 2;
		const height = bottom - top + padding * 2 + header;

		return {
			id: boundary.id,
			type: 'architectureBoundary',
			position: { x: left - padding, y: top - padding - header },
			data: { boundary },
			width,
			height,
			initialWidth: width,
			initialHeight: height,
			style: { width, height },
			draggable: false,
			selectable: false,
			connectable: false,
			deletable: false,
			focusable: false,
			zIndex: nested ? -1 : -2,
		};
	});
}

export function createArchitectureFlowEdges(
	document: ArchitectureDocument,
	nodes: ArchitectureFlowNode[],
	focusedNodeId: string | null = null,
	selectedEdgeId: string | null = null,
): ArchitectureFlowEdge[] {
	const routes = assignInteractiveEdgeRoutes(
		document.edges,
		nodes.filter(
			(node): node is ArchitectureComponentFlowNode =>
				node.type === 'architectureNode',
		),
	);

	return document.edges.map((edge) => {
		const route = routes.get(edge.id);
		if (!route) {
			throw new Error(`No live route was assigned to edge "${edge.id}".`);
		}

		const isIncident =
			edge.source === focusedNodeId || edge.target === focusedNodeId;

		return {
			id: edge.id,
			type: 'architectureEdge',
			source: edge.source,
			target: edge.target,
			sourceHandle: route.sourceHandleId,
			targetHandle: route.targetHandleId,
			data: {
				edge,
				routeSlot: route.routeSlot,
				marker: route.marker,
				dimmed: focusedNodeId !== null && !isIncident,
				emphasized: focusedNodeId !== null && isIncident,
				selected: edge.id === selectedEdgeId,
			},
			reconnectable: false,
			selectable: true,
			deletable: false,
			focusable: true,
			ariaLabel: `${edge.label ?? 'Architecture connection'}: ${edge.source} to ${edge.target}`,
			zIndex: isIncident ? 2 : 0,
		};
	});
}

function graphBounds(nodes: ArchitectureFlowNode[]) {
	const width = Math.max(
		...nodes.map(
			(node) =>
				node.position.x + (
					node.type === 'architectureNode'
						? NODE_DIMENSIONS[node.data.node.type].width
						: node.width ?? 0
				),
		),
		INTERACTIVE_CANVAS_PADDING,
	);
	const height = Math.max(
		...nodes.map(
			(node) =>
				node.position.y + (
					node.type === 'architectureNode'
						? NODE_DIMENSIONS[node.data.node.type].height
						: node.height ?? 0
				),
		),
		INTERACTIVE_CANVAS_PADDING,
	);

	return {
		width: width + INTERACTIVE_CANVAS_PADDING,
		height: height + INTERACTIVE_CANVAS_PADDING,
	};
}

/**
 * Compatibility helper for tests and future automatic initial-layout work.
 * Authored grid coordinates are the only initial layout used today; no edge
 * path or connector coordinate is precomputed.
 */
export async function layoutArchitectureDocument(
	document: ArchitectureDocument,
): Promise<LayoutGraph> {
	const componentNodes = createArchitectureFlowNodes(document);
	const nodes = [
		...createArchitectureBoundaryNodes(document, componentNodes),
		...componentNodes,
	];
	const edges = createArchitectureFlowEdges(document, nodes);
	return { nodes, edges, ...graphBounds(nodes) };
}
