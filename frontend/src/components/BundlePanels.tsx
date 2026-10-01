import AutoFixHighRoundedIcon from '@mui/icons-material/AutoFixHighRounded';
import CheckCircleOutlineRoundedIcon from '@mui/icons-material/CheckCircleOutlineRounded';
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded';
import { Alert, Box, Chip, LinearProgress, Link, Stack, Tooltip, Typography } from '@mui/material';
import { pdfLinks } from '../api/client';
import type { Criterion, PageKind, TenderDetail, TenderFact } from '../api/types';
import { tokens, verdictStyle } from '../theme';
import { CitationTag } from './CitationTag';
import { Panel } from './common';

export const PAGE_KIND: Record<PageKind, { label: string; color: string; help: string }> = {
  english: { label: 'Text', color: '#166534', help: 'Machine-readable English text layer' },
  ocr_en: { label: 'OCR', color: '#4338CA', help: 'Read by OCR (Chandra)' },
  ocr_mr: { label: 'OCR · मराठी', color: '#7C3AED', help: 'Marathi, read by OCR (Chandra)' },
  marathi: { label: 'Marathi (lossy)', color: '#B7791F', help: 'Marathi text layer that drops conjuncts — citable, not quotable' },
  garbled: { label: 'Garbled', color: '#B42318', help: 'Text layer is nonsense (bad OCR or legacy font)' },
  image: { label: 'Scan only', color: '#9CA3AF', help: 'No text layer' },
  blank: { label: 'Blank', color: '#E5E7EB', help: 'Blank or near-blank page (scanned backside, separator) — nothing to read' },
};

const DOC_TYPE: Record<string, string> = {
  notice: 'Notice', conditions: 'Conditions', rfp: 'RFP', datasheet: 'Data sheet', boq: 'BOQ', corrigendum: 'Corrigendum',
  specifications: 'Specs / SOP', approval: 'Approval', other: 'Other',
};

export function OcrBanner({ tender }: { tender: TenderDetail }) {
  const p = tender.ocr_progress || {};
  if (tender.ocr_status === 'queued' || tender.ocr_status === 'running') {
    const pct = p.total ? Math.round(((p.done ?? 0) / p.total) * 100) : 0;
    return (
      <Alert severity="info" icon={<AutoFixHighRoundedIcon />} sx={{ mb: 2, '& .MuiAlert-message': { width: '100%' } }}>
        <Typography sx={{ fontSize: 13.5, fontWeight: 600 }}>
          Reading {p.total} scanned / Marathi pages with Chandra OCR — {p.done ?? 0} of {p.total} done
        </Typography>
        <Typography sx={{ fontSize: 12.5, mb: 1 }}>
          The criteria below come from the text layer and are already usable; they will update when OCR finishes
          (derived criteria become located and quoted).
        </Typography>
        <LinearProgress variant="determinate" value={pct} sx={{ height: 6, borderRadius: 3 }} />
      </Alert>
    );
  }
  if (tender.ocr_status === 'unavailable') {
    return (
      <Alert severity="warning" sx={{ mb: 2 }}>
        {p.total} pages could not be read locally (scans, garbled OCR layers or Marathi) and OCR is not configured.
        Criteria from those pages are shown as derived from the PMC standard.
      </Alert>
    );
  }
  if (tender.ocr_status === 'failed') {
    return <Alert severity="warning" sx={{ mb: 2 }}>OCR could not finish ({(p.errors || [])[0] ?? 'unknown error'}). Local results are shown.</Alert>;
  }
  return null;
}

export function KindStrip({ kinds }: { kinds: PageKind[] }) {
  return (
    <Stack direction="row" sx={{ height: 8, borderRadius: 1, overflow: 'hidden', bgcolor: tokens.hover, width: '100%' }}>
      {kinds.map((k, i) => (
        <Tooltip key={i} title={`p.${i + 1} · ${PAGE_KIND[k]?.help ?? k}`}>
          <Box sx={{ flex: 1, bgcolor: PAGE_KIND[k]?.color ?? tokens.faint, borderRight: kinds.length < 60 ? '1px solid #fff' : 'none' }} />
        </Tooltip>
      ))}
    </Stack>
  );
}

export function DocumentsPanel({ tender }: { tender: TenderDetail }) {
  if (!tender.documents.length) return null;
  return (
    <Panel sx={{ p: '16px 20px', mb: 2.5 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline', mb: 1.5, flexWrap: 'wrap', gap: 1 }}>
        <Typography sx={{ fontSize: 15, fontWeight: 600 }}>
          Tender bundle <Box component="span" sx={{ color: tokens.muted, fontWeight: 500 }}>({tender.documents.length} documents)</Box>
        </Typography>
        <Stack direction="row" spacing={1.5} useFlexGap sx={{ flexWrap: 'wrap' }}>
          {(Object.keys(PAGE_KIND) as PageKind[]).map((k) => (
            <Stack key={k} direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
              <Box sx={{ width: 9, height: 9, borderRadius: '2px', bgcolor: PAGE_KIND[k].color }} />
              <Typography sx={{ fontSize: 11.5, color: tokens.muted }}>{PAGE_KIND[k].label}</Typography>
            </Stack>
          ))}
        </Stack>
      </Stack>
      <Stack spacing={1.25}>
        {tender.documents.map((d) => (
          <Box key={d.id} sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '260px 90px 1fr 70px' }, gap: { xs: 0.5, md: 1.5 }, alignItems: 'center' }}>
            <Tooltip title={d.origin}>
              <Link href={pdfLinks.tenderDoc(d.id)} target="_blank" rel="noopener" underline="hover"
                sx={{ fontFamily: tokens.mono, fontSize: 12, color: tokens.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.file_name}
              </Link>
            </Tooltip>
            <Chip size="small" label={DOC_TYPE[d.doc_type] ?? d.doc_type} sx={{ height: 20, fontSize: 11, bgcolor: tokens.hover, justifySelf: 'start' }} />
            {d.page_kinds.length ? <KindStrip kinds={d.page_kinds} /> : <Typography sx={{ fontSize: 12, color: tokens.faint }}>Spreadsheet</Typography>}
            <Typography sx={{ fontSize: 12, color: tokens.muted, textAlign: { md: 'right' } }}>
              {d.page_count ? `${d.page_count} pp` : ''}
            </Typography>
          </Box>
        ))}
      </Stack>
    </Panel>
  );
}

const FACT_ORDER = ['estimated_cost', 'total_estimated_cost', 'emd', 'security_deposit', 'completion_months', 'dlp_months',
  'form_of_contract', 'contract_type', 'tender_category', 'rainy_season', 'pre_bid_meeting', 'cost_of_tender_doc'];

function FactStatus({ fact }: { fact: TenderFact }) {
  if (fact.status === 'conflict') {
    return <Chip size="small" icon={<ErrorOutlineRoundedIcon />} label="Differs across documents"
      sx={{ height: 20, fontSize: 10.5, bgcolor: verdictStyle.fail.bg, color: verdictStyle.fail.fg, '& .MuiChip-icon': { fontSize: 13, color: verdictStyle.fail.fg } }} />;
  }
  if (fact.status === 'amended') {
    return <Chip size="small" icon={<ErrorOutlineRoundedIcon />} label="Changed by corrigendum"
      sx={{ height: 20, fontSize: 10.5, bgcolor: '#FFF6E5', color: '#92600C', '& .MuiChip-icon': { fontSize: 13, color: '#92600C' } }} />;
  }
  const n = new Set(fact.sources.map((s) => `${s.doc_name}|${s.page}|${s.method}`)).size;
  return (
    <Tooltip title={fact.sources.map((s) => `${s.method} — ${s.doc_name}${s.page ? ` p.${s.page}` : ''}: ${s.quote}`).join('\n')}
      slotProps={{ tooltip: { sx: { whiteSpace: 'pre-line', maxWidth: 520 } } }}>
      <Chip size="small" icon={<CheckCircleOutlineRoundedIcon />} label={n > 1 ? `${n} sources agree` : 'Single source'}
        sx={{ height: 20, fontSize: 10.5, bgcolor: n > 1 ? verdictStyle.pass.bg : tokens.hover, color: n > 1 ? tokens.green : tokens.muted, '& .MuiChip-icon': { fontSize: 13, color: 'inherit' } }} />
    </Tooltip>
  );
}

export function FactsPanel({ tender }: { tender: TenderDetail }) {
  const facts = FACT_ORDER.map((k) => tender.facts.find((f) => f.key === k)).filter((f): f is TenderFact => Boolean(f));
  if (!facts.length) return null;
  return (
    <Panel sx={{ p: '16px 20px', mb: 2.5 }}>
      <Typography sx={{ fontSize: 15, fontWeight: 600, mb: 0.5 }}>Key facts</Typography>
      <Typography sx={{ fontSize: 12.5, color: tokens.muted, mb: 1.5 }}>
        Each figure is read from every structured section that states it (Tender Notice, Work Input Form, B-1 form, conditions table,
        Goshwara, BOQ spreadsheet) and cross-checked. Hover a status to see the sources.
      </Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 1.5 }}>
        {facts.map((f) => {
          const first = (f.status === 'amended' && f.sources.find((s) => s.page && s.method.startsWith('Corrigendum')))
            || f.sources.find((s) => s.page);
          return (
            <Box key={f.key} sx={{ border: `1px solid ${f.status === 'conflict' ? verdictStyle.fail.border : tokens.border}`, borderRadius: 2, p: 1.5 }}>
              <Typography sx={{ fontSize: 11.5, color: tokens.muted, mb: 0.25 }}>{f.label}</Typography>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.75 }}>
                <Typography sx={{ fontSize: 15, fontWeight: 600, color: tokens.ink }}>
                  {f.status === 'conflict' ? [...new Set(f.sources.map((s) => String(s.value)))].slice(0, 3).join(' vs ') : f.display}
                </Typography>
                {first && <CitationTag href={pdfLinks.tenderDoc(first.document_id, first.page)} page={first.page!} tooltip={`${first.doc_name}: ${first.quote}`} />}
              </Stack>
              <FactStatus fact={f} />
              {f.note && <Typography sx={{ fontSize: 11.5, color: tokens.inkSoft, mt: 0.75, lineHeight: 1.4 }}>{f.note}</Typography>}
            </Box>
          );
        })}
      </Box>
    </Panel>
  );
}

const STATUS = {
  located: { label: 'Located', bg: verdictStyle.pass.bg, fg: tokens.green },
  located_ocr: { label: 'Located · OCR', bg: '#EEF2FF', fg: '#4338CA' },
  located_marathi: { label: 'Marathi · cited, not quoted', bg: '#FFF6E5', fg: '#92600C' },
  derived: { label: 'Derived — confirm', bg: verdictStyle.fail.bg, fg: verdictStyle.fail.fg },
} as const;

export function criterionStatus(c: Criterion) {
  if (c.status === 'located' && c.derivation?.source_method === 'ocr') return STATUS.located_ocr;
  return STATUS[c.status] ?? STATUS.located;
}

export function CriterionStatusChip({ c }: { c: Criterion }) {
  const s = criterionStatus(c);
  return <Box component="span" sx={{ fontSize: 10.5, fontWeight: 700, px: 0.75, py: '2px', borderRadius: 1, bgcolor: s.bg, color: s.fg, whiteSpace: 'nowrap' }}>{s.label}</Box>;
}

export function requirementHref(tenderId: string, documentId: string | null, page: number | null) {
  return documentId ? pdfLinks.tenderDoc(documentId, page) : pdfLinks.tender(tenderId, page ?? 1);
}
