import CancelRoundedIcon from '@mui/icons-material/CancelRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import HelpRoundedIcon from '@mui/icons-material/HelpRounded';
import { Chip } from '@mui/material';
import type { MouseEvent } from 'react';
import { verdictStyle, type Verdict } from '../theme';

const ICONS = {
  pass: CheckCircleRoundedIcon,
  fail: CancelRoundedIcon,
  unknown: HelpRoundedIcon,
};

interface Props {
  verdict: Verdict;
  onClick?: (e: MouseEvent<HTMLElement>) => void;
  selected?: boolean;
}

export function VerdictChip({ verdict, onClick, selected }: Props) {
  const s = verdictStyle[verdict];
  const Icon = ICONS[verdict];
  return (
    <Chip
      size="small"
      label={s.label}
      icon={<Icon />}
      onClick={onClick}
      aria-label={`${s.label} — show evidence`}
      sx={{
        bgcolor: s.bg,
        color: s.fg,
        border: `1px solid ${s.border}`,
        height: 26,
        px: 0.5,
        fontSize: 12,
        boxShadow: selected ? `0 0 0 2px ${s.solid}` : 'none',
        '& .MuiChip-icon': { color: s.fg, fontSize: 15 },
        '&:hover': { bgcolor: s.bg, filter: 'brightness(0.96)' },
        '&.MuiChip-clickable:active': { boxShadow: `0 0 0 2px ${s.solid}` },
      }}
    />
  );
}
