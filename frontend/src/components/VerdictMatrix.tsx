import { Box, Stack, Typography } from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import { useMemo, useState, type MouseEvent } from 'react';
import { pdfLinks } from '../api/client';
import type { Criterion, EvaluatedBidder, Evaluation, VerdictCell } from '../api/types';
import { standingStyle, tokens } from '../theme';
import { BucketChip } from './BucketChip';
import { requirementHref } from './BundlePanels';
import { CitationTag } from './CitationTag';
import { DualCitationPopover } from './DualCitationPopover';
import { VerdictChip } from './VerdictChip';

type Row = { id: string; criterion: Criterion | null };

interface Props {
  evaluation: Evaluation;
  /** Comparison mode: show each bidder's stated value under the verdict, plus a quoted-price row. */
  showValues?: boolean;
  /** Only rows where at least one bidder is not a pass. */
  issuesOnly?: boolean;
}

function cellValue(cell: VerdictCell): string {
  if (cell.unknown_kind === 'conflict') return cell.evidence.map((e) => `${e.value} (p.${e.page})`).join(' vs ');
  if (cell.unknown_kind === 'not_found' && !cell.evidence.length) return 'Not found';
  if (cell.unknown_kind === 'not_found') return 'Partly missing';
  return cell.evidence.map((e) => e.value).join(' · ');
}

const PRICE_ROW = '__price';

export function VerdictMatrix({ evaluation, showValues = false, issuesOnly = false }: Props) {
  const [open, setOpen] = useState<{ anchor: HTMLElement; cell: VerdictCell } | null>(null);
  const tenderId = evaluation.tender.id;

  const index = useMemo(() => {
    const m = new Map<string, VerdictCell>();
    evaluation.verdicts.forEach((v) => m.set(`${v.criterion_id}:${v.bidder_id}`, v));
    return m;
  }, [evaluation.verdicts]);

  const rows: Row[] = useMemo(() => {
    const criteria = evaluation.criteria.filter(
      (c) => !issuesOnly || evaluation.bidders.some((b) => index.get(`${c.id}:${b.id}`)?.verdict !== 'pass'),
    );
    const out: Row[] = criteria.map((c) => ({ id: c.id, criterion: c }));
    if (showValues) out.push({ id: PRICE_ROW, criterion: null });
    return out;
  }, [evaluation, index, issuesOnly, showValues]);

  const columns: GridColDef<Row>[] = useMemo(() => {
    const requirement: GridColDef<Row> = {
      field: 'requirement',
      headerName: 'Requirement',
      flex: 2.4,
      minWidth: 280,
      sortable: false,
      renderCell: ({ row }) =>
        row.criterion ? (
          <Stack spacing={0.75} sx={{ py: 1.5, pr: 1.5 }}>
            <Typography sx={{ fontSize: 13.5, fontWeight: 500, color: tokens.ink, lineHeight: 1.4, whiteSpace: 'normal' }}>
              {row.criterion.label}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <BucketChip bucket={row.criterion.bucket} />
              <Typography sx={{ fontSize: 12, color: tokens.muted }}>{row.criterion.threshold_display}</Typography>
              {row.criterion.source_page ? (
                <CitationTag href={requirementHref(tenderId, row.criterion.source_document_id, row.criterion.source_page)}
                  page={row.criterion.source_page}
                  tooltip={`Requirement source: ${row.criterion.source_document_name ?? 'tender'} page ${row.criterion.source_page}`} />
              ) : (
                <Typography sx={{ fontSize: 11, color: '#92600C' }}>derived</Typography>
              )}
            </Stack>
          </Stack>
        ) : (
          <Stack spacing={0.5} sx={{ py: 1.5 }}>
            <Typography sx={{ fontSize: 13.5, fontWeight: 600, color: tokens.ink }}>Quoted price (Form B)</Typography>
            <Typography sx={{ fontSize: 12, color: tokens.muted }}>Used for the ranking; not a pass/fail criterion</Typography>
          </Stack>
        ),
    };

    const bidderCol = (b: EvaluatedBidder): GridColDef<Row> => ({
      field: b.id,
      headerName: b.name,
      flex: 1,
      minWidth: 180,
      sortable: false,
      align: 'center',
      headerAlign: 'center',
      renderHeader: () => {
        const s = standingStyle[b.overall];
        return (
          <Stack spacing={0.5} sx={{ alignItems: 'center', py: 1, whiteSpace: 'normal' }}>
            <Typography sx={{ fontSize: 12.5, fontWeight: 600, color: tokens.ink, textAlign: 'center', lineHeight: 1.3 }}>{b.name}</Typography>
            <Box component="span" sx={{ fontSize: 10.5, fontWeight: 700, px: 0.75, py: '1px', borderRadius: 1, bgcolor: s.bg, color: s.fg, textTransform: 'uppercase', letterSpacing: 0.3 }}>
              {s.label}
            </Box>
          </Stack>
        );
      },
      renderCell: ({ row }) => {
        if (!row.criterion) {
          return b.price_citation && b.price_display ? (
            <Stack spacing={0.5} sx={{ alignItems: 'center', py: 1.5 }}>
              <Typography sx={{ fontSize: 14, fontWeight: 600, color: tokens.ink }}>{b.price_display}</Typography>
              <CitationTag href={pdfLinks.bidderDoc(b.price_citation.document_id, b.price_citation.page)} page={b.price_citation.page}
                tooltip={b.price_citation.quote} />
            </Stack>
          ) : (
            <Typography sx={{ fontSize: 12.5, color: tokens.faint }}>{b.price_conflict ? 'Conflicting prices' : 'Not found'}</Typography>
          );
        }
        const cell = index.get(`${row.criterion.id}:${b.id}`);
        if (!cell) return <Typography sx={{ fontSize: 12, color: tokens.faint }}>—</Typography>;
        return (
          <Stack spacing={0.5} sx={{ alignItems: 'center', py: 1.25, width: '100%' }}>
            <VerdictChip
              verdict={cell.verdict}
              selected={open?.cell.id === cell.id}
              onClick={(e: MouseEvent<HTMLElement>) => setOpen({ anchor: e.currentTarget, cell })}
            />
            {showValues && (
              <Typography sx={{ fontSize: 11.5, color: tokens.muted, textAlign: 'center', lineHeight: 1.35, whiteSpace: 'normal' }}>
                {cellValue(cell)}
              </Typography>
            )}
          </Stack>
        );
      },
    });

    return [requirement, ...evaluation.bidders.map(bidderCol)];
  }, [evaluation.bidders, index, open, showValues, tenderId]);

  const openCriterion = open ? evaluation.criteria.find((c) => c.id === open.cell.criterion_id) ?? null : null;
  const openBidder = open ? evaluation.bidders.find((b) => b.id === open.cell.bidder_id) ?? null : null;

  return (
    <>
      <DataGrid
        rows={rows}
        columns={columns}
        autoHeight
        getRowHeight={() => 'auto'}
        columnHeaderHeight={72}
        disableRowSelectionOnClick
        disableColumnMenu
        disableColumnResize
        hideFooter
        getRowClassName={({ id }) => (id === PRICE_ROW ? 'price-row' : '')}
        sx={{
          border: 'none',
          fontFamily: tokens.sans,
          '--DataGrid-rowBorderColor': tokens.rowBorder,
          '& .MuiDataGrid-columnHeaders, & .MuiDataGrid-columnHeader': { bgcolor: tokens.subtle },
          '& .MuiDataGrid-columnHeaderTitle': { fontSize: 11.5, fontWeight: 600, color: tokens.muted, textTransform: 'uppercase', letterSpacing: 0.3 },
          '& .MuiDataGrid-cell': { display: 'flex', alignItems: 'center', outline: 'none !important' },
          '& .MuiDataGrid-cell[data-field="requirement"]': { px: 2.5 },
          '& .MuiDataGrid-columnHeader[data-field="requirement"]': { px: 2.5 },
          '& .MuiDataGrid-row:hover': { bgcolor: tokens.subtle },
          '& .price-row': { bgcolor: tokens.quoteBg },
          '& .MuiDataGrid-columnSeparator': { display: 'none' },
        }}
      />
      <DualCitationPopover
        anchorEl={open?.anchor ?? null}
        onClose={() => setOpen(null)}
        cell={open?.cell ?? null}
        criterion={openCriterion}
        bidder={openBidder}
        tenderId={tenderId}
      />
    </>
  );
}
