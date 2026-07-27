import { ArchitectureIcon } from './ArchitectureIcon';
import type { ArchitectureIconName, ArchitectureNode } from './diagram-schema';

/**
 * Visual metadata is intentionally separate from individual diagrams. New
 * architecture documents only describe nodes in JSON; this primitive applies
 * the shared chrome, semantic family, and accessible copy everywhere.
 */
export const DIAGRAM_BOX_TYPES = {
	layer: { label: 'Layer', family: 'boundary', icon: 'layers' },
	client: { label: 'Client', family: 'entry', icon: 'terminal' },
	api: { label: 'API', family: 'entry', icon: 'api' },
	function: { label: 'Process', family: 'compute', icon: 'process' },
	service: { label: 'Service', family: 'compute', icon: 'service' },
	worker: { label: 'Worker', family: 'worker', icon: 'worker' },
	database: { label: 'Database', family: 'state', icon: 'database' },
	queue: { label: 'Queue', family: 'state', icon: 'queue' },
	storage: { label: 'Storage', family: 'state', icon: 'object-storage' },
	external: { label: 'External', family: 'boundary', icon: 'external' },
} as const;

export type DiagramBoxType = keyof typeof DIAGRAM_BOX_TYPES;

export function diagramBoxIcon(node: ArchitectureNode): ArchitectureIconName {
	return node.icon ?? DIAGRAM_BOX_TYPES[node.type].icon;
}

export function diagramBoxClassName(node: ArchitectureNode) {
	const type = DIAGRAM_BOX_TYPES[node.type];

	return [
		'architecture-node',
		'architecture-box',
		`architecture-node--${node.type}`,
		`architecture-box--${type.family}`,
		node.variant ? `architecture-node--variant-${node.variant}` : '',
		node.foundation ? 'architecture-node--foundation' : '',
		node.href && !node.foundation ? 'architecture-node--linked' : '',
	]
		.filter(Boolean)
		.join(' ');
}

export function DiagramBoxContents({
	node,
	showLinkStatus = true,
}: {
	node: ArchitectureNode;
	/**
	 * Static cards use the passive "open" status as a link affordance. Interactive
	 * React Flow cards suppress it because they render a separate native anchor.
	 */
	showLinkStatus?: boolean;
}) {
	const type = DIAGRAM_BOX_TYPES[node.type];

	return (
		<>
			<span className="architecture-box__stripe" aria-hidden="true" />
			{node.foundation ? (
				<span className="architecture-box__status" aria-hidden="true">terminal</span>
			) : node.href && showLinkStatus ? (
				<span className="architecture-box__status" aria-hidden="true">open →</span>
			) : null}
			<span className="architecture-box__glyph" aria-hidden="true">
				<ArchitectureIcon name={diagramBoxIcon(node)} />
			</span>
			<span className="architecture-node__kind" aria-hidden="true">
				{type.label}
			</span>
			<strong className="architecture-node__label">{node.label}</strong>
			{node.contents?.length ? (
				<span className="architecture-box__contents" aria-label={`Stored contents: ${node.contents.join(', ')}`}>
					{node.contents.map((content) => (
						<span className="architecture-box__content" key={content}>
							<ArchitectureIcon name="file-json" />
							<span>{content}</span>
						</span>
					))}
				</span>
			) : null}
			<span className="architecture-node__description">{node.description}</span>
		</>
	);
}
