import type { ArchitectureDocument, ArchitectureEdge } from './diagram-schema';

export type DiagramPoint = { x: number; y: number };

export type NodeRect = {
	left: number;
	top: number;
	width: number;
	height: number;
};

export type StaticEdgeKind = 'synchronous' | 'asynchronous' | 'data';

export type StaticEdge = {
	id: string;
	edge: ArchitectureEdge;
	kind: StaticEdgeKind;
	path: string;
};

function pathFromPoints(points: DiagramPoint[]) {
	return points
		.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
		.join(' ');
}

function center(rect: NodeRect) {
	return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}


function edgeKind(edge: ArchitectureEdge): StaticEdgeKind {
	if (edge.flow === 'asynchronous' || edge.protocol === 'queue' || edge.protocol === 'event') {
		return 'asynchronous';
	}

	if (edge.protocol === 'database' || edge.protocol === 'storage' || edge.protocol === 'file') {
		return 'data';
	}

	return 'synchronous';
}

function routeDesktopEdge(
	source: NodeRect,
	target: NodeRect,
	bounds: NodeRect,
	feedbackIndex: number,
	topFeedbackIndex: number,
	parallelIndex: number,
	parallelCount: number,
	routeIndex: number,
): DiagramPoint[] {
	const sourceCenter = center(source);
	const targetCenter = center(target);
	const isSameRow = Math.abs(targetCenter.y - sourceCenter.y) < Math.max(source.height, target.height) * 0.65;

	if (targetCenter.x > sourceCenter.x && isSameRow) {
		if (parallelCount > 1) {
			const laneOffset = (parallelIndex - (parallelCount - 1) / 2) * 18;
			const sourceLaneX = source.left + source.width + 14;
			const targetLaneX = target.left - 14;
			return [
				{ x: source.left + source.width, y: sourceCenter.y },
				{ x: sourceLaneX, y: sourceCenter.y },
				{ x: sourceLaneX, y: sourceCenter.y + laneOffset },
				{ x: targetLaneX, y: targetCenter.y + laneOffset },
				{ x: targetLaneX, y: targetCenter.y },
				{ x: target.left, y: targetCenter.y },
			];
		}
		return [
			{ x: source.left + source.width, y: sourceCenter.y },
			{ x: target.left, y: targetCenter.y },
		];
	}

	if (targetCenter.x <= sourceCenter.x && isSameRow) {
		const laneY = Math.max(8, Math.min(source.top, target.top) - 18 - topFeedbackIndex * 24);
		return [
			{ x: sourceCenter.x, y: source.top },
			{ x: sourceCenter.x, y: laneY },
			{ x: targetCenter.x, y: laneY },
			{ x: targetCenter.x, y: target.top },
		];
	}

	if (targetCenter.y > sourceCenter.y) {
		if (Math.abs(targetCenter.x - sourceCenter.x) < Math.max(source.width, target.width) * 0.35) {
			return [
				{ x: sourceCenter.x, y: source.top + source.height },
				{ x: targetCenter.x, y: target.top },
			];
		}

		const sourceBottom = source.top + source.height;
		const availableGap = Math.max(24, target.top - sourceBottom);
		const laneFractions = [0.28, 0.5, 0.72];
		const laneY = sourceBottom + availableGap * laneFractions[routeIndex % laneFractions.length];
		return [
			{ x: sourceCenter.x, y: sourceBottom },
			{ x: sourceCenter.x, y: laneY },
			{ x: targetCenter.x, y: laneY },
			{ x: targetCenter.x, y: target.top },
		];
	}

	const laneY = bounds.height - 18 - feedbackIndex * 26;
	return [
		{ x: sourceCenter.x, y: source.top + source.height },
		{ x: sourceCenter.x, y: laneY },
		{ x: targetCenter.x, y: laneY },
		{ x: targetCenter.x, y: target.top + target.height },
	];
}

function routeMobileEdge(
	source: NodeRect,
	target: NodeRect,
	bounds: NodeRect,
	index: number,
	isNarrativeStep: boolean,
): DiagramPoint[] {
	const sourceCenter = center(source);
	const targetCenter = center(target);
	if (isNarrativeStep) {
		return [
			{ x: sourceCenter.x, y: source.top + source.height },
			{ x: targetCenter.x, y: target.top },
		];
	}
	const laneX = bounds.width - 10 - (index % 3) * 15;
	if (targetCenter.y > sourceCenter.y) {
		const approachY = target.top - 18;
		return [
			{ x: source.left + source.width, y: sourceCenter.y },
			{ x: laneX, y: sourceCenter.y },
			{ x: laneX, y: approachY },
			{ x: targetCenter.x, y: approachY },
			{ x: targetCenter.x, y: target.top },
		];
	}

	return [
		{ x: source.left + source.width, y: sourceCenter.y },
		{ x: laneX, y: sourceCenter.y },
		{ x: laneX, y: targetCenter.y },
		{ x: target.left + target.width, y: targetCenter.y },
	];
}


export function buildStaticEdges(
	document: ArchitectureDocument,
	nodeRects: Map<string, NodeRect>,
	bounds: NodeRect,
	isMobile: boolean,
): StaticEdge[] {
	let feedbackIndex = 0;
	let topFeedbackIndex = 0;
	const nodesById = new Map(document.nodes.map((node) => [node.id, node]));
	const edgesByPair = new Map<string, ArchitectureDocument['edges']>();
	document.edges.forEach((edge) => {
		const pair = `${edge.source}:${edge.target}`;
		edgesByPair.set(pair, [...(edgesByPair.get(pair) ?? []), edge]);
	});

	return document.edges.flatMap((edge, index) => {
		const source = nodeRects.get(edge.source);
		const target = nodeRects.get(edge.target);
		const sourceNode = nodesById.get(edge.source);
		const targetNode = nodesById.get(edge.target);
		if (!source || !target || !sourceNode || !targetNode) return [];

		const sourceCenter = center(source);
		const targetCenter = center(target);
		const feedback = !isMobile && targetCenter.x <= sourceCenter.x;
		const sameRowFeedback = feedback
			&& Math.abs(targetCenter.y - sourceCenter.y) < Math.max(source.height, target.height) * 0.65;
		const parallelEdges = edgesByPair.get(`${edge.source}:${edge.target}`) ?? [edge];
		const parallelIndex = parallelEdges.findIndex((candidate) => candidate.id === edge.id);
		const narrativeStep = targetNode.mobileOrder === sourceNode.mobileOrder + 1;
		const points = isMobile
			? routeMobileEdge(source, target, bounds, index, narrativeStep)
			: routeDesktopEdge(source, target, bounds, feedbackIndex, topFeedbackIndex, parallelIndex, parallelEdges.length, index);
		if (sameRowFeedback) {
			topFeedbackIndex += 1;
		} else if (feedback) {
			feedbackIndex += 1;
		}
		return [{
			id: edge.id,
			edge,
			kind: edgeKind(edge),
			path: pathFromPoints(points),
		}];
	});
}
