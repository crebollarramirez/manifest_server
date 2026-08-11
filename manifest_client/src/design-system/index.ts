/**
 * The design system's public surface. Consumers import from here — never by
 * reaching into individual component files — so the internal file layout can
 * be reorganized later without touching call sites anywhere else in the app.
 */
export { ThemeProvider, useTheme } from "./ThemeProvider";
export type { ThemePreference, ResolvedTheme } from "./ThemeProvider";

export { Button } from "./Button";
export type { ButtonProps, ButtonVariant, ButtonSize } from "./Button";

export { GlassPanel } from "./GlassPanel";
export type { GlassPanelProps, GlassVariant } from "./GlassPanel";

export { Card } from "./Card";
export type { CardProps } from "./Card";

export { Badge, statusToBadgeVariant } from "./Badge";
export type { BadgeProps, BadgeVariant } from "./Badge";

export { IconButton } from "./IconButton";
export type { IconButtonProps, IconButtonVariant, IconButtonSize } from "./IconButton";

export { Select } from "./Select";
export type { SelectProps, SelectOption } from "./Select";

export { Switch } from "./Switch";
export type { SwitchProps } from "./Switch";

export { Slider } from "./Slider";
export type { SliderProps } from "./Slider";

export { Tabs } from "./Tabs";
export type { TabsProps, TabOption } from "./Tabs";
