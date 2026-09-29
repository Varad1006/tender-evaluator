import { Box, Button, CircularProgress, Stack, Typography, type ButtonProps } from '@mui/material';
import { useRef, type ChangeEvent, type ReactNode } from 'react';
import { tokens } from '../theme';

/** A button that opens a file picker (PDF by default; tender bundles also take zip/rar/xls, several at once). */
export function FilePickerButton({ onFiles, accept = 'application/pdf,.pdf', multiple = false, children, ...props }:
  ButtonProps & { onFiles: (files: File[]) => void; accept?: string; multiple?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button {...props} onClick={() => input.current?.click()}>
        {children}
      </Button>
      <Box
        component="input"
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = '';
          if (files.length) onFiles(files);
        }}
      />
    </>
  );
}

/** A button that opens a PDF picker. */
export function PdfPickerButton({ onFile, children, ...props }: ButtonProps & { onFile: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button {...props} onClick={() => input.current?.click()}>
        {children}
      </Button>
      <Box
        component="input"
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) onFile(file);
        }}
      />
    </>
  );
}

export function PageTitle({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ alignItems: { xs: 'flex-start', sm: 'flex-end' }, justifyContent: 'space-between', mb: 2.5 }}>
      <Box>
        <Typography variant="h5" sx={{ color: tokens.ink, mb: 0.5 }}>{title}</Typography>
        {subtitle && <Typography variant="body2" sx={{ color: tokens.muted }}>{subtitle}</Typography>}
      </Box>
      {actions}
    </Stack>
  );
}

export function Panel({ children, sx }: { children: ReactNode; sx?: object }) {
  return (
    <Box sx={{ bgcolor: tokens.surface, border: `1px solid ${tokens.border}`, borderRadius: '10px', animation: 'te-fade-in 0.3s ease', ...sx }}>
      {children}
    </Box>
  );
}

export function Busy({ title, detail }: { title: string; detail?: string }) {
  return (
    <Panel sx={{ py: 12, px: 2.5, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
      <CircularProgress size={34} thickness={4} sx={{ mb: 2.25 }} />
      <Typography sx={{ fontSize: 15, fontWeight: 600, color: tokens.ink, mb: 0.5 }}>{title}</Typography>
      {detail && <Typography sx={{ fontSize: 13, color: tokens.muted }}>{detail}</Typography>}
    </Panel>
  );
}
