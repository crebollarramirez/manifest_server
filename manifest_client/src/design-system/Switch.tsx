import { forwardRef, type InputHTMLAttributes } from "react";
import styles from "./Switch.module.css";

export type SwitchProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  label: string;
};

/** Toggle switch — a styled native checkbox, so keyboard/a11y behavior is free. */
export const Switch = forwardRef<HTMLInputElement, SwitchProps>(
  ({ label, className, ...rest }, ref) => {
    return (
      <input
        ref={ref}
        type="checkbox"
        role="switch"
        aria-label={label}
        className={[styles.track, className].filter(Boolean).join(" ")}
        {...rest}
      />
    );
  },
);
Switch.displayName = "Switch";
