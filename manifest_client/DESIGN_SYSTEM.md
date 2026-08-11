# Design System — integration notes

Source of record: [`design_handoff_manifest_tokens/`](../design_handoff_manifest_tokens/) (repo root) — the "Lavender Mist" token package, left untouched there. This document records how it was wired into `manifest_client`, every deviation from the handoff, and why — the same discipline as [CONTRACT.md](CONTRACT.md) for the backend contract.

## Layout

```
src/design-system/
  tokens.css       # adapted copy of manifest-tokens.css (see deviations below)
  fonts.css        # single source for font loading (Fontsource, self-hosted)
  tailwind.css     # Tailwind v4 @theme wiring over tokens.css
  ThemeProvider.tsx / useTheme()
  Button.tsx / IconButton.tsx / GlassPanel.tsx / Card.tsx / Badge.tsx
  Select.tsx / Switch.tsx / Slider.tsx / Tabs.tsx      (+ .module.css + .test.tsx each)
  index.ts         # the only import path — see "Scalability" below

src/shell/          # the main interface — replicates
                     # design_handoff_manifest_tokens/reference-screens/
  AppShell.tsx       # composition + shared state (focus, unit, camera, settings)
  TopBar.tsx / PlateSelector.tsx / DimensionsChip.tsx / CenterToolbar.tsx
  ChatPanel.tsx / AxisCube.tsx / SettingsPanel.tsx / RulerOverlay.tsx

src/viewer/
  PreviewLayer.tsx   # the R3F canvas — canvas-field backdrop, focused/all-parts mode
  cameraApi.ts       # imperative camera control shared between DOM chrome and OrbitControls
  useProjectData.ts  # single real data source (parts + geometry), shared by 3D + chrome
```

## Deviations from the handoff, and why

| # | Handoff said | This app does | Why |
|---|---|---|---|
| 1 | `@import` Nunito/JetBrains Mono from Google Fonts | Self-hosted via `@fontsource-variable/*` (`fonts.css`) | The CDN import is blocked outright by the existing strict CSP (`style-src`/`font-src` are `'self'`-only). Fontsource ships the font files as npm packages Vite bundles same-origin — zero CSP changes needed, and it's exactly what the handoff's own README suggested ("self-hosted `@font-face`"), just via a package instead of manually sourced `.woff2` files. |
| 2 | Dark theme only via `[data-theme="dark"]` | Also keys off `@media (prefers-color-scheme: dark)` by default, with `[data-theme]` overriding in both directions | Without this, a first-time visitor with a dark OS would see the light theme flash until `ThemeProvider` mounted and ran JS. Standard token-level pattern: OS preference is the default signal, explicit choice always wins. |
| 3 | Tailwind v3 JS preset (`manifest.tailwind.preset.js`) | Tailwind **v4** + a hand-written `@theme` CSS block re-expressing the same mappings | v4 replaced JS presets with CSS-first config as the primary mechanism; the old preset format is only supported via a migration compatibility path (`@config`), not idiomatic v4. Version chosen for compatibility with the current stack (Vite 6, native `@tailwindcss/vite` plugin) rather than with the preset's own authored syntax. |
| 4 | `bg-primary`/`text-primary` mean different things (v3 kept `colors`/`textColor` as separate namespaces) | Renamed — see table below | **Found via inspecting the actual built CSS, not assumed:** Tailwind v4's `@theme` compiles into `@layer theme`. Per the CSS Cascade Layers spec, an unlayered rule (tokens.css, plain CSS) always beats a layered one for the same custom property, regardless of source order. Any `@theme` key reusing a tokens.css name with a *different* intended meaning is silently inert — it generates a utility class that produces the wrong color with no error. This is now covered by an automated regression test (`tailwind-theme.test.ts`) so a future `@theme` addition can't quietly reintroduce it. |

### The renaming table (deviation 4)

| tokens.css / handoff meaning | Old (broken) idea | Utility actually generated |
|---|---|---|
| `--color-primary` (purple brand accent) | `bg-primary` | `bg-brand` / `bg-brand-hover` / `-active` / `-soft` |
| `--color-secondary` (mint accent) | `bg-secondary` | `bg-action-secondary` (`-soft`) |
| `--color-accent` (peach accent) | `bg-accent` | `bg-action-accent` (`-soft`) |
| `--color-success/warning/error/info` (+ `-soft`/`-text`) | `bg-success`, `text-success-text`, ... | `bg-status-success`, `text-status-success-text`, ... |
| `--text-primary/secondary/tertiary/on-primary/disabled/link` | `text-primary` | `text-ink`, `text-ink-secondary`, `text-ink-tertiary`, `text-ink-inverted`, `text-ink-disabled`, `text-ink-link` |

Everything else (ramps, surfaces, borders, type scale, radius, shadow, motion) kept its handoff-intended name — those don't collide with anything, or intentionally pass the identical value through (harmless, since the real value always resolves from tokens.css's single declaration either way).

**Plain CSS custom properties remain unaffected by any of this.** Component CSS Modules (`Button.module.css`, `Badge.module.css`, ...) reference `var(--color-primary)`, `var(--color-success-soft)`, etc. directly — the tokens.css names, not the Tailwind ones — so they were never at risk from the layering issue. Tailwind utility classes are an *additional* way to reach the same tokens, available for components built in later phases; nothing already built depends on them.

## Scalability — how a redesign or asset update stays a small diff

- **One value, one place.** Every raw hex/rgba lives in `tokens.css` only. `tailwind.css` and every component's CSS Module reference tokens via `var()` — never a literal color. Changing a palette value means editing `tokens.css` once; Tailwind utilities and every component pick it up automatically, in both themes.
- **One font-loading seam.** `fonts.css` is the only file that names a font source. Swapping Fontsource for self-hosted brand `.woff2` files later is a one-file edit; `tokens.css`'s `--font-sans`/`--font-mono` values don't change.
- **One import surface.** Consumers import from `design-system/index.ts`, never individual component files. The internal file layout (e.g. splitting `Button.tsx` into a folder, adding variants) can change without touching call sites elsewhere in the app.
- **Status colors, not hardcoded per-caller logic.** `statusToBadgeVariant()` centralizes the `JobStatus -> BadgeVariant` mapping (typed against the real Zod enum in `schemas.ts`, not a re-declared union) so Phase 4's job-status UI — and anything else that ever shows a status — never re-derives it.
- **The naming-collision class of bug is now a test, not a surprise.** `tailwind-theme.test.ts` asserts the renamed keys stay renamed and that no future `@theme` key silently collides with a tokens.css name of different intent — catches the exact bug found while building this at review time, not at runtime.

## Component inventory

Primitives: `Button`, `IconButton`, `GlassPanel` (default/strong/subtle/gloss — floating UI only, per the handoff: "blur is for floating surfaces only"), `Card` (solid surface, dense/long-form content), `Badge` (+ `statusToBadgeVariant`), `Select`, `Switch`, `Slider`, `Tabs`, `ThemeProvider`/`useTheme` (system/light/dark, persisted, OS-reactive).

Shell (the main interface, replicating `design_handoff_manifest_tokens/reference-screens/`): `TopBar`, `PlateSelector`, `DimensionsChip`, `CenterToolbar`, `ChatPanel`, `AxisCube`, `SettingsPanel`, `RulerOverlay`, composed in `AppShell`.

## The main interface — decisions made replicating the reference screens

The reference screens (`design_handoff_manifest_tokens/reference-screens/*.dc.html`) are a richer, fictional demo (a print-ordering flow, a "friendly dinosaur" model split into named part groups). Four decisions on how to ground that in what this app actually has:

- **No fabricated part grouping.** The mockup's "Body & legs" / "Head & spikes" plates don't correspond to anything in the data model — every `Part` today is independent, with no group or placement data. `PlateSelector` instead lists real parts one-for-one; "All parts" shows the full grid (`PlateGrid`), selecting one focuses just its plate. The mockup's single-"assembled" toggle is deliberately absent — it would need real multi-part placement data this app doesn't have yet (the open question from the earlier assembly-architecture discussion).
- **Print settings are visual, not fake.** Filament type, infill, layer height, and supports are local UI state only — there is no print-ordering concept anywhere in `cad-agent`. The **Order** button in `TopBar` renders permanently disabled with a "coming soon" title rather than implying it queues anything.
- **Two exceptions that are genuinely functional, not fake:** the Material tab's color swatches live-tint the focused part's actual mesh, and the Size tab's scale slider live-scales it — both flow through `AppShell`'s `scalePreview`/`colorPreview` state into `PreviewLayer` → `PartModel`. Real visual effect, explicitly a preview, no claim about an actual print material or size.
- **"Manny" (name, avatar, "your buddy" tagline) kept exactly** — the only piece of the mockup's content, rather than structure, carried over as-is. Chat *messages* themselves are real (`ChatPanel` calls the fixture client's `chat()`/`getEditJob()`/`getExportJob()` — sending a message really submits an edit and, once it completes, calls `useProjectData`'s `refreshPart` to pull in the new geometry), not the mockup's scripted dinosaur copy.

Also genuinely wired, not decorative: `CenterToolbar`'s zoom (`cameraApi.ts`, real `OrbitControls` distance), `AxisCube`'s click-to-snap and drag-to-orbit (same camera API — pure CSS 3D transforms for the cube itself, not WebGL), `TopBar`'s theme toggle (`ThemeProvider`) and Export button (`client.exportPart()`), `DimensionsChip`/`RulerOverlay`'s numbers (the focused part's real decoded bounds). `CenterToolbar`'s undo/redo/version are the one place restraint went the other way: rendered, but honestly disabled — there's no edit-history stack to back them, so they don't pretend to work.

## Open item for the design team

Per the handoff's own §6: does CAD generation assign authored color/appearance data to solids, or is output geometry-only? `GlassPanel`/`Badge`/`Button` don't depend on the answer, but `PartModel`'s material system (`viewer/materials/useMaterial.ts`) will, if authored CAD color is ever added.
