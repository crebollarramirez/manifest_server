import { forwardRef, type InputHTMLAttributes } from "react";
import styles from "./Slider.module.css";

export type SliderProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  label: string;
};

/** Token-styled native range input. */
export const Slider = forwardRef<HTMLInputElement, SliderProps>(
  ({ label, className, ...rest }, ref) => {
    return (
      <input
        ref={ref}
        type="range"
        aria-label={label}
        className={[styles.slider, className].filter(Boolean).join(" ")}
        {...rest}
      />
    );
  },
);
Slider.displayName = "Slider";
