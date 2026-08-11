# Manifest Design Tokens — Codebase Handoff

A self-contained token package for the **Manifest** design system ("Lavender Mist" direction): pastel purple + mint + peach on warm neutrals, value-driven and **dual-theme** (light + dark), with a signature translucent **glass** surface language.

These files are the source of truth for **color, type, spacing, radius, elevation, motion, and the glass primitives**. Drop them into the codebase and build components against the tokens — do not hardcode hex values.

## What's in this folder

| File | Purpose |
|------|---------|
| `manifest-tokens.css` | **Primary deliverable.** All tokens as CSS custom properties, both themes, plus the `.glass` / `.glass--gloss` / `.canvas-field` primitives and base reset. Import once globally. |
| `manifest-tokens.json` | Machine-readable tokens (resolved literals per theme) for design tooling, Style Dictionary, or generating platform-native constants. |
| `manifest.tailwind.preset.js` | Tailwind preset mapping the CSS vars to theme utilities (`bg-card`, `text-primary`, `rounded-lg`, `shadow-md`, `blur-lg`, …). |
| `reference-screens/` | The two full reference screens (light + dark) as readable HTML — the design to replicate. See note below. |

## Reference screens — the design to replicate

`reference-screens/Manifest Interface Bubble Light.dc.html` and `…Dark.dc.html` are the full app screens these tokens were tuned against: a 3D-print workspace with a glossy-glass **chat** (bottom-left, mid/full stages), **print-settings** panel (bottom-right, pill/full), always-on **dimensions** chip, centered **undo/redo/version/zoom** toolbar, an interactive **axis cube**, a collapsible **plate selector**, and a **canvas field** preview.

These files are the readable source of the layout and every component's markup + inline styles — **use them as the structural blueprint for the façade.** Two things to know when porting:

- Each is a **Design Component** (`.dc.html`): the markup lives in the `<x-dc>…</x-dc>` template (plain HTML with inline styles) and behavior in the `class Component extends DCLogic` block. Read both; translate the template to your framework's markup and the logic class to your component state. Ignore the `support.js`/`x-import`/`dc-*` runtime scaffolding — it's the prototype host, not part of the design.
- They reference the design-system bundle at `_ds/…` for `Button`, `Input`, `Select`, `Tabs`, `Switch`, `MessageBubble`, `IconButton`. Rebuild those from your component library against the tokens in `manifest-tokens.css` — the token names line up 1:1.

## How to use

### Plain CSS / any framework
```html
<link rel="stylesheet" href="manifest-tokens.css">
```
```css
.panel { background: var(--surface-card); color: var(--text-primary); border-radius: var(--radius-lg); box-shadow: var(--shadow-md); }
```

### Tailwind
```js
// tailwind.config.js
module.exports = { presets: [require('./manifest.tailwind.preset.js')], content: ['./src/**/*.{js,ts,jsx,tsx}'] };
```
Import `manifest-tokens.css` once (global entry) so the variables exist, then use utilities: `class="bg-card text-primary rounded-lg shadow-md"`.

## Theming

Everything routes through semantic tokens defined in **both** themes. Switch the whole system by setting one attribute on a root element:

```html
<html data-theme="light">   <!-- default -->
<html data-theme="dark">     <!-- dark -->
```

Light is also the `:root` default, so omitting the attribute yields light. The Tailwind preset's `darkMode` is wired to `[data-theme="dark"]` (not the `dark:` class).

**Purple is an accent, never the substrate.** Surfaces come from the neutral surface ramp (`--bg-page`, `--surface-card`, `--surface-card-tint`, `--surface-sunken`); purple appears only in `--color-primary*`, selection, and emphasis. Hierarchy is carried by the surface ramp + text ramp (`--text-primary` / `--text-secondary` / `--text-tertiary`), which invert per theme.

## Token groups

- **Base ramps** (theme-invariant hue anchors): `--purple-50…900`, `--mint-*`, `--peach-*`, `--coral-*`, `--gray-*`.
- **Surfaces**: `--bg-page` (gradient), `--bg-page-flat`, `--surface-card`, `--surface-card-tint`, `--surface-sunken`.
- **Canvas field** (the 3D preview backdrop): `--canvas-bg`, `--canvas-glow`, `--canvas-dot` — consume via the `.canvas-field` class.
- **Glass**: `--surface-glass` / `-strong` / `-subtle`, `--surface-glass-border` / `-border-soft`.
- **Glossy glass** (the "see-through workspace bubble" look): `--glass-fill`, `--glass-shine`, `--glass-gloss`, `--glass-shine-edge`, `--glass-gloss-border` — consume via `.glass--gloss`.
- **Text**: `--text-primary/secondary/tertiary`, `--text-on-primary`, `--text-disabled`, `--text-link` / `-hover`.
- **Actions & status**: `--color-primary*`, `--color-secondary*`, `--color-accent*`, `--color-{success,warning,error,info}` each with `-soft` and (status) `-text` variants.
- **Borders**: `--border-subtle/default/strong/focus` (low-opacity hairlines — never heavy or colored left-accent borders).
- **Type**: `--font-sans` (Nunito), `--font-mono` (JetBrains Mono); sizes `--text-xs…4xl` (base **17px**); weights `--weight-regular…display`; `--leading-*`; `--tracking-*`.
- **Spacing**: `--space-1…20` (4px base rhythm).
- **Radius**: `--radius-sm/md/lg/xl/pill` — smooth corners everywhere, **never below 12px** on real surfaces.
- **Elevation**: `--shadow-xs…lg` (soft, theme-tinted), `--shadow-focus`; blur `--blur-sm/md/lg`.
- **Motion**: `--ease-standard/out/bounce`, `--duration-fast/base/slow` (120–360ms, gentle; bounce reserved for rare confirms).

## The glass primitives (signature look)

```html
<div class="glass">…</div>          <!-- standard translucent panel -->
<div class="glass glass--strong">…</div>
<div class="glass--gloss">…</div>    <!-- see-through workspace bubble: strong blur + saturate, specular top edge, inset highlight -->
```

`.glass--gloss` is the treatment used across the app's floating UI (chat, print-settings, toolbars, plate menu). It intentionally reads far more transparent than `.glass`: a low-opacity fill (`--glass-fill`) over `backdrop-filter: blur(var(--blur-lg)) saturate(1.4)`, a top-edge gloss gradient (`--glass-shine` → `--glass-gloss` → transparent), a hairline (`--glass-gloss-border`), and an inset top highlight (`--glass-shine-edge`). Blur is for floating surfaces only — use a solid `--surface-card` for dense/long-form content.

## Substitutions to confirm with the team

- **Fonts** — Nunito + JetBrains Mono are Google Fonts stand-ins (loaded via `@import` in `manifest-tokens.css`). If Manifest has brand fonts, ship the `.woff2` files and switch to self-hosted `@font-face`.
- **Icons** — the prototypes used [Phosphor Icons](https://phosphoricons.com) (regular weight) as a placeholder. Not part of the tokens; swap for the real icon set.
- **Accessibility** — all color pairings target WCAG AA for body text in both themes. Re-verify after any palette change.

## Reference implementations

The working prototype these tokens were tuned against lives in the project root:
`Manifest Interface Bubble Light.dc.html` and `Manifest Interface Bubble Dark.dc.html` — open them to see the glass primitives, canvas field, and theming in context.
