/** SATVA dashboard design tokens.
 *
 * The palette mirrors the mobile app so an officer and a consumer are looking
 * at the same system. Severity colours are deliberately restrained: amber for
 * an advisory pattern, red only where a confirmed chemical reading exceeds the
 * action threshold.
 */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: '#101828', soft: '#475467', faint: '#98A2B3' },
        line: '#E4E7EC',
        canvas: '#F7F8FA',
        brand: { DEFAULT: '#0B6E4F', dark: '#075139', soft: '#E7F4EF' },
        clear: { DEFAULT: '#067647', soft: '#ECFDF3' },
        unsure: { DEFAULT: '#5B7FBE', soft: '#EFF4FF' },
        caution: { DEFAULT: '#B54708', soft: '#FFF6ED' },
        confirmed: { DEFAULT: '#B42318', soft: '#FEF3F2' },
        neutral2: { DEFAULT: '#475467', soft: '#F2F4F7' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)',
      },
    },
  },
  plugins: [],
}
