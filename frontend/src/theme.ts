import { createTheme } from '@mui/material/styles';

/**
 * Single theme file (FSD 3.1): every colour used by the app lives here. Components reference
 * these tokens through the `sx` prop; there are no inline `style={{}}` props or ad-hoc CSS files.
 * Palette carried over from the Tender Evaluator PoC prototype.
 */
export const tokens = {
  ink: '#14181C',
  inkSoft: '#374151',
  muted: '#6B7280',
  faint: '#9CA3AF',
  border: '#E2E5E9',
  rowBorder: '#EEF0F1',
  page: '#F5F6F7',
  surface: '#FFFFFF',
  subtle: '#FAFBFB',
  quoteBg: '#F7F8FA',
  hover: '#F0F2F3',
  headerBg: '#14181C',
  headerMuted: '#9BA3AB',
  headerBorder: '#3A3F44',
  green: '#166534',
  greenDark: '#0F4C2A',
  greenTint: '#ECFDF3',
  greenBorder: '#BFE6C9',
  mono: "'IBM Plex Mono', ui-monospace, monospace",
  sans: "'IBM Plex Sans', 'Noto Sans Devanagari', system-ui, sans-serif",
} as const;

/** `not_matched` is a figure that was read and falls short; `fail` is a rule broken outright.
 *  Both disqualify — they differ in what the committee is being told. */
export type Verdict = 'pass' | 'not_matched' | 'fail' | 'unknown';

export const verdictStyle: Record<Verdict, { label: string; bg: string; fg: string; border: string; solid: string }> = {
  pass: { label: 'Pass', bg: '#E8F6EC', fg: '#166534', border: '#BFE6C9', solid: '#166534' },
  not_matched: { label: 'Not matched', bg: '#FDECEC', fg: '#B42318', border: '#F3B7B0', solid: '#B42318' },
  fail: { label: 'Fail', bg: '#FDECEC', fg: '#B42318', border: '#F3B7B0', solid: '#B42318' },
  unknown: { label: 'Unknown', bg: '#FFF6E5', fg: '#92600C', border: '#F5D68A', solid: '#92600C' },
};

/** A condition that informs the committee but decides nothing (bid validity, rate analysis): neutral, not amber. */
export const notGateStyle = { label: 'Not a gate', bg: '#F0F2F3', fg: '#4B5563', border: '#D5D9DE', solid: '#6B7280' };

/** How a verdict looks where it sits: a pass reads the same everywhere; anything else on a non-gating
 * criterion is neutral, since it cannot hold up or disqualify a bid. */
export function verdictLook(verdict: Verdict, gating = true) {
  if (gating || verdict === 'pass') return verdictStyle[verdict];
  return verdict === 'fail' || verdict === 'not_matched'
    ? { ...notGateStyle, label: 'Not met (not a gate)' }
    : notGateStyle;
}

export const bucketStyle: Record<string, { bg: string; fg: string }> = {
  Financial: { bg: '#EEF2FF', fg: '#4338CA' },
  Technical: { bg: '#ECFDF3', fg: '#166534' },
  Eligibility: { bg: '#F0F2F3', fg: '#374151' },
  Commercial: { bg: '#FFF6E5', fg: '#92600C' },
};

export const standingStyle = {
  eligible: { ...verdictStyle.pass, label: 'Eligible' },
  pending: { ...verdictStyle.unknown, label: 'Pending' },
  disqualified: { ...verdictStyle.not_matched, label: 'Disqualified' },
} as const;

export const theme = createTheme({
  palette: {
    primary: { main: tokens.green, dark: tokens.greenDark, contrastText: '#fff' },
    error: { main: verdictStyle.not_matched.fg },
    warning: { main: verdictStyle.unknown.fg },
    success: { main: tokens.green },
    text: { primary: tokens.ink, secondary: tokens.muted },
    divider: tokens.border,
    background: { default: tokens.page, paper: tokens.surface },
  },
  typography: {
    fontFamily: tokens.sans,
    button: { textTransform: 'none', fontWeight: 600, letterSpacing: 0 },
    h5: { fontSize: 18, fontWeight: 600 },
    h6: { fontSize: 15, fontWeight: 600 },
    body2: { fontSize: 13.5 },
    overline: { fontSize: 11, fontWeight: 600, letterSpacing: 0.4, lineHeight: 1.6 },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: tokens.page },
        '@keyframes te-fade-in': {
          from: { opacity: 0, transform: 'translateY(6px)' },
          to: { opacity: 1, transform: 'translateY(0)' },
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { borderRadius: 7, padding: '9px 20px' } },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: { outlined: { borderColor: tokens.border, borderRadius: 10 } },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
    MuiTooltip: { styleOverrides: { tooltip: { fontSize: 12.5, backgroundColor: tokens.ink, maxWidth: 380 } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 8 } } },
  },
});
