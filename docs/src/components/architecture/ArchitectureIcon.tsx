import type { ArchitectureIconName } from './diagram-schema';

type ArchitectureIconProps = {
	name: ArchitectureIconName;
};

/**
 * Shared, dependency-free architecture symbols. The icons use conventional
 * outline forms so every diagram has the same visual vocabulary.
 */
export function ArchitectureIcon({ name }: ArchitectureIconProps) {
	return (
		<svg
			className="architecture-box__icon"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.7"
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			{name === 'layers' ? (
				<>
					<path d="m12 3 9 5-9 5-9-5 9-5Z" />
					<path d="m3 12 9 5 9-5" />
					<path d="m3 16 9 5 9-5" />
				</>
			) : name === 'terminal' ? (
				<>
					<rect x="2.5" y="4" width="19" height="16" rx="2" />
					<path d="m6.5 9 3 3-3 3" />
					<path d="M12 15h5" />
				</>
			) : name === 'api' ? (
				<>
					<path d="m8 6-5 6 5 6" />
					<path d="m16 6 5 6-5 6" />
					<path d="m14 4-4 16" />
				</>
			) : name === 'process' ? (
				<>
					<circle cx="12" cy="12" r="3" />
					<path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1" />
				</>
			) : name === 'service' ? (
				<>
					<rect x="3" y="4" width="18" height="6" rx="1.5" />
					<rect x="3" y="14" width="18" height="6" rx="1.5" />
					<circle cx="7" cy="7" r=".8" fill="currentColor" stroke="none" />
					<circle cx="7" cy="17" r=".8" fill="currentColor" stroke="none" />
					<path d="M11 7h6M11 17h6" />
				</>
			) : name === 'worker' ? (
				<>
					<rect x="3" y="4" width="18" height="16" rx="2" />
					<circle cx="12" cy="12" r="3" />
					<path d="M12 6.5v2M12 15.5v2M6.5 12h2M15.5 12h2M8.1 8.1l1.4 1.4M14.5 14.5l1.4 1.4M15.9 8.1l-1.4 1.4M9.5 14.5l-1.4 1.4" />
				</>
			) : name === 'database' ? (
				<>
					<ellipse cx="12" cy="5" rx="8" ry="3" />
					<path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
					<path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" />
				</>
			) : name === 'queue' ? (
				<>
					<path d="M4 6h11M4 12h11M4 18h11" />
					<path d="m15 3 4 3-4 3M15 9l4 3-4 3M15 15l4 3-4 3" />
				</>
			) : name === 'object-storage' ? (
				<>
					<path d="M4 6.5 12 3l8 3.5-8 3.5-8-3.5Z" />
					<path d="m4 11 8 3.5 8-3.5" />
					<path d="m4 15.5 8 3.5 8-3.5" />
					<path d="M4 6.5v9M20 6.5v9" />
				</>
			) : name === 'file-json' ? (
				<>
					<path d="M6 2.5h8l4 4V21.5H6z" />
					<path d="M14 2.5v4h4" />
					<path d="M10 11c-1 0-1.5.6-1.5 1.5S8 14 7 14c1 0 1.5.6 1.5 1.5S9 17 10 17M14 11c1 0 1.5.6 1.5 1.5S16 14 17 14c-1 0-1.5.6-1.5 1.5S15 17 14 17" />
				</>
			) : name === 'source-code' ? (
				<>
					<path d="M6 2.5h8l4 4V21.5H6z" />
					<path d="M14 2.5v4h4" />
					<path d="m11 11-2 2 2 2M14 11l2 2-2 2" />
				</>
			) : name === 'ast' ? (
				<>
					<circle cx="12" cy="4.5" r="2" />
					<circle cx="6" cy="18.5" r="2" />
					<circle cx="12" cy="18.5" r="2" />
					<circle cx="18" cy="18.5" r="2" />
					<path d="M12 6.5v5M6 16.5v-3h12v3M12 11.5v5" />
				</>
			) : name === 'search' ? (
				<>
					<circle cx="10.5" cy="10.5" r="6.5" />
					<path d="m15.5 15.5 5 5" />
					<path d="m8.5 8-2 2.5 2 2.5M12.5 8l2 2.5-2 2.5" />
				</>
			) : name === 'editor' ? (
				<>
					<path d="m4 16-.8 4.8L8 20l10.8-10.8-4-4L4 16Z" />
					<path d="m12.8 7.2 4 4M4 16l4 4" />
					<path d="M4 4h6M4 8h4" />
				</>
			) : (
				<>
					<rect x="3" y="3" width="13" height="13" rx="2" strokeDasharray="3 2" />
					<path d="M13 11 21 3M16 3h5v5" />
				</>
			)}
		</svg>
	);
}
