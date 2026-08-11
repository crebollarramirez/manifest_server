import { forwardRef, type ButtonHTMLAttributes } from "react";
import styles from "./IconButton.module.css";

export type IconButtonVariant = "ghost" | "soft" | "dangerSoft" | "primary";
export type IconButtonSize = "sm" | "md";

export type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  /** Accessible name — required since the button typically renders an icon only. */
  label: string;
};

/**
 * Icon-only circular button. `label` is mandatory and becomes aria-label,
 * since these buttons carry no visible text (undo/redo, zoom, theme toggle,
 * mic, ruler, ...).
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ variant = "ghost", size = "md", label, className, type = "button", ...rest }, ref) => {
    const classes = [styles.button, styles[variant], styles[size], className]
      .filter(Boolean)
      .join(" ");
    return (
      <button ref={ref} type={type} className={classes} aria-label={label} title={label} {...rest} />
    );
  },
);
IconButton.displayName = "IconButton";
