import {
	Handle,
	Position,
	type HandleType,
	type NodeProps,
} from '@xyflow/react';

import type { ArchitectureFlowNode } from './layout-graph';
import type { ArchitectureNode as ArchitectureNodeContract } from './diagram-schema';
import {
	DiagramBoxContents,
	diagramBoxClassName,
} from './DiagramBox';
import { isNodeActivationKey } from './node-details';

const NODE_TYPE_LABELS: Record<ArchitectureNodeContract['type'], string> = {
	layer: 'Architecture layer',
	client: 'Client',
	api: 'API',
	function: 'Processing stage',
	service: 'Service',
	worker: 'Worker',
	database: 'Database',
	queue: 'Queue',
	storage: 'Storage',
	external: 'External system',
};

const CARDINAL_HANDLES: ReadonlyArray<{
	name: 'top' | 'right' | 'bottom' | 'left';
	position: Position;
}> = [
	{ name: 'top', position: Position.Top },
	{ name: 'right', position: Position.Right },
	{ name: 'bottom', position: Position.Bottom },
	{ name: 'left', position: Position.Left },
];

function CardinalHandles({ type }: { type: HandleType }) {
	return CARDINAL_HANDLES.map(({ name, position }) => (
		<Handle
			key={`${type}-${name}`}
			id={`${type}-${name}`}
			className="architecture-node__handle"
			type={type}
			position={position}
			isConnectable={false}
		/>
	));
}

/**
 * One semantic node renderer is deliberately used for every architecture type.
 * The contract type is exposed as a class so CSS can give each component its
 * conventional system-design silhouette.
 */
export function ArchitectureNode({
	data,
}: NodeProps<ArchitectureFlowNode>) {
	const node = data.node;
	const className = diagramBoxClassName(node);

	return (
		<>
			{/* Live routing selects the nearest cardinal edge after every drag.
			    Source and target handles overlap visually and stay hidden. */}
			<CardinalHandles type="target" />
			<CardinalHandles type="source" />
			<article
				className={className}
				data-node-type={node.type}
				data-node-variant={node.variant}
				aria-label={`${NODE_TYPE_LABELS[node.type]}: ${node.label}`}
				role="button"
				tabIndex={0}
				aria-haspopup="dialog"
				onKeyDown={(event) => {
					if (!isNodeActivationKey(event.key)) return;
					event.preventDefault();
					data.onInspect?.(node.id);
				}}
			>
				<div className="architecture-node__body">
					<DiagramBoxContents node={node} showLinkStatus={false} />
				</div>
				{node.href && !node.foundation ? (
					<a
						className="architecture-node__open-link nodrag nopan"
						href={node.href}
					aria-label={`Open architecture details for ${node.label}`}
					onPointerDown={(event) => event.stopPropagation()}
					onClick={(event) => event.stopPropagation()}
					onKeyDown={(event) => {
						event.stopPropagation();
						if (event.key === 'Enter') {
							event.preventDefault();
							window.location.assign(event.currentTarget.href);
						}
					}}
				>
						<span aria-hidden="true">↗</span>
					</a>
				) : null}
			</article>
		</>
	);
}

export default ArchitectureNode;
