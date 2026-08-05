import { forwardRef, type HTMLAttributes } from "react";
import styles from "./GlassPanel.module.css";

export type GlassVariant = "default" | "strong" | "subtle" | "gloss";

export type GlassPanelProps = HTMLAttributes<HTMLDivElement> & {
  variant?: GlassVariant;
};

const VARIANT_CLASS: Record<GlassVariant, string> = {
  default: "glass",
  strong: "glass glass--strong",
  subtle: "glass glass--subtle",
  gloss: "glass--gloss",
};

/**
 * The signature translucent surface, per the handoff: reach for this on
 * floating UI (chat, toolbars, menus) — never for dense/long-form content,
 * which should use <Card> (a solid surface-card) instead.
 */
export const GlassPanel = forwardRef<HTMLDivElement, GlassPanelProps>(
  ({ variant = "default", className, ...rest }, ref) => {
    const classes = [styles.panel, VARIANT_CLASS[variant], className]
      .filter(Boolean)
      .join(" ");
    return <div ref={ref} className={classes} {...rest} />;
  },
);
GlassPanel.displayName = "GlassPanel";
