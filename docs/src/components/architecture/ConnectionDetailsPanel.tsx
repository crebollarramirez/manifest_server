import { useEffect, useRef } from 'react';

import type { ArchitectureEdge } from './diagram-schema';
import { getConnectionDetails } from './connection-details';

interface ConnectionDetailsPanelProps {
	edge: ArchitectureEdge | null;
	onClose: () => void;
	sourceLabel?: string;
	targetLabel?: string;
}

export default function ConnectionDetailsPanel({
	edge,
	onClose,
	sourceLabel,
	targetLabel,
}: ConnectionDetailsPanelProps) {
	const closeButtonRef = useRef<HTMLButtonElement>(null);

	useEffect(() => {
		if (!edge) return;
		closeButtonRef.current?.focus();
	}, [edge?.id]);

	if (!edge) return null;
	const details = getConnectionDetails(edge);
	const requestDirection = `${sourceLabel ?? edge.source} → ${targetLabel ?? edge.target}`;
	const responseDirection = `${targetLabel ?? edge.target} → ${sourceLabel ?? edge.source}`;

	return (
		<aside
			className="architecture-connection-panel nodrag nopan"
			aria-label={`${details.title} connection details`}
			role="dialog"
			aria-modal="false"
		>
			<header className="architecture-connection-panel__header">
				<div>
					<span>Connection</span>
					<h4>{details.title}</h4>
				</div>
				<button
					ref={closeButtonRef}
					type="button"
					className="architecture-connection-panel__close"
					onClick={onClose}
					aria-label="Close connection details"
				>
					<span aria-hidden="true">×</span>
				</button>
			</header>

			{edge.details?.summary ? (
				<p className="architecture-connection-panel__summary">{edge.details.summary}</p>
			) : null}

			<dl className="architecture-inspector__metadata">
				<div>
					<dt>From</dt>
					<dd>{sourceLabel ?? edge.source}</dd>
				</div>
				<div>
					<dt>To</dt>
					<dd>{targetLabel ?? edge.target}</dd>
				</div>
			</dl>

			{edge.details?.preconditions?.length ? (
				<section className="architecture-inspector__field">
					<h5>Preconditions</h5>
					<ul>{edge.details.preconditions.map((item) => <li key={item}>{item}</li>)}</ul>
				</section>
			) : null}

			{edge.details?.operation || edge.details?.payload ? (
				<section className="architecture-inspector__field">
					<h5>Operation or payload</h5>
					{edge.details.operation ? <p><strong>Operation:</strong> <code>{edge.details.operation}</code></p> : null}
					{edge.details.payload ? <p>{edge.details.payload}</p> : null}
				</section>
			) : null}

			{edge.request ? (
				<section className="architecture-connection-panel__message">
					<h5>{edge.direction === 'two-way' ? 'Request' : 'Message'} · {requestDirection}</h5>
					<strong>{edge.request.label}</strong>
					<p>
						<span>Type</span>
						<code>{edge.request.type}</code>
					</p>
					<pre>{edge.request.exampleBody}</pre>
				</section>
			) : null}

			{edge.response ? (
				<section className="architecture-connection-panel__message">
					<h5>Response · {responseDirection}</h5>
					<strong>{edge.response.label}</strong>
					<p>
						<span>Type</span>
						<code>{edge.response.type}</code>
					</p>
					<pre>{edge.response.exampleBody}</pre>
				</section>
			) : null}

			{edge.details?.durability ? (
				<section className="architecture-inspector__field">
					<h5>Durability</h5>
					<p>{edge.details.durability}</p>
				</section>
			) : null}

			{edge.details?.failureBehavior ? (
				<section className="architecture-inspector__field">
					<h5>Failure or non-delivery</h5>
					<p>{edge.details.failureBehavior}</p>
				</section>
			) : null}

			{edge.details?.trustBoundary ? (
				<section className="architecture-inspector__field">
					<h5>Trust boundary</h5>
					<p>{edge.details.trustBoundary}</p>
				</section>
			) : null}

			{edge.details?.evidence?.length ? (
				<section className="architecture-inspector__field">
					<h5>Evidence</h5>
					<ul className="architecture-inspector__evidence">
						{edge.details.evidence.map((evidence) => (
							<li key={`${evidence.path}-${evidence.symbol ?? ''}`}>
								<span>{evidence.kind}</span>
								<code>{evidence.path}</code>
								{evidence.symbol ? <strong>{evidence.symbol}</strong> : null}
							</li>
						))}
					</ul>
				</section>
			) : null}

			<details className="architecture-inspector__section">
				<summary>Transport metadata</summary>
				<dl className="architecture-connection-panel__metadata">
					<div><dt>Protocol</dt><dd>{details.protocol}</dd></div>
					<div><dt>Flow</dt><dd>{details.flow}</dd></div>
					<div><dt>Direction</dt><dd>{details.direction}</dd></div>
				</dl>
			</details>

			{edge.href ? (
				<a className="architecture-connection-panel__reference" href={edge.href}>
					Open connection reference
					<span aria-hidden="true"> →</span>
				</a>
			) : null}
		</aside>
	);
}
