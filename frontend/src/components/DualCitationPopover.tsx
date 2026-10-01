import CloseIcon from '@mui/icons-material/Close';
import GavelRoundedIcon from '@mui/icons-material/GavelRounded';
import { Box, Chip, IconButton, Popover, Stack, Typography } from '@mui/material';
import { pdfLinks } from '../api/client';
import type { BidderSummary, Criterion, Evidence, VerdictCell } from '../api/types';
import { notGateStyle, tokens, verdictLook, verdictStyle } from '../theme';
import { requirementHref } from './BundlePanels';
import { CitationTag } from './CitationTag';

const TIER_LABEL: Record<VerdictCell['tier'], string> = {
  deterministic: 'Deterministic check · no AI in the comparison',
  ai_assisted: 'AI-assisted judgement (nature of work) · arithmetic checked in code',
  rule_based: 'Rule-based check · AI judge not configured',
};

const UNKNOWN_NOTE: Partial<Record<NonNullable<VerdictCell['unknown_kind']>, string>> = {
  conflict: 'These figures disagree within the same submission. The engine flags this as Unknown rather than guessing which one governs.',
  not_found: 'Missing evidence is never silently treated as a failure — the evaluator seeks clarification outside the system.',
  low_confidence: 'Needs evaluator judgement.',
};

interface Props {
  anchorEl: HTMLElement | null;
  onClose: () => void;
  cell: VerdictCell | null;
  criterion: Criterion | null;
  bidder: BidderSummary | null;
  tenderId: string;
}

function Quote({ children }: { children: string }) {
  return (
    <Typography sx={{ fontSize: 14, lineHeight: 1.55, fontStyle: 'italic', color: tokens.ink, whiteSpace: 'pre-wrap' }}>
      “{children}”
    </Typography>
  );
}

function ColumnTitle({ children }: { children: string }) {
  return (
    <Typography variant="overline" sx={{ color: tokens.muted, display: 'block', mb: 1 }}>
      {children}
    </Typography>
  );
}

function EvidenceCard({ ev }: { ev: Evidence }) {
  return (
    <Box sx={{ bgcolor: tokens.quoteBg, border: `1px solid ${tokens.border}`, borderRadius: 2, p: '12px 14px' }}>
      <Typography sx={{ fontSize: 11, fontWeight: 600, color: tokens.muted, mb: 0.75 }}>
        {ev.section || 'Bidder submission'}
        {ev.tier === 'llm' && ' · LLM-located, quote verified'}
      </Typography>
      {ev.fetched_from ? (
        <Typography sx={{ fontSize: 11, color: tokens.green, mb: 0.75 }}>
          Read from the {ev.fetched_from.label.charAt(0).toLowerCase() + ev.fetched_from.label.slice(1)} fetched for this requirement
          {' '}(p.{ev.fetched_from.to > ev.fetched_from.from ? `${ev.fetched_from.from}–${ev.fetched_from.to}` : ev.fetched_from.from})
        </Typography>
      ) : ev.fetch_tier === 'whole_bid' ? (
        <Typography sx={{ fontSize: 11, color: '#92600C', mb: 0.75 }}>
          Found elsewhere in the bid — not in a document of the kind this requirement asks for
        </Typography>
      ) : null}
      <Quote>{ev.quote}</Quote>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 1.25 }}>
        {ev.document_id && (
          <CitationTag href={pdfLinks.bidderDoc(ev.document_id, ev.page)} page={ev.page}
            tooltip={`Open ${ev.document_name} at page ${ev.page}`} />
        )}
        <Typography sx={{ fontFamily: tokens.mono, fontSize: 11, color: tokens.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {ev.document_name}
        </Typography>
      </Stack>
    </Box>
  );
}

function NotFoundCard({ title, detail }: { title: string; detail: string }) {
  return (
    <Box sx={{ border: `1px dashed ${verdictStyle.unknown.border}`, bgcolor: '#FFFBF2', borderRadius: 2, p: '12px 14px' }}>
      <Typography sx={{ fontSize: 13.5, fontWeight: 600, color: verdictStyle.unknown.fg, mb: 0.5 }}>{title}</Typography>
      <Typography sx={{ fontSize: 13, color: tokens.inkSoft, lineHeight: 1.5 }}>{detail}</Typography>
    </Box>
  );
}

/** The demo moment: tender requirement and bidder evidence side by side, both page-cited (FSD 7.3 UI-02). */
export function DualCitationPopover({ anchorEl, onClose, cell, criterion, bidder, tenderId }: Props) {
  const open = Boolean(anchorEl && cell && criterion && bidder);
  const gating = criterion?.gating ?? true;
  const style = cell ? verdictLook(cell.verdict, gating) : verdictStyle.pass;
  const pages = bidder?.document?.page_count;

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      transformOrigin={{ vertical: 'top', horizontal: 'center' }}
      slotProps={{
        paper: {
          sx: { width: 'min(880px, calc(100vw - 32px))', borderRadius: 3, boxShadow: '0 20px 60px rgba(0,0,0,0.22)', mt: 1 },
        },
      }}
    >
      {open && cell && criterion && bidder && (
        <Box>
          <Stack direction="row"
            sx={{ alignItems: 'flex-start', justifyContent: 'space-between', px: 3, pt: 2.5, pb: 2, borderBottom: `1px solid ${tokens.border}`, borderTop: `3px solid ${style.solid}` }}>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="overline" sx={{ color: style.fg }}>
                {bidder.name} · {style.label}
              </Typography>
              <Typography sx={{ fontSize: 17, fontWeight: 600, color: tokens.ink }}>{criterion.label}</Typography>
              <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
                <Chip size="small" icon={<GavelRoundedIcon />} label={TIER_LABEL[cell.tier]}
                  sx={{ bgcolor: tokens.hover, color: tokens.inkSoft, fontWeight: 500, fontSize: 11.5, '& .MuiChip-icon': { fontSize: 14 } }} />
                {cell.computation && (
                  <Box component="code" sx={{ fontFamily: tokens.mono, fontSize: 12, color: style.fg, bgcolor: style.bg, border: `1px solid ${style.border}`, borderRadius: 1, px: 1, py: '2px' }}>
                    {cell.computation}
                  </Box>
                )}
              </Stack>
            </Box>
            <IconButton onClick={onClose} size="small" aria-label="Close" sx={{ color: tokens.faint }}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Stack>

          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2.5, p: 3 }}>
            <Box>
              <ColumnTitle>Tender requirement</ColumnTitle>
              <Box sx={{ bgcolor: tokens.quoteBg, border: `1px solid ${tokens.border}`, borderRadius: 2, p: '12px 14px' }}>
                <Typography sx={{ fontSize: 11, fontWeight: 600, color: tokens.muted, mb: 0.75 }}>
                  {[cell.requirement.clause && `Clause ${cell.requirement.clause}`, cell.requirement.section].filter(Boolean).join(' · ')}
                </Typography>
                {cell.requirement.quote ? (
                  <Quote>{cell.requirement.quote}</Quote>
                ) : (
                  <Typography sx={{ fontSize: 13, color: '#92600C', lineHeight: 1.5 }}>
                    {criterion.notes[0] ?? 'Derived from the PMC standard; not located in a readable page.'}
                  </Typography>
                )}
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 1.25 }}>
                  {cell.requirement.page ? (
                    <CitationTag href={requirementHref(tenderId, cell.requirement.document_id, cell.requirement.page)} page={cell.requirement.page}
                      tooltip={`Open ${criterion.source_document_name ?? 'the tender document'} at page ${cell.requirement.page}`} />
                  ) : null}
                  <Typography sx={{ fontSize: 12, color: tokens.muted }}>Threshold: {criterion.threshold_display}</Typography>
                </Stack>
              </Box>
            </Box>

            <Box>
              <ColumnTitle>{cell.evidence.length > 1 ? `Bidder evidence (${cell.evidence.length} statements)` : 'Bidder evidence'}</ColumnTitle>
              <Stack spacing={1.25}>
                {cell.evidence.map((ev, i) => <EvidenceCard key={`${ev.page}-${i}`} ev={ev} />)}
                {!gating && !cell.evidence.length && cell.unknown_kind !== 'not_found' && (
                  <Box sx={{ border: `1px dashed ${notGateStyle.border}`, bgcolor: tokens.subtle, borderRadius: 2, p: '12px 14px' }}>
                    <Typography sx={{ fontSize: 13, color: notGateStyle.fg, lineHeight: 1.5 }}>Nothing to check in the submission at this stage.</Typography>
                  </Box>
                )}
                {cell.unknown_kind === 'not_found' && (
                  <NotFoundCard
                    title={cell.evidence.length ? 'Partly not found in submission' : 'Not found in submission'}
                    detail={`Searched all ${pages ?? ''} pages of ${bidder.document?.file_name ?? 'the submission'}. ${cell.reason}`}
                  />
                )}
              </Stack>
            </Box>
          </Box>

          <Box sx={{ px: 3, pb: 2.5 }}>
            {!gating && cell.verdict !== 'pass' && (
              <Box sx={{ fontSize: 13, color: notGateStyle.fg, bgcolor: notGateStyle.bg, border: `1px solid ${notGateStyle.border}`, borderRadius: 1.5, px: 1.75, py: 1.25, mb: 1.5 }}>
                Not an eligibility gate — recorded for the committee; it does not change this bidder&apos;s standing.
              </Box>
            )}
            {gating && cell.verdict === 'unknown' && cell.unknown_kind && UNKNOWN_NOTE[cell.unknown_kind] && (
              <Box sx={{ fontSize: 13, color: verdictStyle.unknown.fg, bgcolor: verdictStyle.unknown.bg, border: `1px solid ${verdictStyle.unknown.border}`, borderRadius: 1.5, px: 1.75, py: 1.25, mb: 1.5 }}>
                {UNKNOWN_NOTE[cell.unknown_kind]}
              </Box>
            )}
            {cell.unknown_kind !== 'not_found' && (
              <Typography sx={{ fontSize: 13, color: tokens.inkSoft, lineHeight: 1.5 }}>{cell.reason}</Typography>
            )}
          </Box>
        </Box>
      )}
    </Popover>
  );
}
