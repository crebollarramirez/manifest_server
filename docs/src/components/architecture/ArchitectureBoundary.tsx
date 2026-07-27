import type { NodeProps } from '@xyflow/react';

import type { ArchitectureBoundaryFlowNode } from './layout-graph';

const BOUNDARY_KIND_LABELS: Record<
	ArchitectureBoundaryFlowNode['data']['boundary']['kind'],
	string
> = {
	service: 'Service boundary',
	worker: 'Worker boundary',
	runtime: 'Runtime boundary',
	trust: 'Trust boundary',
	transaction: 'Transaction boundary',
};

export default function ArchitectureBoundary({
	data,
}: NodeProps<ArchitectureBoundaryFlowNode>) {
	const boundary = data.boundary;
	return (
		<section
			className={`architecture-boundary architecture-boundary--${boundary.kind}`}
			aria-label={`${BOUNDARY_KIND_LABELS[boundary.kind]}: ${boundary.label}`}
		>
			<header>
				<span>{BOUNDARY_KIND_LABELS[boundary.kind]}</span>
				<strong>{boundary.label}</strong>
			</header>
			{boundary.description ? (
				<p className="architecture-visually-hidden">{boundary.description}</p>
			) : null}
		</section>
	);
}
