import { Box } from '@mui/material';
import { bucketStyle } from '../theme';

export function BucketChip({ bucket }: { bucket: string }) {
  const s = bucketStyle[bucket] ?? bucketStyle.Eligibility;
  return (
    <Box
      component="span"
      sx={{ fontSize: 11.5, px: 1, py: '3px', borderRadius: '5px', bgcolor: s.bg, color: s.fg, fontWeight: 600, whiteSpace: 'nowrap' }}
    >
      {bucket}
    </Box>
  );
}
