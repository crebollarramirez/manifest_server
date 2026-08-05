import { forwardRef, type HTMLAttributes } from "react";
import styles from "./Badge.module.css";
import type { JobStatus } from "../api/schemas";

export type BadgeVariant = "neutral" | "info" | "success" | "warning" | "error";

export type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  variant?: BadgeVariant;
};

/** Status pill. See statusToBadgeVariant() for the JobStatus -> BadgeVariant mapping. */
export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ variant = "neutral", className, ...rest }, ref) => {
    const classes = [styles.badge, styles[variant], className].filter(Boolean).join(" ");
    return <span ref={ref} className={classes} {...rest} />;
  },
);
Badge.displayName = "Badge";

/**
 * Maps the backend's job-status vocabulary (schemas.ts JobStatus — the Zod
 * enum, not a redeclared union) onto a badge variant. Exhaustive switch,
 * compiler-enforced: adding a status to the Zod enum without updating this
 * mapping is a type error, not a silent runtime gap. Centralized here so
 * Phase 4's job-status UI (and anything else that shows a status) never
 * re-derives this mapping itself.
 */
export function statusToBadgeVariant(status: JobStatus): BadgeVariant {
  switch (status) {
    case "queued":
      return "neutral";
    case "running":
      return "info";
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "cancelled":
      return "neutral";
  }
}
