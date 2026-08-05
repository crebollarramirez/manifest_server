import { forwardRef, type HTMLAttributes } from "react";
import styles from "./Card.module.css";

export type CardProps = HTMLAttributes<HTMLDivElement> & {
  /** Slightly tinted surface for visual grouping without a full glass treatment. */
  tinted?: boolean;
};

/** Solid card surface for dense/long-form content — see GlassPanel for floating UI. */
export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ tinted = false, className, ...rest }, ref) => {
    const classes = [styles.card, tinted && styles.tinted, className]
      .filter(Boolean)
      .join(" ");
    return <div ref={ref} className={classes} {...rest} />;
  },
);
Card.displayName = "Card";
