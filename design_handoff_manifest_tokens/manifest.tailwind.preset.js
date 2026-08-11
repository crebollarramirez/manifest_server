/**
 * Manifest Design System — Tailwind preset
 * Maps the CSS variables in manifest-tokens.css to Tailwind's theme so utilities
 * (bg-card, text-primary, rounded-lg, shadow-md, blur-lg …) resolve to tokens and
 * follow the active theme automatically (light / [data-theme="dark"]).
 *
 * Usage (tailwind.config.js):
 *   module.exports = { presets: [require('./manifest.tailwind.preset.js')], content: [...] }
 *
 * Requires manifest-tokens.css to be imported once globally so the vars exist.
 * darkMode is driven by the data-theme attribute (not the `dark:` class).
 */
module.exports = {
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Base ramps
        purple: {
          50: 'var(--purple-50)', 100: 'var(--purple-100)', 200: 'var(--purple-200)',
          300: 'var(--purple-300)', 400: 'var(--purple-400)', 500: 'var(--purple-500)',
          600: 'var(--purple-600)', 700: 'var(--purple-700)', 800: 'var(--purple-800)',
          900: 'var(--purple-900)',
        },
        mint:  { 50: 'var(--mint-50)', 100: 'var(--mint-100)', 300: 'var(--mint-300)', 500: 'var(--mint-500)', 700: 'var(--mint-700)' },
        peach: { 50: 'var(--peach-50)', 100: 'var(--peach-100)', 300: 'var(--peach-300)', 500: 'var(--peach-500)', 700: 'var(--peach-700)' },
        coral: { 100: 'var(--coral-100)', 300: 'var(--coral-300)', 500: 'var(--coral-500)', 700: 'var(--coral-700)' },

        // Semantic (theme-aware)
        page: 'var(--bg-page-flat)',
        card: 'var(--surface-card)',
        'card-tint': 'var(--surface-card-tint)',
        sunken: 'var(--surface-sunken)',
        primary: 'var(--color-primary)',
        'primary-hover': 'var(--color-primary-hover)',
        'primary-active': 'var(--color-primary-active)',
        'primary-soft': 'var(--color-primary-soft)',
        secondary: 'var(--color-secondary)',
        accent: 'var(--color-accent)',
        success: 'var(--color-success)',
        warning: 'var(--color-warning)',
        error: 'var(--color-error)',
        info: 'var(--color-info)',
      },
      textColor: {
        primary: 'var(--text-primary)',
        secondary: 'var(--text-secondary)',
        tertiary: 'var(--text-tertiary)',
        'on-primary': 'var(--text-on-primary)',
        disabled: 'var(--text-disabled)',
        link: 'var(--text-link)',
      },
      borderColor: {
        subtle: 'var(--border-subtle)',
        DEFAULT: 'var(--border-default)',
        strong: 'var(--border-strong)',
        focus: 'var(--border-focus)',
        glass: 'var(--surface-glass-border)',
      },
      backgroundImage: {
        'page-gradient': 'var(--bg-page)',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        xs: 'var(--text-xs)', sm: 'var(--text-sm)', base: 'var(--text-base)',
        lg: 'var(--text-lg)', xl: 'var(--text-xl)', '2xl': 'var(--text-2xl)',
        '3xl': 'var(--text-3xl)', '4xl': 'var(--text-4xl)',
      },
      fontWeight: {
        normal: 'var(--weight-regular)', medium: 'var(--weight-medium)',
        semibold: 'var(--weight-semibold)', bold: 'var(--weight-bold)',
        display: 'var(--weight-display)',
      },
      lineHeight: {
        tight: 'var(--leading-tight)', snug: 'var(--leading-snug)',
        normal: 'var(--leading-normal)', relaxed: 'var(--leading-relaxed)',
      },
      letterSpacing: {
        tight: 'var(--tracking-tight)', normal: 'var(--tracking-normal)', wide: 'var(--tracking-wide)',
      },
      spacing: {
        1: 'var(--space-1)', 2: 'var(--space-2)', 3: 'var(--space-3)', 4: 'var(--space-4)',
        5: 'var(--space-5)', 6: 'var(--space-6)', 8: 'var(--space-8)', 10: 'var(--space-10)',
        12: 'var(--space-12)', 16: 'var(--space-16)', 20: 'var(--space-20)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)', md: 'var(--radius-md)', lg: 'var(--radius-lg)',
        xl: 'var(--radius-xl)', pill: 'var(--radius-pill)',
      },
      boxShadow: {
        xs: 'var(--shadow-xs)', sm: 'var(--shadow-sm)', md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)', focus: 'var(--shadow-focus)',
      },
      blur: {
        sm: 'var(--blur-sm)', md: 'var(--blur-md)', lg: 'var(--blur-lg)',
      },
      maxWidth: { content: 'var(--content-max-width)' },
      width: { sidebar: 'var(--sidebar-width)' },
      transitionTimingFunction: {
        standard: 'var(--ease-standard)', out: 'var(--ease-out)', bounce: 'var(--ease-bounce)',
      },
      transitionDuration: {
        fast: 'var(--duration-fast)', base: 'var(--duration-base)', slow: 'var(--duration-slow)',
      },
    },
  },
};
