import styles from "./Tabs.module.css";

export type TabOption = { label: string; value: string };

export type TabsProps = {
  tabs: TabOption[];
  active: string;
  onChange: (value: string) => void;
  className?: string;
};

/** Segmented control — Material/Size/Quality, Rough/Normal/Fine, etc. */
export function Tabs({ tabs, active, onChange, className }: TabsProps) {
  return (
    <div className={[styles.list, className].filter(Boolean).join(" ")} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={tab.value === active}
          className={[styles.tab, tab.value === active && styles.tabActive]
            .filter(Boolean)
            .join(" ")}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
