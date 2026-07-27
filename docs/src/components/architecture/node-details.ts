import type { ArchitectureNode, ArchitectureNodeDetails } from './diagram-schema';

export type NodeInspectorSectionId =
	| 'when'
	| 'contract'
	| 'logic'
	| 'outcomes'
	| 'guarantees'
	| 'operations'
	| 'source';

export interface NodeInspectorSection {
	id: NodeInspectorSectionId;
	title: string;
	defaultOpen: boolean;
}

export function getVisibleNodeInspectorSections(
	node: ArchitectureNode,
): NodeInspectorSection[] {
	const details = node.details;
	if (!details) return [];

	const sections: Array<[NodeInspectorSection, boolean]> = [
		[
			{ id: 'when', title: 'When it runs', defaultOpen: true },
			Boolean(details.trigger?.length || details.preconditions?.length),
		],
		[
			{ id: 'contract', title: 'Contract', defaultOpen: true },
			Boolean(
				details.inputs?.length
				|| details.outputs?.length
				|| details.durableReads?.length
				|| details.durableWrites?.length
				|| details.downstream?.length,
			),
		],
		[
			{ id: 'logic', title: 'Logic', defaultOpen: false },
			Boolean(
				details.steps?.length
				|| details.decisionPoints?.length
				|| details.shortCircuit,
			),
		],
		[
			{ id: 'outcomes', title: 'Outcomes', defaultOpen: false },
			Boolean(details.successCondition || details.failureConditions?.length),
		],
		[
			{ id: 'guarantees', title: 'Guarantees', defaultOpen: false },
			Boolean(details.guarantees?.length),
		],
		[
			{ id: 'operations', title: 'Operations', defaultOpen: false },
			Boolean(details.operations),
		],
		[
			{ id: 'source', title: 'Source', defaultOpen: false },
			Boolean(details.evidence?.length),
		],
	];

	return sections.filter(([, visible]) => visible).map(([section]) => section);
}

export function nodeResponsibility(node: ArchitectureNode): string {
	return node.details?.responsibility ?? node.description;
}

export function hasOperationContent(
	operations: NonNullable<ArchitectureNodeDetails['operations']>,
) {
	return Boolean(
		operations.timeout
			|| operations.concurrency
			|| operations.retry
			|| operations.observability?.length
			|| operations.controls?.length,
	);
}

export function isNodeActivationKey(key: string) {
	return key === 'Enter' || key === ' ';
}
