import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Regression test for a real bug found while wiring Tailwind v4: @theme
 * compiles into `@layer theme`, and per the CSS Cascade Layers spec, an
 * unlayered rule (tokens.css, plain CSS) always beats a layered one for the
 * same custom property — regardless of source order. Confirmed against the
 * actual built output, not assumed.
 *
 * Consequence: any @theme key that reuses a property name tokens.css already
 * defines with a DIFFERENT intended meaning is silently inert — it looks
 * wired up, generates a utility class, and produces the WRONG color with no
 * error. This test keeps that class of bug from quietly coming back as new
 * @theme keys get added (see the naming-rule comment atop tailwind.css).
 */

const tailwindCss = readFileSync(
  fileURLToPath(new URL("./tailwind.css", import.meta.url)),
  "utf-8",
);
const tokensCss = readFileSync(
  fileURLToPath(new URL("./tokens.css", import.meta.url)),
  "utf-8",
);

function extractThemeBlock(source: string): string {
  const match = source.match(/@theme\s*{([\s\S]*?)\n}/);
  if (!match) throw new Error("Could not find an @theme block in tailwind.css.");
  return match[1]!;
}

function declaredProperties(source: string): Set<string> {
  const names = new Set<string>();
  for (const match of source.matchAll(/--([a-z0-9-]+)\s*:/g)) {
    names.add(match[1]!);
  }
  return names;
}

/** Keys where @theme intentionally reuses tokens.css's own value under the
 * identical name — safe because the MEANING doesn't differ, only whether
 * the declaration is the "live" one (harmless either way). */
const SAFE_SAME_NAME_PASSTHROUGHS = new Set([
  "font-sans",
  "font-mono",
  "text-xs",
  "text-sm",
  "text-base",
  "text-lg",
  "text-xl",
  "text-2xl",
  "text-3xl",
  "text-4xl",
  "leading-tight",
  "leading-snug",
  "leading-normal",
  "leading-relaxed",
  "tracking-tight",
  "tracking-normal",
  "tracking-wide",
  "radius-sm",
  "radius-md",
  "radius-lg",
  "radius-xl",
  "radius-pill",
  "shadow-xs",
  "shadow-sm",
  "shadow-md",
  "shadow-lg",
  "shadow-focus",
  "ease-standard",
  "ease-out",
  "ease-bounce",
  "duration-fast",
  "duration-base",
  "duration-slow",
]);

/** Renamed on purpose — must never reappear as a bare @theme key (see
 * tailwind.css's naming-rule comment for why each was renamed). */
const MUST_NOT_BE_THEME_KEYS = [
  "color-primary",
  "color-secondary",
  "color-success",
  "color-success-soft",
  "color-success-text",
  "color-warning",
  "color-warning-soft",
  "color-warning-text",
  "color-error",
  "color-error-soft",
  "color-error-text",
  "color-info",
  "color-info-soft",
  "color-info-text",
];

describe("tailwind.css @theme naming", () => {
  const themeBlock = extractThemeBlock(tailwindCss);
  const declarations = [...themeBlock.matchAll(/--([a-z0-9-]+)\s*:\s*var\(--([a-z0-9-]+)\)/g)]
    .map((match) => ({ key: match[1]!, source: match[2]! }));

  it("has at least the expected number of var()-backed declarations (sanity check)", () => {
    expect(declarations.length).toBeGreaterThan(30);
  });

  it("only self-referencing (--x: var(--x)) declarations are on the safe pass-through allowlist", () => {
    const selfReferencing = declarations.filter((d) => d.key === d.source);
    const unexpected = selfReferencing.filter((d) => !SAFE_SAME_NAME_PASSTHROUGHS.has(d.key));
    expect(unexpected).toEqual([]);
  });

  it.each(MUST_NOT_BE_THEME_KEYS)(
    "does not redeclare tokens.css's %s as a bare @theme key",
    (name) => {
      // Matches only a @theme *declaration* of this exact property name,
      // not a var(--name) *reference* to it (references are fine and expected).
      const declaresIt = new RegExp(`--${name}\\s*:`).test(themeBlock);
      expect(declaresIt).toBe(false);
    },
  );

  it("the renamed brand/status/ink keys exist and reference the correct tokens.css source", () => {
    const byKey = new Map(declarations.map((d) => [d.key, d.source]));
    expect(byKey.get("color-brand")).toBe("color-primary");
    expect(byKey.get("color-action-secondary")).toBe("color-secondary");
    expect(byKey.get("color-action-accent")).toBe("color-accent");
    expect(byKey.get("color-status-success")).toBe("color-success");
    expect(byKey.get("color-status-warning")).toBe("color-warning");
    expect(byKey.get("color-status-error")).toBe("color-error");
    expect(byKey.get("color-status-info")).toBe("color-info");
    expect(byKey.get("color-ink")).toBe("text-primary");
    expect(byKey.get("color-ink-secondary")).toBe("text-secondary");
  });

  it("every non-passthrough @theme key that shares a name with a tokens.css property also shares its intended meaning", () => {
    // Cross-check: every key still declared as a bare tokens.css semantic
    // name (color-primary, color-success, ...) must NOT appear as an @theme
    // key at all, given tokens.css's own declaration of that exact name
    // already exists (found in tokens.css) and always wins per the layering
    // rule documented in tailwind.css.
    const tokensDeclaredNames = declaredProperties(tokensCss);
    const themeDeclaredNames = declaredProperties(themeBlock);
    const collisions = [...themeDeclaredNames].filter(
      (name) => tokensDeclaredNames.has(name) && !SAFE_SAME_NAME_PASSTHROUGHS.has(name),
    );
    expect(collisions).toEqual([]);
  });
});
