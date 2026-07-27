import { useEffect, useRef, type ReactNode } from 'react';

import type {
	ArchitectureContractItem,
	ArchitectureNode,
} from './diagram-schema';
import {
	getVisibleNodeInspectorSections,
	hasOperationContent,
	nodeResponsibility,
	type NodeInspectorSectionId,
} from './node-details';

const NODE_TYPE_LABELS: Record<ArchitectureNode['type'], string> = {
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

function TextList({ items }: { items?: string[] }) {
	if (!items?.length) return null;
	return <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>;
}

function LabeledList({ label, items }: { label: string; items?: string[] }) {
	if (!items?.length) return null;
	return (
		<div className="architecture-inspector__field">
			<h6>{label}</h6>
			<TextList items={items} />
		</div>
	);
}

function ContractList({
	label,
	items,
}: {
	label: string;
	items?: ArchitectureContractItem[];
}) {
	if (!items?.length) return null;
	return (
		<div className="architecture-inspector__field">
			<h6>{label}</h6>
			<dl className="architecture-inspector__contracts">
				{items.map((item) => (
					<div key={item.name}>
						<dt>{item.name}</dt>
						<dd>{item.description}</dd>
					</div>
				))}
			</dl>
		</div>
	);
}

function Section({
	title,
	defaultOpen,
	children,
}: {
	title: string;
	defaultOpen: boolean;
	children: ReactNode;
}) {
	return (
		<details className="architecture-inspector__section" open={defaultOpen}>
			<summary>{title}</summary>
			<div>{children}</div>
		</details>
	);
}

function SectionContents({
	id,
	node,
}: {
	id: NodeInspectorSectionId;
	node: ArchitectureNode;
}) {
	const details = node.details!;
	switch (id) {
		case 'when':
			return (
				<>
					<LabeledList label="Trigger" items={details.trigger} />
					<LabeledList label="Preconditions" items={details.preconditions} />
				</>
			);
		case 'contract':
			return (
				<>
					<ContractList label="Inputs" items={details.inputs} />
					<ContractList label="Outputs" items={details.outputs} />
					<LabeledList label="Durable reads" items={details.durableReads} />
					<LabeledList label="Durable writes" items={details.durableWrites} />
					<LabeledList label="Downstream" items={details.downstream} />
				</>
			);
		case 'logic':
			return (
				<>
					{details.steps?.length ? (
						<div className="architecture-inspector__field">
							<h6>Ordered steps</h6>
							<ol>{details.steps.map((step) => <li key={step}>{step}</li>)}</ol>
						</div>
					) : null}
					<LabeledList label="Decision points" items={details.decisionPoints} />
					{details.shortCircuit ? (
						<div className="architecture-inspector__field">
							<h6>Short circuit</h6>
							<p>{details.shortCircuit}</p>
						</div>
					) : null}
				</>
			);
		case 'outcomes':
			return (
				<>
					{details.successCondition ? (
						<div className="architecture-inspector__field">
							<h6>Success</h6>
							<p>{details.successCondition}</p>
						</div>
					) : null}
					{details.failureConditions?.length ? (
						<div className="architecture-inspector__field">
							<h6>Failure conditions</h6>
							<ul className="architecture-inspector__failures">
								{details.failureConditions.map((failure) => (
									<li key={`${failure.condition}-${failure.outcome}`}>
										<strong>{failure.condition}</strong>
										<span>{failure.outcome}</span>
										{failure.continuesTo ? <small>Continues to: {failure.continuesTo}</small> : null}
									</li>
								))}
							</ul>
						</div>
					) : null}
				</>
			);
		case 'guarantees':
			return <TextList items={details.guarantees} />;
		case 'operations': {
			const operations = details.operations!;
			if (!hasOperationContent(operations)) return null;
			return (
				<>
					<dl className="architecture-inspector__metadata">
						{operations.timeout ? <div><dt>Timeout</dt><dd>{operations.timeout}</dd></div> : null}
						{operations.concurrency ? <div><dt>Concurrency</dt><dd>{operations.concurrency}</dd></div> : null}
						{operations.retry ? <div><dt>Retry</dt><dd>{operations.retry}</dd></div> : null}
					</dl>
					<LabeledList label="Observability" items={operations.observability} />
					<LabeledList label="Controls" items={operations.controls} />
				</>
			);
		}
		case 'source':
			return (
				<ul className="architecture-inspector__evidence">
					{details.evidence?.map((evidence) => (
						<li key={`${evidence.path}-${evidence.symbol ?? ''}`}>
							<span>{evidence.kind}</span>
							<code>{evidence.path}</code>
							{evidence.symbol ? <strong>{evidence.symbol}</strong> : null}
						</li>
					))}
				</ul>
			);
	}
}

interface NodeDetailsPanelProps {
	node: ArchitectureNode | null;
	onClose: () => void;
}

export default function NodeDetailsPanel({
	node,
	onClose,
}: NodeDetailsPanelProps) {
	const closeButtonRef = useRef<HTMLButtonElement>(null);

	useEffect(() => {
		if (node) closeButtonRef.current?.focus();
	}, [node?.id]);

	if (!node) return null;
	const sections = getVisibleNodeInspectorSections(node);

	return (
		<aside
			className="architecture-connection-panel architecture-node-panel nodrag nopan"
			aria-labelledby={`architecture-node-panel-${node.id}`}
			role="dialog"
			aria-modal="false"
		>
			<header className="architecture-connection-panel__header">
				<div>
					<span>{node.variant ?? NODE_TYPE_LABELS[node.type]}</span>
					<h4 id={`architecture-node-panel-${node.id}`}>{node.label}</h4>
				</div>
				<button
					ref={closeButtonRef}
					type="button"
					className="architecture-connection-panel__close"
					onClick={onClose}
					aria-label="Close component details"
				>
					<span aria-hidden="true">×</span>
				</button>
			</header>
			<p className="architecture-node-panel__responsibility">{nodeResponsibility(node)}</p>
			{node.details?.ownedBy || node.details?.runsIn ? (
				<dl className="architecture-inspector__metadata">
					{node.details.ownedBy ? <div><dt>Owned by</dt><dd>{node.details.ownedBy}</dd></div> : null}
					{node.details.runsIn ? <div><dt>Runs in</dt><dd>{node.details.runsIn}</dd></div> : null}
				</dl>
			) : null}
			{sections.map((section) => (
				<Section key={section.id} title={section.title} defaultOpen={section.defaultOpen}>
					<SectionContents id={section.id} node={node} />
				</Section>
			))}
		</aside>
	);
}
