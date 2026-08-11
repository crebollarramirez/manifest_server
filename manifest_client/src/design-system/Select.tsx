import { forwardRef, type SelectHTMLAttributes } from "react";
import { CaretDown } from "@phosphor-icons/react";
import styles from "./Select.module.css";

export type SelectOption = { label: string; value: string };

export type SelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> & {
  options: SelectOption[];
};

/** Token-styled native <select> — real keyboard/a11y behavior for free. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ options, className, ...rest }, ref) => {
    return (
      <div className={[styles.wrapper, className].filter(Boolean).join(" ")}>
        <select ref={ref} className={styles.select} {...rest}>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <CaretDown className={styles.caret} weight="bold" aria-hidden="true" />
      </div>
    );
  },
);
Select.displayName = "Select";
