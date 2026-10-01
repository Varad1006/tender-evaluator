import { Alert, Box, Button, Stack, Typography } from '@mui/material';
import { Navigate, useNavigate } from 'react-router-dom';
import { Busy, PageTitle, Panel } from '../components/common';
import { VerdictMatrix } from '../components/VerdictMatrix';
import { useEvaluation } from '../hooks/useEvaluation';
import { useTender } from '../hooks/useTender';
import { notGateStyle, tokens, verdictStyle, type Verdict } from '../theme';

export function Legend() {
  return (
    <Stack direction="row" spacing={1.75} sx={{ fontSize: 12.5, color: tokens.muted }}>
      {[...(['pass', 'fail', 'unknown'] as Verdict[]).map((v) => verdictStyle[v]), notGateStyle].map((s) => (
        <Stack key={s.label} direction="row" spacing={0.75} sx={{ alignItems: 'center' }}>
          <Box sx={{ width: 9, height: 9, borderRadius: '2px', bgcolor: s.solid }} />
          <span>{s.label}</span>
        </Stack>
      ))}
    </Stack>
  );
}

export function VerdictsPage() {
  const { tender } = useTender();
  const navigate = useNavigate();
  const { data, error } = useEvaluation(tender?.id ?? '', tender?.evaluated);
  if (!tender) return null;
  if (!tender.evaluated) return <Navigate to={`/tenders/${tender.id}/bidders`} replace />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Busy title="Loading verdicts…" />;

  const gating = new Map(data.criteria.map((c) => [c.id, c.gating]));
  const counts = data.verdicts.reduce((acc, v) => {
    const key = v.verdict !== 'pass' && gating.get(v.criterion_id) === false ? 'not_gate' : v.verdict;
    return { ...acc, [key]: (acc[key] ?? 0) + 1 };
  }, {} as Record<string, number>);

  return (
    <Box sx={{ animation: 'te-fade-in 0.3s ease' }}>
      <PageTitle
        title="Verdict Grid"
        subtitle="Click any verdict to see the tender requirement and the bidder's evidence, both cited by page."
        actions={<Legend />}
      />
      {tender.evaluation_stale && (
        <Alert severity="warning" sx={{ mb: 2 }}
          action={<Button color="inherit" size="small" onClick={() => navigate(`/tenders/${tender.id}/bidders`)}>Go to bidders</Button>}>
          Bidders changed since this evaluation ran. Re-run it to include every current bidder.
        </Alert>
      )}
      <Panel sx={{ overflow: 'hidden' }}>
        <VerdictMatrix evaluation={data} />
      </Panel>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ alignItems: { sm: 'center' }, justifyContent: 'space-between', mt: 2.5 }}>
        <Typography sx={{ fontSize: 13, color: tokens.muted }}>
          {data.verdicts.length} verdicts · {counts.pass ?? 0} pass · {counts.fail ?? 0} fail · {counts.unknown ?? 0} unknown ·{counts.not_gate ? ` ${counts.not_gate} not a gate ·` : ''}{' '}
          every criterion evaluated for every bidder, even after a failed gate
        </Typography>
        <Button variant="contained" onClick={() => navigate(`/tenders/${tender.id}/comparison`)}>
          View Comparison &amp; Ranking →
        </Button>
      </Stack>
    </Box>
  );
}
