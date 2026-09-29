import { Avatar, Box, ButtonBase, Chip, Container, Stack, Typography } from '@mui/material';
import type { ReactNode } from 'react';
import { Link as RouterLink, useLocation, useNavigate } from 'react-router-dom';
import type { TenderDetail } from '../api/types';
import { tokens } from '../theme';

interface Step {
  number: number;
  label: string;
  to: string | null;
  active: boolean;
  done: boolean;
}

function steps(tender: TenderDetail | null, path: string): Step[] {
  const id = tender?.id;
  const ready = tender?.extraction_status === 'ready';
  const evaluated = Boolean(tender?.evaluated);
  return [
    { number: 1, label: 'Tender & Criteria', to: id ? `/tenders/${id}/criteria` : null, active: !id || path.endsWith('/criteria'), done: ready },
    {
      number: 2,
      label: 'Bidders & Verdicts',
      to: ready ? `/tenders/${id}/${evaluated && !tender?.evaluation_stale ? 'verdicts' : 'bidders'}` : null,
      active: path.endsWith('/bidders') || path.endsWith('/verdicts'),
      done: evaluated,
    },
    { number: 3, label: 'Comparison & Ranking', to: evaluated ? `/tenders/${id}/comparison` : null, active: path.endsWith('/comparison'), done: false },
  ];
}

const STATUS_COLOR: Record<string, string> = {
  Draft: tokens.headerMuted,
  'Under Evaluation': '#F5D68A',
};

export function AppShell({ tender, children }: { tender: TenderDetail | null; children: ReactNode }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  return (
    <Box sx={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Box component="header" sx={{ bgcolor: tokens.headerBg, color: '#fff', px: { xs: 2, sm: 3.5 }, py: 1.75 }}>
        <Stack direction="row" useFlexGap spacing={1.5} sx={{ alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <ButtonBase component={RouterLink} to="/" sx={{ gap: 1.25, borderRadius: 1 }}>
            <Box sx={{ width: 28, height: 28, borderRadius: '6px', bgcolor: tokens.green, display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 14 }}>
              TE
            </Box>
            <Typography sx={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.2 }}>Tender Evaluator</Typography>
            <Box sx={{ fontSize: 11, color: tokens.headerMuted, border: `1px solid ${tokens.headerBorder}`, borderRadius: 1, px: 0.875, py: '2px' }}>
              PoC BUILD
            </Box>
          </ButtonBase>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center', fontSize: 13, color: '#C6CBCF' }}>
            {tender?.ref_no && (
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <Typography sx={{ fontSize: 13, fontFamily: tokens.mono }}>{tender.ref_no}</Typography>
                <Chip size="small" label={tender.status}
                  sx={{ height: 22, fontSize: 11, bgcolor: 'transparent', color: STATUS_COLOR[tender.status] ?? '#fff', border: `1px solid ${tokens.headerBorder}` }} />
              </Stack>
            )}
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Avatar sx={{ width: 24, height: 24, bgcolor: tokens.headerBorder, fontSize: 11, fontWeight: 600 }}>EV</Avatar>
              <Typography sx={{ fontSize: 13, display: { xs: 'none', sm: 'block' } }}>Evaluator (Demo)</Typography>
            </Stack>
          </Stack>
        </Stack>
      </Box>

      <Box component="nav" sx={{ bgcolor: tokens.surface, borderBottom: `1px solid ${tokens.border}`, px: { xs: 1, sm: 3.5 } }}>
        <Stack direction="row" sx={{ maxWidth: 1240, mx: 'auto', overflowX: 'auto' }}>
          {steps(tender, pathname).map((step) => {
            const circle = step.active
              ? { bg: tokens.green, fg: '#fff', text: tokens.ink }
              : step.done
                ? { bg: tokens.greenTint, fg: tokens.green, text: tokens.inkSoft }
                : step.to
                  ? { bg: tokens.hover, fg: tokens.inkSoft, text: tokens.inkSoft }
                  : { bg: tokens.hover, fg: tokens.faint, text: tokens.faint };
            return (
              <ButtonBase
                key={step.number}
                disabled={!step.to}
                onClick={() => step.to && navigate(step.to)}
                sx={{
                  gap: 1.25, px: 2.75, py: 2, flexShrink: 0,
                  borderBottom: `2px solid ${step.active ? tokens.green : 'transparent'}`,
                  '&:hover': { bgcolor: tokens.subtle },
                }}
              >
                <Box sx={{ width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 600, bgcolor: circle.bg, color: circle.fg }}>
                  {step.done && !step.active ? '✓' : step.number}
                </Box>
                <Typography sx={{ fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap', color: circle.text }}>{step.label}</Typography>
              </ButtonBase>
            );
          })}
        </Stack>
      </Box>

      <Box component="main" sx={{ flex: 1, px: { xs: 2, sm: 3 }, pt: 4, pb: 8 }}>
        <Container maxWidth={false} disableGutters sx={{ maxWidth: 1240 }}>
          {children}
        </Container>
      </Box>
    </Box>
  );
}
