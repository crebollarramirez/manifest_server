import type { ArchitectureEdge } from './diagram-schema';

const PROTOCOL_LABELS: Record<
	NonNullable<ArchitectureEdge['protocol']>,
	string
> = {
	http: 'HTTP',
	event: 'Event',
	queue: 'Queue',
	database: 'Database',
	storage: 'Storage',
	file: 'File',
	internal: 'Internal',
};

export function getConnectionDetails(edge: ArchitectureEdge) {
	const protocol = edge.protocol ?? 'internal';
	const flow = edge.flow ?? 'synchronous';
	const direction = edge.direction ?? 'one-way';

	return {
		title: edge.label ?? `${PROTOCOL_LABELS[protocol]} connection`,
		summary: edge.details?.summary,
		preconditions: edge.details?.preconditions ?? [],
		operation: edge.details?.operation,
		payload: edge.details?.payload,
		durability: edge.details?.durability,
		failureBehavior: edge.details?.failureBehavior,
		trustBoundary: edge.details?.trustBoundary,
		evidence: edge.details?.evidence ?? [],
		protocol: PROTOCOL_LABELS[protocol],
		flow: flow === 'asynchronous' ? 'Asynchronous' : 'Synchronous',
		direction: direction === 'two-way' ? 'Two-way' : 'One-way',
	};
}
