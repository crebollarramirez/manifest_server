# Manifest Client

Frontend preview layer for the Manifest CAD backend: a fixture-driven React +
three.js viewer that renders each part in a project on its own plate, driven by
the `cad-agent` Edge Function contract.

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
- **npm audit disposition (2026-07-28):** all 6 findings are a single
  brace-expansion DoS advisory confined to the ESLint dev-dependency chain.
  No production exposure (dev-only tooling; nothing ships to the bundle).
  **Do not run `npm audit fix --force`** — it downgrades `eslint-plugin-react`
  to a pre-React-19 version. Disposition: accept, clear via normal upstream
  updates.
