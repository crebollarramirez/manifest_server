# Manifest Client

Frontend preview layer for the Manifest CAD backend: a fixture-driven React +
three.js viewer, wrapped in the full "Lavender Mist" interface shell (top bar,
chat, print settings, orbit gizmo, dimensions) — driven by the `cad-agent`
Edge Function contract.

- Contract and confirmed deviations: [CONTRACT.md](CONTRACT.md)
- Design system and confirmed deviations: [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md)
- Architecture: modular monolith — feature folders, one transport interface
  (`CadAgentClient`), Zod schemas as the only source of backend-derived types.

## Commands

```bash
npm install
npm run fixtures   # generate geometry fixtures (python3, no deps required)
npm run dev        # Vite dev server
npm test           # Vitest (data layer + decode + gpu logic)
npm run lint       # ESLint (security bans enforced)
npm run build      # tsc --noEmit + vite build
```

Fixtures are generated into `public/fixtures/` (gitignored, ~25 MB — includes
the 503k-triangle performance STL) and mirror the verified export layout
`<project_id>/exports/<part_id>/model.*`. Run `npm run fixtures` after a fresh
clone. With `cadquery` installed the bracket part is a real export of
`../3dModel.py`; otherwise a procedural fallback is used.

## Security notes

- **CSP:** strict policy in `index.html` — no inline/eval script,
  `connect-src` limited to self + Supabase origins, `worker-src 'self' blob:`
  for the geometry decode worker. Verified working with dev HMR (2026-07-28).
- **Lint-enforced bans:** `dangerouslySetInnerHTML`, `eval`, implied eval.
  All backend/AI-originated strings render through React's default escaping.
- **Signed URLs** are held in memory only — never logged or persisted. Only
  inert identifiers (part IDs, job IDs) may touch localStorage.
- **npm audit disposition:** historically flagged findings (brace-expansion,
  js-yaml, nanoid) have all been dev-dependency-chain only (ESLint config
  parsing, Vite's PostCSS pipeline) — no production exposure, nothing ships
  to the bundle. Cleared via plain `npm audit fix` (2026-08-10) once upstream
  patch releases became available, with no forced major-version bumps. If a
  future finding requires `--force` to clear (e.g. downgrading
  `eslint-plugin-react` off React 19), don't — leave it and re-check after
  the next `npm install`.
