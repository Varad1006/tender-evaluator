import AddRoundedIcon from '@mui/icons-material/AddRounded';
import AutoFixHighRoundedIcon from '@mui/icons-material/AutoFixHighRounded';
import CheckRoundedIcon from '@mui/icons-material/CheckRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import UploadFileRoundedIcon from '@mui/icons-material/UploadFileRounded';
import {
  Alert, Box, Button, ButtonBase, Chip, CircularProgress, IconButton, LinearProgress, Link, Stack, Tooltip, Typography,
} from '@mui/material';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, pdfLinks } from '../api/client';
import type { BidderDocumentInfo, BidderSummary } from '../api/types';
import { KindStrip } from '../components/BundlePanels';
import { CitationTag } from '../components/CitationTag';
import { FilePickerButton, PageTitle, Panel } from '../components/common';
import { isBusy, useTender } from '../hooks/useTender';
import { tokens } from '../theme';

const SAMPLE_BIDS = [
  'sunrise_energy_technical_financial_bid.pdf',
  'greenvolt_infra_combined_bid.pdf',
  'urja_solar_tech_combined_bid.pdf',
];
const SOFT_LIMIT = 20;
const BID_FILES = 'application/pdf,.pdf,application/zip,.zip,.rar,application/x-rar-compressed';

function SlotHeader({ n, done, onRemove, bidRef }: { n: number; done?: boolean; onRemove?: () => void; bidRef?: string | null }) {
  return (
    <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', minHeight: 28 }}>
      <Typography variant="overline" sx={{ color: tokens.muted }}>
        Bidder {n}{bidRef ? ` · bid ${bidRef}` : ''}
      </Typography>
      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
        {done && <CheckRoundedIcon sx={{ color: tokens.green, fontSize: 18 }} />}
        {onRemove && (
          <Tooltip title="Remove this bidder">
            <IconButton size="small" onClick={onRemove}><DeleteOutlineRoundedIcon fontSize="small" /></IconButton>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}

function Reading({ label }: { label: string }) {
  return (
    <Stack direction="row" spacing={1.25} sx={{ alignItems: 'center', py: 1.75 }}>
      <CircularProgress size={16} thickness={5} />
      <Typography sx={{ fontSize: 13, color: tokens.muted }}>{label}</Typography>
    </Stack>
  );
}

/** What the bid contains, by kind, in the order it first appears: "Income-tax return · p.14, 16, 20". */
function Contents({ doc }: { doc: BidderDocumentInfo }) {
  if (!doc.contents.length) return null;
  const groups: { label: string; ranges: string[]; first: number }[] = [];
  for (const c of doc.contents) {
    const r = c.to > c.from ? `${c.from}–${c.to}` : `${c.from}`;
    const g = groups.find((x) => x.label === c.label);
    if (g) g.ranges.push(r);
    else groups.push({ label: c.label, ranges: [r], first: c.from });
  }
  return (
    <Stack direction="row" useFlexGap sx={{ flexWrap: 'wrap', gap: 0.5, mt: 0.75 }}>
      {groups.map((g) => (
        <Tooltip key={g.label} title={`p. ${g.ranges.join(', ')}`}>
          <Chip size="small" component="a" clickable href={pdfLinks.bidderDoc(doc.id, g.first)} target="_blank" rel="noopener"
            label={`${g.label} · p.${g.ranges.slice(0, 3).join(', ')}${g.ranges.length > 3 ? ` +${g.ranges.length - 3}` : ''}`}
            sx={{ height: 20, fontSize: 10.5, bgcolor: tokens.hover, color: tokens.inkSoft }} />
        </Tooltip>
      ))}
    </Stack>
  );
}

function BidderCard({ n, bidder, criteriaCount, onRemove }: { n: number; bidder: BidderSummary; criteriaCount: number; onRemove: () => void }) {
  const reading = bidder.status === 'Registered' || bidder.status === 'Extracting';
  const p = bidder.ocr_progress || {};
  const ocrRunning = reading && bidder.ocr_status === 'running' && (p.total ?? 0) > 0;
  const pages = bidder.documents.reduce((a, d) => a + (d.page_count ?? 0), 0);
  const firm = bidder.identity?.firm_name;
  return (
    <Panel sx={{ p: 2.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
      <SlotHeader n={n} done={bidder.status === 'ClaimsReady'} onRemove={reading ? undefined : onRemove} bidRef={bidder.bid_ref} />
      {reading && !ocrRunning && <Reading label={`Reading ${bidder.documents.length ? `${bidder.documents.length} document(s)` : 'the upload'}…`} />}
      {ocrRunning && (
        <Box sx={{ py: 1 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.75 }}>
            <AutoFixHighRoundedIcon sx={{ fontSize: 16, color: '#4338CA' }} />
            <Typography sx={{ fontSize: 13, color: tokens.inkSoft }}>
              Reading {p.total} scanned / Marathi pages — {p.done ?? 0} of {p.total}
            </Typography>
          </Stack>
          <LinearProgress variant="determinate" value={p.total ? Math.round(((p.done ?? 0) / p.total) * 100) : 0}
            sx={{ height: 5, borderRadius: 3 }} />
          {firm && <Typography sx={{ fontSize: 12.5, color: tokens.muted, mt: 1 }}>{firm.value}</Typography>}
        </Box>
      )}
      {bidder.status === 'Failed' && <Alert severity="error" sx={{ fontSize: 13 }}>{bidder.error}</Alert>}
      {bidder.status === 'ClaimsReady' && (
        <Box>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline', flexWrap: 'wrap' }}>
            <Typography sx={{ fontSize: 14.5, fontWeight: 600, color: tokens.ink }}>{bidder.name}</Typography>
            {bidder.identity?.constitution && (
              <Typography sx={{ fontSize: 12, color: tokens.muted }}>{bidder.identity.constitution.value}</Typography>
            )}
            {firm?.page && firm.document_id && (
              <CitationTag href={pdfLinks.bidderDoc(firm.document_id, firm.page)} page={firm.page} tooltip="Where the firm is named" />
            )}
          </Stack>
          {bidder.identity?.bid_date && (
            <Typography sx={{ fontSize: 12, color: tokens.muted }}>
              Bid made {new Date(bidder.identity.bid_date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}
              {' '}(EMD payment receipt)
            </Typography>
          )}
          {bidder.price_citation && (
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 1 }}>
              <Typography sx={{ fontSize: 12.5, color: tokens.muted }}>
                Quoted price: <Box component="span" sx={{ color: tokens.ink, fontWeight: 600 }}>{bidder.price_display ?? (bidder.price_conflict ? 'conflicting figures' : 'not found')}</Box>
              </Typography>
              <CitationTag href={pdfLinks.bidderDoc(bidder.price_citation.document_id, bidder.price_citation.page)}
                page={bidder.price_citation.page} tooltip={bidder.price_citation.quote} />
            </Stack>
          )}
          <Stack spacing={1} sx={{ mt: 1.25 }}>
            {bidder.documents.map((d) => (
              <Box key={d.id}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.5 }}>
                  <Tooltip title={d.origin ?? d.file_name}>
                    <Link href={pdfLinks.bidderDoc(d.id, 1)} target="_blank" rel="noopener" underline="hover"
                      sx={{ fontFamily: tokens.mono, fontSize: 11.5, color: tokens.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 190 }}>
                      {d.file_name}
                    </Link>
                  </Tooltip>
                  <Typography sx={{ fontSize: 11, color: tokens.faint }}>
                    {d.page_count} pp{d.ocr_pages ? ` · ${d.ocr_pages} OCR` : ''}
                  </Typography>
                </Stack>
                {d.page_kinds.length > 0 && <KindStrip kinds={d.page_kinds} />}
                <Contents doc={d} />
              </Box>
            ))}
          </Stack>
          <Typography sx={{ fontSize: 12, color: tokens.faint, mt: 1 }}>
            Evidence located for {bidder.criteria_with_evidence} of {criteriaCount} criteria · {pages} pages
          </Typography>
          {bidder.warnings.length > 0 && (
            <Tooltip title={<Box sx={{ whiteSpace: 'pre-line' }}>{bidder.warnings.join('\n')}</Box>}>
              <Typography sx={{ fontSize: 11.5, color: '#92600C', mt: 0.5, cursor: 'help' }}>
                {bidder.warnings.length} note{bidder.warnings.length > 1 ? 's' : ''} on reading this bid
              </Typography>
            </Tooltip>
          )}
        </Box>
      )}
    </Panel>
  );
}

export function BiddersPage() {
  const { tender, refresh } = useTender();
  const navigate = useNavigate();
  const [extraSlots, setExtraSlots] = useState<number | null>(null);
  const [uploading, setUploading] = useState<Record<number, string>>({});
  const [bulk, setBulk] = useState<string | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!tender) return null;
  const bidders = tender.bidders;
  const empty = extraSlots ?? Math.max(0, 3 - bidders.length);

  const uploadTo = async (slot: number, files: File[]) => {
    setUploading((u) => ({ ...u, [slot]: files.length === 1 ? files[0].name : `${files.length} files` }));
    setError(null);
    try {
      await api.addBidder(tender.id, files);
      setExtraSlots(Math.max(0, empty - 1));
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading((u) => {
        const next = { ...u };
        delete next[slot];
        return next;
      });
    }
  };

  /** Several portal envelopes at once: each zip / rar is one bidder; loose PDFs go together as one. */
  const uploadEnvelopes = async (files: File[]) => {
    setError(null);
    const archives = files.filter((f) => /\.(zip|rar)$/i.test(f.name));
    const loose = files.filter((f) => !/\.(zip|rar)$/i.test(f.name));
    const groups = [...archives.map((f) => [f]), ...(loose.length ? [loose] : [])];
    try {
      for (let i = 0; i < groups.length; i++) {
        setBulk(`Uploading ${i + 1} of ${groups.length}…`);
        await api.addBidder(tender.id, groups[i]);
      }
      setExtraSlots(0);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBulk(null);
    }
  };

  const loadSamples = async () => {
    setError(null);
    try {
      for (const name of SAMPLE_BIDS) {
        await api.addBidder(tender.id, await api.sampleFile(name));
      }
      setExtraSlots(0);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (b: BidderSummary) => {
    await api.deleteBidder(b.id).catch((e: Error) => setError(e.message));
    setExtraSlots(empty + 1);
    refresh();
  };

  const run = async () => {
    setEvaluating(true);
    setError(null);
    try {
      await api.evaluate(tender.id);
      await refresh();
      navigate(`/tenders/${tender.id}/verdicts`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setEvaluating(false);
    }
  };

  const ready = bidders.filter((b) => b.status === 'ClaimsReady').length;
  const busy = isBusy(tender) || Object.keys(uploading).length > 0 || bulk !== null;
  const canRun = ready > 0 && !busy && !evaluating;
  const upToDate = tender.evaluated && !tender.evaluation_stale;
  const reading = bidders.filter((b) => b.status === 'Registered' || b.status === 'Extracting').length;

  return (
    <Box sx={{ animation: 'te-fade-in 0.3s ease' }}>
      <PageTitle
        title="Upload Bidder Submissions"
        subtitle={`One bid per bidder: the portal's envelope (Fee_PreQual_Technical_<bid id>.zip) or its PDFs. Scanned and Marathi pages are read by OCR. Each bid is checked against all ${tender.criteria.length} extracted criteria.`}
        actions={
          <Stack direction="row" spacing={1}>
            {bidders.length === 0 && (
              <Button size="small" onClick={loadSamples} disabled={busy} sx={{ color: tokens.muted, fontWeight: 500 }}>
                Load the 3 seeded sample bids
              </Button>
            )}
            <FilePickerButton size="small" variant="outlined" multiple accept={BID_FILES} onFiles={uploadEnvelopes}
              disabled={bulk !== null} startIcon={<UploadFileRoundedIcon fontSize="small" />}>
              Upload envelopes
            </FilePickerButton>
          </Stack>
        }
      />
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {bulk && <Alert severity="info" sx={{ mb: 2 }}>{bulk}</Alert>}
      {reading > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Reading {reading} bid{reading > 1 ? 's' : ''}. Scanned bids take a few minutes each (OCR); evaluation opens when every bid is read.
        </Alert>
      )}
      {bidders.length >= SOFT_LIMIT && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {bidders.length} bidders — above the {SOFT_LIMIT}-bidder soft limit. Evaluation still runs; expect longer extraction times.
        </Alert>
      )}

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 2, mb: 3 }}>
        {bidders.map((b, i) => (
          <BidderCard key={b.id} n={i + 1} bidder={b} criteriaCount={tender.criteria.length} onRemove={() => remove(b)} />
        ))}
        {Array.from({ length: empty }, (_, i) => (
          <Panel key={`empty-${i}`} sx={{ p: 2.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
            <SlotHeader n={bidders.length + i + 1} />
            {uploading[i] ? (
              <Reading label={`Uploading ${uploading[i]}…`} />
            ) : (
              <Box sx={{ py: 1 }}>
                <Typography sx={{ fontSize: 13, color: tokens.faint, mb: 1.25 }}>
                  The envelope zip, or all of this bidder's PDFs
                </Typography>
                <FilePickerButton fullWidth multiple accept={BID_FILES} onFiles={(f) => uploadTo(i, f)}
                  sx={{ bgcolor: tokens.hover, color: tokens.ink, border: `1px dashed #C6CBCF`, '&:hover': { bgcolor: '#E8EBEC' } }}>
                  Choose files &amp; upload
                </FilePickerButton>
              </Box>
            )}
          </Panel>
        ))}
        <ButtonBase onClick={() => setExtraSlots(empty + 1)}
          sx={{ minHeight: 150, border: `1px dashed ${tokens.border}`, borderRadius: '10px', color: tokens.muted, gap: 1, '&:hover': { bgcolor: tokens.surface, borderColor: tokens.faint } }}>
          <AddRoundedIcon fontSize="small" />
          <Typography sx={{ fontSize: 13.5, fontWeight: 600 }}>Add another bidder</Typography>
        </ButtonBase>
      </Box>

      <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center', justifyContent: 'flex-end' }}>
        {upToDate && (
          <Button onClick={run} disabled={!canRun} sx={{ color: tokens.muted }}>Re-run evaluation</Button>
        )}
        <Button
          variant="contained"
          disabled={!upToDate && !canRun}
          onClick={upToDate ? () => navigate(`/tenders/${tender.id}/verdicts`) : run}
          startIcon={evaluating ? <CircularProgress size={14} color="inherit" /> : undefined}
        >
          {evaluating ? 'Evaluating bidders…' : upToDate ? 'View Verdicts →' : tender.evaluated ? 'Re-run Evaluation →' : 'Run Evaluation →'}
        </Button>
      </Stack>
    </Box>
  );
}
