import {
	Fragment,
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
	type CSSProperties,
} from 'react';
import {
	Background,
	BackgroundVariant,
	Controls,
	Panel,
	ReactFlow,
	useNodesState,
	type NodeChange,
	type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../styles/architecture.css';

import {
	ArchitectureDocumentSchema,
	type ArchitectureDocument,
	type ArchitectureEdge as ArchitectureEdgeContract,
	type ArchitectureNode as ArchitectureNodeContract,
} from './diagram-schema';
import ArchitectureEdge from './ArchitectureEdge';
import ArchitectureBoundary from './ArchitectureBoundary';
import ArchitectureNode from './ArchitectureNode';
import ConnectionDetailsPanel from './ConnectionDetailsPanel';
import NodeDetailsPanel from './NodeDetailsPanel';
import { DiagramBoxContents, diagramBoxClassName } from './DiagramBox';
import {
	createArchitectureFlowEdges,
	createArchitectureBoundaryNodes,
	createArchitectureFlowNodes,
	type ArchitectureComponentFlowNode,
	type ArchitectureFlowEdge,
	type ArchitectureFlowNode,
} from './layout-graph';
import {
	getArchitectureLayoutStorageKey,
	restoreArchitectureLayout,
	serializeArchitectureLayout,
	type ArchitectureLayoutPositions,
} from './layout-persistence';
import { buildInitialNodePositions } from './interactive-routing';
import { buildStaticEdges, type NodeRect, type StaticEdgeKind } from './static-layout';

const NODE_TYPES = {
	architectureNode: ArchitectureNode,
	architectureBoundary: ArchitectureBoundary,
};
const EDGE_TYPES = { architectureEdge: ArchitectureEdge };
const FIT_VIEW_OPTIONS = { padding: 0.18, minZoom: 0.28, maxZoom: 1 };

const NODE_TYPE_LABELS: Record<ArchitectureNodeContract['type'], string> = {
	layer: 'Layer',
	client: 'Client',
	api: 'API',
	function: 'Process',
	service: 'Service',
	worker: 'Worker',
	database: 'Database',
	queue: 'Queue',
	storage: 'Storage',
	external: 'External',
};

const EDGE_KIND_LABELS: Record<StaticEdgeKind, string> = {
	synchronous: 'Synchronous call',
	asynchronous: 'Async / event flow',
	data: 'Data / storage flow',
};

export interface ArchitectureDiagramProps {
	/** Raw JSON is accepted so invalid architecture never reaches the renderer. */
	document: unknown;
	/** Service pages use the page title as the single visible diagram heading. */
	showHeader?: boolean;
}

function formatValidationPath(path: PropertyKey[]) {
	if (path.length === 0) return 'Document';
	return path
		.map((part) => (typeof part === 'number' ? `[${part}]` : String(part)))
		.join('.')
		.replace('.[', '[');
}

function ValidationError({
	issues,
}: {
	issues: Array<{ path: PropertyKey[]; message: string }>;
}) {
	const visibleIssues = issues.slice(0, 4);

	return (
		<section className="architecture-message architecture-message--error" role="alert">
			<strong>Architecture diagram could not be displayed</strong>
			<p>The architecture data does not match the diagram contract.</p>
			<ul>
				{visibleIssues.map((issue, index) => (
					<li key={`${formatValidationPath(issue.path)}-${index}`}>
						<span>{formatValidationPath(issue.path)}:</span> {issue.message}
					</li>
				))}
			</ul>
		</section>
	);
}

function edgeKind(edge: ArchitectureDocument['edges'][number]): StaticEdgeKind {
	if (edge.flow === 'asynchronous' || edge.protocol === 'queue' || edge.protocol === 'event') {
		return 'asynchronous';
	}
	if (edge.protocol === 'database' || edge.protocol === 'storage' || edge.protocol === 'file') {
		return 'data';
	}
	return 'synchronous';
}

function edgeDetails(edge: ArchitectureDocument['edges'][number]) {
	return `${edge.protocol ?? 'internal'} · ${edge.flow ?? 'synchronous'} · ${edge.direction ?? 'one-way'}`;
}

function DiagramHeader({ document }: { document: ArchitectureDocument }) {
	return (
		<header className="architecture-diagram__header">
			<p className="architecture-diagram__eyebrow">{document.scope} architecture</p>
			<h3 id={`architecture-title-${document.id}`}>{document.title}</h3>
			<p>{document.summary}</p>
		</header>
	);
}

function DiagramLegend({ document }: { document: ArchitectureDocument }) {
	const nodeTypes = Array.from(new Set(document.nodes.map((node) => node.type)));
	const edgeKinds = Array.from(new Set(document.edges.map(edgeKind)));

	return (
		<aside className="architecture-legend" aria-label="Diagram legend">
			<div className="architecture-legend__group">
				<span className="architecture-legend__heading">Components</span>
				<ul>
					{nodeTypes.map((type) => (
						<li key={type}>
							<span className={`architecture-legend__node architecture-legend__node--${type}`} aria-hidden="true" />
							{NODE_TYPE_LABELS[type]}
						</li>
					))}
				</ul>
			</div>
			<div className="architecture-legend__group">
				<span className="architecture-legend__heading">Connections</span>
				<ul>
					{edgeKinds.map((kind) => (
						<li key={kind}>
							<span className={`architecture-legend__line architecture-legend__line--${kind}`} aria-hidden="true" />
							{EDGE_KIND_LABELS[kind]}
						</li>
					))}
				</ul>
			</div>
		</aside>
	);
}

function EdgeSummary({ document }: { document: ArchitectureDocument }) {
	return (
		<ul className="architecture-static__edge-summary">
			{document.edges.map((edge) => (
				<li key={edge.id}>
					{edge.label ?? 'Connection'}
					{edge.request ? ` (${edge.request.type})` : ''}: {edgeDetails(edge)}
				</li>
			))}
		</ul>
	);
}

interface ArchitectureRendererProps {
	document: ArchitectureDocument;
	selectedEdgeId: string | null;
	onSelectEdge: (edgeId: string | null) => void;
	selectedNodeId: string | null;
	onSelectNode: (nodeId: string | null) => void;
}

function StaticArchitectureDiagram({
	document,
	selectedEdgeId,
	onSelectEdge,
	selectedNodeId,
	onSelectNode,
}: ArchitectureRendererProps) {
	const rootRef = useRef<HTMLDivElement>(null);
	const [geometry, setGeometry] = useState<{
		bounds: NodeRect;
		nodes: Map<string, NodeRect>;
		isMobile: boolean;
	} | null>(null);
	const maxColumn = Math.max(...document.nodes.map((node) => node.position.column)) + 1;
	const nodesById = new Map(document.nodes.map((node) => [node.id, node]));
	const topFeedbackLaneCount = document.edges.filter((edge) => {
		const source = nodesById.get(edge.source);
		const target = nodesById.get(edge.target);
		return source && target
			&& source.position.row === target.position.row
			&& target.position.column <= source.position.column;
	}).length;

	useEffect(() => {
		const root = rootRef.current;
		if (!root) return;

		let frame = 0;
		const updateGeometry = () => {
			window.cancelAnimationFrame(frame);
			frame = window.requestAnimationFrame(() => {
				const rootBounds = root.getBoundingClientRect();
				const nodes = new Map<string, NodeRect>();
				root.querySelectorAll<HTMLElement>('[data-architecture-node]').forEach((element) => {
					const bounds = element.getBoundingClientRect();
					nodes.set(element.dataset.architectureNode!, {
						left: bounds.left - rootBounds.left,
						top: bounds.top - rootBounds.top,
						width: bounds.width,
						height: bounds.height,
					});
				});
				setGeometry({
					bounds: { left: 0, top: 0, width: rootBounds.width, height: rootBounds.height },
					nodes,
					isMobile: window.matchMedia('(max-width: 52rem)').matches,
				});
			});
		};

		const observer = new ResizeObserver(updateGeometry);
		observer.observe(root);
		window.addEventListener('resize', updateGeometry);
		updateGeometry();

		return () => {
			window.cancelAnimationFrame(frame);
			observer.disconnect();
			window.removeEventListener('resize', updateGeometry);
		};
	}, [document.id]);

	const edges = useMemo(
		() => geometry ? buildStaticEdges(document, geometry.nodes, geometry.bounds, geometry.isMobile) : [],
		[document, geometry],
	);
	const orderedNodes = [...document.nodes].sort(
		(left, right) => left.mobileOrder - right.mobileOrder,
	);
	const boundaryStarts = new Map<
		number,
		NonNullable<ArchitectureDocument['boundaries']>
	>();
	(document.boundaries ?? []).forEach((boundary) => {
		const firstIndex = orderedNodes.findIndex((node) => boundary.nodeIds.includes(node.id));
		if (firstIndex < 0) return;
		const entries = boundaryStarts.get(firstIndex) ?? [];
		entries.push(boundary);
		entries.sort((left, right) => (
			left.parentId === right.id ? 1 : right.parentId === left.id ? -1 : 0
		));
		boundaryStarts.set(firstIndex, entries);
	});

	return (
		<div
			className="architecture-static"
			ref={rootRef}
			style={{
				'--architecture-columns': maxColumn,
				'--architecture-feedback-space': `${0.85 + topFeedbackLaneCount * 1.65}rem`,
			} as CSSProperties}
		>
			<svg
				className="architecture-static__edges"
				aria-label={`${document.title} connections`}
				viewBox={geometry ? `0 0 ${geometry.bounds.width} ${geometry.bounds.height}` : undefined}
				preserveAspectRatio="none"
			>
				<defs>
					{(['synchronous', 'asynchronous', 'data'] as const).map((kind) => (
						<marker key={kind} id={`${document.id}-${kind}-arrow`} viewBox="0 0 8 8" refX="6.6" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
							<path className={`architecture-static__arrow architecture-static__arrow--${kind}`} d="M 0 0 L 8 4 L 0 8 z" />
						</marker>
					))}
				</defs>
				{edges.map((edge) => {
					const marker = `url(#${document.id}-${edge.kind}-arrow)`;
					const motionClass = `architecture-edge__motion architecture-edge__motion--${edge.kind}`;
					return (
						<g
							key={edge.id}
							className={`architecture-static__edge architecture-static__edge--${edge.kind}${selectedEdgeId === edge.id ? ' architecture-static__edge--selected' : ''}`}
							role="button"
							tabIndex={0}
							aria-label={`Show connection details: ${edge.edge.label ?? 'Connection'}`}
							onClick={() => onSelectEdge(edge.id)}
							onKeyDown={(event) => {
								if (event.key !== 'Enter' && event.key !== ' ') return;
								event.preventDefault();
								onSelectEdge(edge.id);
							}}
						>
							<path d={edge.path} markerEnd={marker} />
							<path d={edge.path} className={`${motionClass} architecture-edge__motion--forward`} />
							<path d={edge.path} className="architecture-static__edge-hitbox" />
						</g>
					);
				})}
			</svg>

			<div className="architecture-static__grid">
				{orderedNodes.map((node, mobileIndex) => (
					<Fragment key={node.id}>
						{boundaryStarts.get(mobileIndex)?.map((boundary, boundaryIndex) => (
							<div
								key={boundary.id}
								className={`architecture-static__boundary architecture-static__boundary--${boundary.kind}`}
								style={{ '--architecture-mobile-order': mobileIndex * 3 + boundaryIndex + 1 } as CSSProperties}
								aria-label={`${boundary.kind} boundary: ${boundary.label}`}
							>
								<span>{boundary.kind} boundary</span>
								<strong>{boundary.label}</strong>
							</div>
						))}
					<article
						className={`architecture-static__card ${diagramBoxClassName(node)}`}
						data-architecture-node={node.id}
						style={{
							'--architecture-column': node.position.column + 1,
							'--architecture-row': node.position.row + 1,
							'--architecture-mobile-order': mobileIndex * 3 + 3,
						} as CSSProperties}
						aria-label={`${NODE_TYPE_LABELS[node.type]}: ${node.label}`}
						role="button"
						tabIndex={0}
						aria-haspopup="dialog"
						aria-pressed={selectedNodeId === node.id}
						onClick={() => onSelectNode(node.id)}
						onKeyDown={(event) => {
							if (event.key !== 'Enter' && event.key !== ' ') return;
							event.preventDefault();
							onSelectNode(node.id);
						}}
					>
						<div className="architecture-node__body"><DiagramBoxContents node={node} showLinkStatus={false} /></div>
						{node.href && !node.foundation ? (
							<a
								className="architecture-node__open-link"
								href={node.href}
								aria-label={`Open architecture details for ${node.label}`}
								onClick={(event) => event.stopPropagation()}
							>
								<span aria-hidden="true">↗</span>
							</a>
						) : null}
					</article>
					</Fragment>
				))}
			</div>
		</div>
	);
}

function positionsFromNodes(nodes: ArchitectureComponentFlowNode[]): ArchitectureLayoutPositions {
	return Object.fromEntries(
		nodes.map((node) => [
			node.id,
			{ x: node.position.x, y: node.position.y },
		]),
	);
}

function InteractiveArchitectureDiagram({
	document,
	selectedEdgeId,
	onSelectEdge,
	selectedNodeId,
	onSelectNode,
}: ArchitectureRendererProps) {
	const authoredPositions = useMemo(
		() => buildInitialNodePositions(document),
		[document],
	);
	const initialNodes = useMemo(
		() => createArchitectureFlowNodes(document, authoredPositions, onSelectNode),
		[authoredPositions, document, onSelectNode],
	);
	const [nodes, setNodes, onNodesChange] = useNodesState<ArchitectureComponentFlowNode>(initialNodes);
	const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
	const flowInstance = useRef<ReactFlowInstance<ArchitectureFlowNode, ArchitectureFlowEdge> | null>(null);
	const storageKey = getArchitectureLayoutStorageKey(document.id);

	const fitCurrentLayout = useCallback((duration = 240) => {
		window.requestAnimationFrame(() => {
			window.requestAnimationFrame(() => {
				void flowInstance.current?.fitView({
					...FIT_VIEW_OPTIONS,
					duration,
				});
			});
		});
	}, []);

	useEffect(() => {
		let restored: ArchitectureLayoutPositions | null = null;
		try {
			restored = restoreArchitectureLayout(
				document,
				window.localStorage.getItem(storageKey),
			);
		} catch {
			// Storage may be unavailable in privacy-restricted browsers.
		}

		setNodes(createArchitectureFlowNodes(document, restored ?? authoredPositions, onSelectNode));
		setFocusedNodeId(null);
		onSelectEdge(null);
		fitCurrentLayout(0);
	}, [authoredPositions, document, fitCurrentLayout, onSelectEdge, onSelectNode, setNodes, storageKey]);

	useEffect(() => {
		const clearFocusOnEscape = (event: KeyboardEvent) => {
			if (event.key === 'Escape') {
				setFocusedNodeId(null);
			}
		};
		window.addEventListener('keydown', clearFocusOnEscape);
		return () => window.removeEventListener('keydown', clearFocusOnEscape);
	}, []);

	const flowEdges = useMemo(
		() => createArchitectureFlowEdges(document, nodes, focusedNodeId, selectedEdgeId),
		[document, focusedNodeId, nodes, selectedEdgeId],
	);
	const relatedNodeIds = useMemo(() => {
		if (!focusedNodeId) return null;
		const related = new Set([focusedNodeId]);
		document.edges.forEach((edge) => {
			if (edge.source === focusedNodeId || edge.target === focusedNodeId) {
				related.add(edge.source);
				related.add(edge.target);
			}
		});
		return related;
	}, [document.edges, focusedNodeId]);

	const displayNodes = useMemo(
		() => nodes.map((node) => {
			const focusClass = !focusedNodeId
				? ''
				: node.id === focusedNodeId
					? 'architecture-flow-node--focused'
					: relatedNodeIds?.has(node.id)
						? 'architecture-flow-node--related'
						: 'architecture-flow-node--dimmed';
			return {
				...node,
				className: [node.className, focusClass].filter(Boolean).join(' '),
			};
		}),
		[focusedNodeId, nodes, relatedNodeIds],
	);
	const boundaryNodes = useMemo(
		() => createArchitectureBoundaryNodes(document, displayNodes),
		[displayNodes, document],
	);
	const allDisplayNodes: ArchitectureFlowNode[] = useMemo(
		() => [...boundaryNodes, ...displayNodes],
		[boundaryNodes, displayNodes],
	);

	const persistLayout = useCallback((draggedNode: ArchitectureFlowNode) => {
		const nextNodes = nodes.map((node) => (
			node.id === draggedNode.id
				? { ...node, position: draggedNode.position }
				: node
		));
		try {
			window.localStorage.setItem(
				storageKey,
				serializeArchitectureLayout(document, positionsFromNodes(nextNodes)),
			);
		} catch {
			// Dragging must continue even when local persistence is unavailable.
		}
	}, [document, nodes, storageKey]);

	const resetLayout = useCallback(() => {
		try {
			window.localStorage.removeItem(storageKey);
		} catch {
			// Reset still restores in-memory authored coordinates without storage.
		}
		setFocusedNodeId(null);
		onSelectEdge(null);
		setNodes(createArchitectureFlowNodes(document, authoredPositions, onSelectNode));
		fitCurrentLayout();
	}, [authoredPositions, document, fitCurrentLayout, onSelectEdge, onSelectNode, setNodes, storageKey]);

	return (
		<>
			<div
				className="architecture-interactive"
				aria-label={`${document.title} interactive architecture diagram`}
				aria-describedby={`architecture-instructions-${document.id}`}
			>
					<ReactFlow<ArchitectureFlowNode, ArchitectureFlowEdge>
						nodes={allDisplayNodes}
						edges={flowEdges}
						nodeTypes={NODE_TYPES}
						edgeTypes={EDGE_TYPES}
						onNodesChange={(changes) => {
							const componentChanges = changes.filter(
								(change) => !document.boundaries?.some(
									(boundary) => boundary.id === change.id,
								),
							) as NodeChange<ArchitectureComponentFlowNode>[];
							onNodesChange(componentChanges);
						}}
						onNodeDragStop={(_event, node) => {
							if (node.type === 'architectureNode') persistLayout(node);
						}}
						onNodeClick={(_event, node) => {
							if (node.type !== 'architectureNode') return;
							setFocusedNodeId((current) => current === node.id ? null : node.id);
							onSelectNode(node.id);
						}}
						onEdgeClick={(_event, edge) => onSelectEdge(edge.id)}
						onPaneClick={() => {
							setFocusedNodeId(null);
							onSelectEdge(null);
						}}
						onInit={(instance) => {
							flowInstance.current = instance;
							fitCurrentLayout(0);
						}}
						fitView
						fitViewOptions={FIT_VIEW_OPTIONS}
						minZoom={0.24}
						maxZoom={1.6}
						nodesDraggable
						nodesConnectable={false}
						elementsSelectable={false}
						edgesReconnectable={false}
						deleteKeyCode={null}
						selectionKeyCode={null}
						multiSelectionKeyCode={null}
						panOnDrag
						panOnScroll={false}
						zoomOnScroll={false}
						zoomOnPinch
						zoomOnDoubleClick={false}
						preventScrolling={false}
						defaultMarkerColor={null}
					>
						<Background
							id={`${document.id}-dots`}
							variant={BackgroundVariant.Dots}
							gap={20}
							size={0.85}
							color="var(--architecture-grid)"
						/>
						<Panel position="top-right" className="architecture-interactive__toolbar">
							<button
								type="button"
								className="architecture-interactive__reset nodrag nopan"
								onClick={resetLayout}
							>
								Reset layout
							</button>
						</Panel>
						<Controls
							position="bottom-right"
							showInteractive={false}
							fitViewOptions={FIT_VIEW_OPTIONS}
							aria-label="Architecture diagram zoom and fit controls"
						/>
					</ReactFlow>
			</div>
			<p
				id={`architecture-instructions-${document.id}`}
				className="architecture-diagram__hint"
			>
				Drag cards to isolate relationships. Click a connection for details, or click a card to focus its neighbors. Use Reset layout to restore the authored arrangement.
			</p>
			<div className="architecture-visually-hidden" aria-live="polite">
				{selectedNodeId
					? `${document.nodes.find((node) => node.id === selectedNodeId)?.label ?? selectedNodeId} details opened.`
					: focusedNodeId
					? `${document.nodes.find((node) => node.id === focusedNodeId)?.label ?? focusedNodeId} focused.`
					: 'No architecture component focused.'}
			</div>
		</>
	);
}

function useMobileArchitectureRenderer() {
	const [isMobile, setIsMobile] = useState(false);

	useEffect(() => {
		const query = window.matchMedia('(max-width: 52rem)');
		const update = () => setIsMobile(query.matches);
		update();
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	}, []);

	return isMobile;
}

function ResponsiveArchitectureDiagram({
	document,
	showHeader,
}: {
	document: ArchitectureDocument;
	showHeader: boolean;
}) {
	const isMobile = useMobileArchitectureRenderer();
	const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
	const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
	const inspectorTriggerRef = useRef<HTMLElement | null>(null);
	const selectedEdge: ArchitectureEdgeContract | null =
		document.edges.find((edge) => edge.id === selectedEdgeId) ?? null;
	const selectedNode =
		document.nodes.find((node) => node.id === selectedNodeId) ?? null;
	const inspectorOpen = selectedEdge !== null || selectedNode !== null;

	const rememberInspectorTrigger = useCallback(() => {
		const active = window.document.activeElement;
		inspectorTriggerRef.current = active instanceof HTMLElement ? active : null;
	}, []);

	const selectEdge = useCallback((edgeId: string | null) => {
		if (edgeId) rememberInspectorTrigger();
		setSelectedNodeId(null);
		setSelectedEdgeId(edgeId);
	}, [rememberInspectorTrigger]);

	const selectNode = useCallback((nodeId: string | null) => {
		if (nodeId) rememberInspectorTrigger();
		setSelectedEdgeId(null);
		setSelectedNodeId(nodeId);
	}, [rememberInspectorTrigger]);

	const closeInspector = useCallback(() => {
		setSelectedEdgeId(null);
		setSelectedNodeId(null);
		window.requestAnimationFrame(() => inspectorTriggerRef.current?.focus());
	}, []);

	useEffect(() => {
		const closeInspectorOnEscape = (event: KeyboardEvent) => {
			if (event.key === 'Escape' && inspectorOpen) closeInspector();
		};
		window.addEventListener('keydown', closeInspectorOnEscape);
		return () => window.removeEventListener('keydown', closeInspectorOnEscape);
	}, [closeInspector, inspectorOpen]);

	return (
		<section
			className={`architecture-diagram${inspectorOpen ? ' architecture-diagram--inspecting' : ''}`}
			aria-labelledby={showHeader ? `architecture-title-${document.id}` : undefined}
			aria-label={showHeader ? undefined : `${document.title} architecture diagram`}
		>
			{showHeader ? <DiagramHeader document={document} /> : null}
			<div className="architecture-diagram__workspace">
				<div className="architecture-diagram__stage">
					{isMobile ? (
						<StaticArchitectureDiagram
							document={document}
							selectedEdgeId={selectedEdgeId}
							onSelectEdge={selectEdge}
							selectedNodeId={selectedNodeId}
							onSelectNode={selectNode}
						/>
					) : (
						<InteractiveArchitectureDiagram
							document={document}
							selectedEdgeId={selectedEdgeId}
							onSelectEdge={selectEdge}
							selectedNodeId={selectedNodeId}
							onSelectNode={selectNode}
						/>
					)}
					<EdgeSummary document={document} />
					<DiagramLegend document={document} />
				</div>
				<ConnectionDetailsPanel
					edge={selectedEdge}
					sourceLabel={selectedEdge ? document.nodes.find((node) => node.id === selectedEdge.source)?.label : undefined}
					targetLabel={selectedEdge ? document.nodes.find((node) => node.id === selectedEdge.target)?.label : undefined}
					onClose={closeInspector}
				/>
				<NodeDetailsPanel node={selectedNode} onClose={closeInspector} />
			</div>
		</section>
	);
}

export default function ArchitectureDiagram({
	document,
	showHeader = true,
}: ArchitectureDiagramProps) {
	const validation = ArchitectureDocumentSchema.safeParse(document);
	if (!validation.success) return <ValidationError issues={validation.error.issues} />;
	return <ResponsiveArchitectureDiagram document={validation.data} showHeader={showHeader} />;
}
