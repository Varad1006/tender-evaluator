import { Alert, Box, Button, Collapse, Stack, Tooltip, Typography } from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Criterion } from '../api/types';
import { BucketChip } from '../components/BucketChip';
import { CriterionStatusChip, DocumentsPanel, FactsPanel, OcrBanner, requirementHref } from '../components/BundlePanels';
import { CitationTag } from '../components/CitationTag';
import { Busy, Panel } from '../components/common';
import { useTender } from '../hooks/useTender';
import { tokens } from '../theme';

const DEVANAGARI = /[ऀ-ॿ]/;

export function CriteriaPage() {
  const { tender } = useTender();
  const navigate = useNavigate();
  const [showWarnings, setShowWarnings] = useState(false);
  if (!tender) return null;

  if (tender.extraction_status === 'pending' || tender.extraction_status === 'extracting') {
    return <Busy title={`Reading ${tender.doc_name}`} detail="Unpacking the bundle, classifying every page, extracting page-anchored requirements…" />;
  }
  if (tender.extraction_status === 'failed') {
    return (
      <Alert severity="error" action={<Button color="inherit" onClick={() => navigate('/')}>Back</Button>}>
        {tender.extraction_error ?? 'Could not read this tender.'}
      </Alert>
    );
  }

  const columns: GridColDef<Criterion & { n: number }>[] = [
    { field: 'n', headerName: '#', width: 52, sortable: false, renderCell: ({ value }) => <Typography sx={{ fontSize: 12.5, color: tokens.faint }}>{value}</Typography> },
    {
      field: 'label',
      headerName: 'Requirement',
      flex: 2.8,
      minWidth: 320,
      sortable: false,
      renderCell: ({ row }) => (
        <Stack spacing={0.6} sx={{ py: 1.5, pr: 2, whiteSpace: 'normal' }}>
          <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography sx={{ fontSize: 13.5, fontWeight: 500, color: tokens.ink }}>{row.label}</Typography>
            <CriterionStatusChip c={row} />
          </Stack>
          {row.source_quote ? (
            <Typography sx={{
              fontSize: 12.5, color: tokens.muted, fontStyle: DEVANAGARI.test(row.source_quote) ? 'normal' : 'italic', lineHeight: 1.5,
              fontFamily: DEVANAGARI.test(row.source_quote) ? "'Noto Sans Devanagari', " + tokens.sans : tokens.sans,
              display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden',
            }}>
              “{row.source_quote}”
            </Typography>
          ) : null}
          {row.notes.slice(0, 2).map((note, i) => (
            <Typography key={i} sx={{
              fontSize: 11.5, lineHeight: 1.45,
              color: row.status === 'derived' || /does not match|computed on the total|template unit error|changed by hand|differs/.test(note)
                ? '#92600C' : tokens.faint,
            }}>
              {note}
            </Typography>
          ))}
        </Stack>
      ),
    },
    { field: 'bucket', headerName: 'Bucket', flex: 0.8, minWidth: 110, renderCell: ({ value }) => <BucketChip bucket={value} /> },
    {
      field: 'threshold_display', headerName: 'Threshold', flex: 1.3, minWidth: 170, sortable: false,
      renderCell: ({ row }) => {
        const formula = row.derivation?.formula as string | undefined;
        const text = <Typography sx={{ fontSize: 13, color: tokens.ink, whiteSpace: 'normal', py: 1 }}>{row.threshold_display}</Typography>;
        return formula ? <Tooltip title={`Formula: ${formula}`}>{text}</Tooltip> : text;
      },
    },
    {
      field: 'source_page', headerName: 'Source', width: 170,
      renderCell: ({ row }) => (
        <Stack spacing={0.25} sx={{ py: 1, minWidth: 0 }}>
          {row.source_page ? (
            <Box><CitationTag href={requirementHref(tender.id, row.source_document_id, row.source_page)} page={row.source_page}
              tooltip={`Open ${row.source_document_name ?? 'the tender'} at page ${row.source_page}`} /></Box>
          ) : (
            <Typography sx={{ fontSize: 11.5, color: '#92600C' }}>Not located</Typography>
          )}
          <Typography sx={{ fontSize: 11, color: tokens.faint, fontFamily: tokens.mono, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {row.source_document_name ?? ''}{row.source_clause ? ` · cl. ${row.source_clause}` : ''}
          </Typography>
        </Stack>
      ),
    },
  ];

  const counts = tender.criteria.reduce((a, c) => ({ ...a, [c.status]: (a[c.status] ?? 0) + 1 }), {} as Record<string, number>);
  const award = tender.award_mechanism_citation;
  const facts = Object.fromEntries(tender.facts.map((f) => [f.key, f]));

  return (
    <Box sx={{ animation: 'te-fade-in 0.3s ease' }}>
      <Panel sx={{ p: '20px 24px', mb: 2.5 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ justifyContent: 'space-between' }}>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="overline" sx={{ color: tokens.green }}>Tender loaded</Typography>
            <Typography sx={{ fontSize: 18, fontWeight: 600, color: tokens.ink, mb: 0.5, fontFamily: "'Noto Sans Devanagari', " + tokens.sans }}>
              {tender.title}
            </Typography>
            <Typography sx={{ fontSize: 13, color: tokens.muted }}>
              {[tender.ref_no, tender.authority, tender.estimated_value_display && `Estimated cost ${tender.estimated_value_display}`,
                facts.completion_months && `${facts.completion_months.display}`, tender.page_count && `${tender.page_count} pages`]
                .filter(Boolean).join(' · ')}
            </Typography>
            {tender.timings?.local_seconds !== undefined && (
              <Typography sx={{ fontSize: 11.5, color: tokens.faint, mt: 0.5 }}>
                Local pass {tender.timings.local_seconds}s{tender.timings.ocr_seconds !== undefined ? ` · OCR pass ${tender.timings.ocr_seconds}s` : ''}
                {tender.ocr_progress?.cached ? ` (${tender.ocr_progress.cached} pages from cache)` : ''}
              </Typography>
            )}
          </Box>
          <Box sx={{ textAlign: { md: 'right' }, flexShrink: 0, maxWidth: 360 }}>
            <Typography sx={{ fontSize: 13, color: tokens.muted, mb: 0.5 }}>Award mechanism</Typography>
            <Typography sx={{ fontWeight: 600, color: tokens.ink, fontSize: 14, mb: 0.5 }}>
              {tender.award_mechanism_label ?? 'Not found in readable text'}
            </Typography>
            {award && <CitationTag href={requirementHref(tender.id, award.document_id ?? null, award.page)} page={award.page} tooltip={award.quote} />}
          </Box>
        </Stack>
      </Panel>

      <OcrBanner tender={tender} />
      <DocumentsPanel tender={tender} />
      <FactsPanel tender={tender} />

      {tender.warnings.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}
          action={<Button color="inherit" size="small" onClick={() => setShowWarnings((v) => !v)}>{showWarnings ? 'Hide' : 'Show'}</Button>}>
          {tender.warnings.length} point{tender.warnings.length > 1 ? 's' : ''} need the committee's attention
          <Collapse in={showWarnings}>
            <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5 }}>
              {tender.warnings.map((w, i) => <li key={i}><Typography sx={{ fontSize: 12.5 }}>{w}</Typography></li>)}
            </Box>
          </Collapse>
        </Alert>
      )}

      <Panel sx={{ overflow: 'hidden' }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={0.5} sx={{ justifyContent: 'space-between', alignItems: { sm: 'center' }, px: 3, py: 2, borderBottom: `1px solid ${tokens.border}` }}>
          <Typography sx={{ fontSize: 15, fontWeight: 600, color: tokens.ink }}>
            Extracted Criteria <Box component="span" sx={{ color: tokens.muted, fontWeight: 500 }}>({tender.criteria.length})</Box>
          </Typography>
          <Typography sx={{ fontSize: 12.5, color: tokens.muted }}>
            {counts.located ?? 0} located & quoted · {counts.located_marathi ?? 0} cited in Marathi · {counts.derived ?? 0} derived from the PMC standard
          </Typography>
        </Stack>
        <DataGrid
          rows={tender.criteria.map((c, i) => ({ ...c, n: i + 1 }))}
          columns={columns}
          autoHeight
          getRowHeight={() => 'auto'}
          disableRowSelectionOnClick
          disableColumnMenu
          hideFooter
          sx={{
            border: 'none',
            '--DataGrid-rowBorderColor': tokens.rowBorder,
            '& .MuiDataGrid-columnHeader': { bgcolor: tokens.subtle },
            '& .MuiDataGrid-columnHeaderTitle': { fontSize: 11.5, fontWeight: 600, color: tokens.muted, textTransform: 'uppercase', letterSpacing: 0.3 },
            '& .MuiDataGrid-cell': { display: 'flex', alignItems: 'center', outline: 'none !important' },
            '& .MuiDataGrid-cell[data-field="n"], & .MuiDataGrid-columnHeader[data-field="n"]': { pl: 3 },
            '& .MuiDataGrid-row:hover': { bgcolor: tokens.subtle },
            '& .MuiDataGrid-columnSeparator': { display: 'none' },
          }}
        />
      </Panel>

      <Stack direction="row" sx={{ justifyContent: 'flex-end', mt: 2.5 }}>
        <Button variant="contained" disabled={!tender.criteria.length} onClick={() => navigate(`/tenders/${tender.id}/bidders`)}>
          Continue to Bidder Upload →
        </Button>
      </Stack>
    </Box>
  );
}
