import { useId } from 'react';
import { BaseEdge, getSmoothStepPath, type EdgeProps } from '@xyflow/react';

import {
	getArchitectureEdgeRenderContract,
} from './edge-presentation';
import type { ArchitectureFlowEdge } from './layout-graph';

function routeOffset(data: NonNullable<ArchitectureFlowEdge['data']>): number {
	const slot = data.routeSlot;
	return 28 + Math.abs(slot) * 14;
}

/**
 * Renders a live orthogonal relationship from React Flow's current node
 * positions. The visible line remains continuous; React Flow supplies a wider,
 * transparent interaction path so authors never need to place edge controls.
 */
export function ArchitectureEdge({
	id,
	data,
	sourceX,
	sourceY,
	targetX,
	targetY,
	sourcePosition,
	targetPosition,
}: EdgeProps<ArchitectureFlowEdge>) {
	const generatedId = useId().replaceAll(':', '');
	if (!data) return null;

	const presentation = getArchitectureEdgeRenderContract(data.edge, data);
	const [path] = getSmoothStepPath({
		sourceX,
		sourceY,
		targetX,
		targetY,
		sourcePosition,
		targetPosition,
		borderRadius: 8,
		offset: routeOffset(data),
	});
	const markerId = `architecture-arrow-${generatedId}`;
	const markerUrl = `url(#${markerId})`;
	const selectedClass = data.selected ? ' architecture-edge--selected' : '';

	return (
		<>
			<defs aria-hidden="true">
				<marker
					id={markerId}
					viewBox="0 0 8 8"
					refX="6.5"
					refY="4"
					markerWidth="8"
					markerHeight="8"
					orient="auto-start-reverse"
				>
					<path className="architecture-edge__arrow" d="M 0 0 L 8 4 L 0 8 z" />
				</marker>
			</defs>
			<BaseEdge
				id={id}
				path={path}
				className={`${presentation.baseClass}${selectedClass}`}
				markerStart={presentation.hasSourceArrow ? markerUrl : undefined}
				markerEnd={markerUrl}
				interactionWidth={22}
			/>
			<path
				d={path}
				className={`${presentation.forwardMotionClass}${selectedClass}`}
				fill="none"
				pointerEvents="none"
				aria-hidden="true"
			/>
			{presentation.reverseMotionClass ? (
				<path
					d={path}
					className={`${presentation.reverseMotionClass ?? ''}${selectedClass}`}
					fill="none"
					pointerEvents="none"
					aria-hidden="true"
				/>
			) : null}
		</>
	);
}

export default ArchitectureEdge;
