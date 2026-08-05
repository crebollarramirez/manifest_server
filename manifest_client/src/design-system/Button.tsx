import { forwardRef, type ButtonHTMLAttributes } from "react";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

/** Token-driven button. Every color/spacing value comes from tokens.css — no hardcoded hex. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", className, type = "button", ...rest }, ref) => {
    const classes = [styles.button, styles[variant], styles[size], className]
      .filter(Boolean)
      .join(" ");
    return <button ref={ref} type={type} className={classes} {...rest} />;
  },
);
Button.displayName = "Button";
