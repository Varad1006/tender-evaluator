import { Alert, Box, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { pdfLinks } from '../api/client';
import type { Issue, RankingEntry, Standing } from '../api/types';
import { requirementHref } from '../components/BundlePanels';
import { CitationTag } from '../components/CitationTag';
import { Busy, PageTitle, Panel } from '../components/common';
import { VerdictMatrix } from '../components/VerdictMatrix';
import { useEvaluation } from '../hooks/useEvaluation';
import { useTender } from '../hooks/useTender';
import { standingStyle, tokens, verdictStyle } from '../theme';
import { Legend } from './VerdictsPage';

const KIND_STYLE: Record<RankingEntry['kind'], { accent: string; border: string; badgeBg: string; badgeFg: string; status: string }> = {
  qualified: { accent: tokens.green, border: tokens.border, badgeBg: tokens.hover, badgeFg: tokens.inkSoft, status: 'Qualified' },
  pending: { accent: verdictStyle.unknown.solid, border: verdictStyle.unknown.border, badgeBg: verdictStyle.unknown.bg, badgeFg: verdictStyle.unknown.fg, status: 'Pending verification' },
  disqualified: { accent: verdictStyle.fail.solid, border: verdictStyle.fail.border, badgeBg: verdictStyle.fail.bg, badgeFg: verdictStyle.fail.fg, status: 'Disqualified' },
};

function StatTile({ kind, value }: { kind: Standing; value: number }) {
  const s = standingStyle[kind];
  const caption = { eligible: 'passed every gating criterion', pending: 'at least one criterion undetermined', disqualified: 'failed a gating criterion' }[kind];
  return (
    <Panel sx={{ p: 2.25, borderTop: `3px solid ${s.solid}` }}>
      <Typography variant="overline" sx={{ color: s.fg }}>{s.label}</Typography>
      <Typography sx={{ fontSize: 28, fontWeight: 600, color: tokens.ink, lineHeight: 1.2 }}>{value}</Typography>
      <Typography sx={{ fontSize: 12.5, color: tokens.muted }}>{caption}</Typography>
    </Panel>
  );
}

function IssueLine({ issue, tenderId }: { issue: Issue; tenderId: string }) {
  const s = verdictStyle[issue.verdict];
  return (
    <Stack direction="row" spacing={1.25} sx={{ alignItems: 'flex-start', py: 0.75 }}>
      <Box sx={{ mt: '5px', width: 8, height: 8, borderRadius: '2px', bgcolor: s.solid, flexShrink: 0 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontSize: 13, color: tokens.ink }}>
          <Box component="span" sx={{ fontWeight: 600 }}>{issue.criterion_label}</Box>
          <Box component="span" sx={{ color: s.fg, fontWeight: 600 }}> · {s.label}</Box>
        </Typography>
        <Typography sx={{ fontSize: 12.5, color: tokens.muted, lineHeight: 1.45 }}>{issue.reason}</Typography>
        <Stack direction="row" spacing={0.75} useFlexGap sx={{ flexWrap: 'wrap', mt: 0.5 }}>
          {issue.requirement.page ? (
            <CitationTag href={requirementHref(tenderId, issue.requirement.document_id, issue.requirement.page)}
              page={issue.requirement.page} source="Tender" tooltip={issue.requirement.quote ?? undefined} />
          ) : null}
          {issue.evidence.map((e, i) => e.document_id && (
            <CitationTag key={i} href={pdfLinks.bidderDoc(e.document_id, e.page)} page={e.page} source="Bid" tooltip={e.quote} />
          ))}
        </Stack>
      </Box>
    </Stack>
  );
}

function RankCard({ entry, tenderId }: { entry: RankingEntry; tenderId: string }) {
  const k = KIND_STYLE[entry.kind];
  const badge = entry.is_top ? { bg: tokens.green, fg: '#fff' } : { bg: k.badgeBg, fg: k.badgeFg };
  return (
    <Panel sx={{ border: `1px solid ${k.border}`, borderLeft: `3px solid ${k.accent}`, borderRadius: 2 }}>
      <Stack direction="row" spacing={2.25} sx={{ alignItems: 'center', px: 2.5, py: 2 }}>
        <Box sx={{ width: 36, height: 36, borderRadius: '50%', bgcolor: badge.bg, color: badge.fg, display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 14, flexShrink: 0 }}>
          {entry.rank_label}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ fontSize: 14.5, fontWeight: 600, color: tokens.ink }}>{entry.name}</Typography>
          <Typography sx={{ fontSize: 12.5, color: tokens.muted, mt: 0.25 }}>{entry.basis}</Typography>
        </Box>
        <Box sx={{ textAlign: 'right', flexShrink: 0 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'flex-end' }}>
            {entry.price_citation && (
              <CitationTag href={pdfLinks.bidderDoc(entry.price_citation.document_id, entry.price_citation.page)}
                page={entry.price_citation.page} tooltip={entry.price_citation.quote} />
            )}
            <Typography sx={{ fontSize: 15, fontWeight: 600, color: tokens.ink }}>{entry.price_display}</Typography>
          </Stack>
          <Box sx={{ fontSize: 11.5, fontWeight: 600, px: 1, py: '2px', borderRadius: 1, bgcolor: entry.kind === 'qualified' ? verdictStyle.pass.bg : k.badgeBg, color: entry.kind === 'qualified' ? tokens.green : k.badgeFg, mt: 0.5, display: 'inline-block' }}>
            {entry.is_top ? 'Qualified — L1' : k.status}
          </Box>
        </Box>
      </Stack>
      {entry.issues.length > 0 && (
        <Box sx={{ borderTop: `1px solid ${tokens.rowBorder}`, px: 2.5, py: 1.25, pl: { sm: 9.5 } }}>
          {entry.issues.map((issue) => <IssueLine key={issue.criterion_id} issue={issue} tenderId={tenderId} />)}
        </Box>
      )}
    </Panel>
  );
}

export function ComparisonPage() {
  const { tender } = useTender();
  const [issuesOnly, setIssuesOnly] = useState(false);
  const { data, error } = useEvaluation(tender?.id ?? '', tender?.evaluated);
  if (!tender) return null;
  if (!tender.evaluated) return <Navigate to={`/tenders/${tender.id}/bidders`} replace />;
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data?.ranking) return <Busy title="Computing comparison…" />;

  const ranking = data.ranking;
  const award = data.tender.award_mechanism_citation;

  return (
    <Box sx={{ animation: 'te-fade-in 0.3s ease' }}>
      <PageTitle title="Eligibility Screen" subtitle={`All ${data.bidders.length} bidders against all ${data.criteria.length} criteria, computed live from the verdict table.`} />
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' }, gap: 2, mb: 4 }}>
        {(['eligible', 'pending', 'disqualified'] as Standing[]).map((k) => <StatTile key={k} kind={k} value={data.eligibility[k]} />)}
      </Box>

      <PageTitle
        title="Comparison Matrix"
        subtitle="Each cell shows the verdict and the figure the bidder actually stated. Click a verdict for its dual citation."
        actions={
          <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <Legend />
            <ToggleButtonGroup size="small" exclusive value={issuesOnly ? 'issues' : 'all'} onChange={(_, v) => v && setIssuesOnly(v === 'issues')}>
              <ToggleButton value="all" sx={{ px: 1.5, fontSize: 12.5, whiteSpace: 'nowrap' }}>All criteria</ToggleButton>
              <ToggleButton value="issues" sx={{ px: 1.5, fontSize: 12.5, whiteSpace: 'nowrap' }}>Rows with issues</ToggleButton>
            </ToggleButtonGroup>
          </Stack>
        }
      />
      <Panel sx={{ overflow: 'hidden', mb: 4 }}>
        <VerdictMatrix evaluation={data} showValues issuesOnly={issuesOnly} />
      </Panel>

      <PageTitle
        title="Ranked Shortlist"
        subtitle={
          <Stack component="span" direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <span>Basis: {ranking.mechanism_label ?? 'award mechanism not found'}</span>
            {award && <CitationTag href={pdfLinks.tender(tender.id, award.page)} page={award.page} source="Tender" tooltip={award.quote} />}
          </Stack>
        }
      />
      {ranking.warning && <Alert severity="warning" sx={{ mb: 2 }}>{ranking.warning}</Alert>}
      <Stack spacing={1.25}>
        {ranking.entries.map((e) => <RankCard key={e.bidder_id} entry={e} tenderId={tender.id} />)}
      </Stack>
      <Typography sx={{ fontSize: 12.5, color: tokens.muted, mt: 2 }}>
        This is an evidence-backed recommendation, not a decision. The committee chair's sign-off selects the winning bidder.
      </Typography>
    </Box>
  );
}
