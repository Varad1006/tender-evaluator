import AddRoundedIcon from '@mui/icons-material/AddRounded';
import CheckRoundedIcon from '@mui/icons-material/CheckRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import { Alert, Box, Button, ButtonBase, CircularProgress, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, pdfLinks } from '../api/client';
import type { BidderSummary } from '../api/types';
import { CitationTag } from '../components/CitationTag';
import { PageTitle, Panel, PdfPickerButton } from '../components/common';
import { isBusy, useTender } from '../hooks/useTender';
import { tokens } from '../theme';

const SAMPLE_BIDS = [
  'sunrise_energy_technical_financial_bid.pdf',
  'greenvolt_infra_combined_bid.pdf',
  'urja_solar_tech_combined_bid.pdf',
];
const SOFT_LIMIT = 20;

function SlotHeader({ n, done, onRemove }: { n: number; done?: boolean; onRemove?: () => void }) {
  return (
    <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', minHeight: 28 }}>
      <Typography variant="overline" sx={{ color: tokens.muted }}>Bidder slot {n}</Typography>
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

function BidderCard({ n, bidder, criteriaCount, onRemove }: { n: number; bidder: BidderSummary; criteriaCount: number; onRemove: () => void }) {
  const reading = bidder.status === 'Registered' || bidder.status === 'Extracting';
  return (
    <Panel sx={{ p: 2.5, display: 'flex', flexDirection: 'column', gap: 1 }}>
      <SlotHeader n={n} done={bidder.status === 'ClaimsReady'} onRemove={reading ? undefined : onRemove} />
      {reading && <Reading label={`Reading ${bidder.document?.file_name ?? 'document'}…`} />}
      {bidder.status === 'Failed' && <Alert severity="error" sx={{ fontSize: 13 }}>{bidder.error}</Alert>}
      {bidder.status === 'ClaimsReady' && (
        <Box>
          <Typography sx={{ fontSize: 14.5, fontWeight: 600, color: tokens.ink, mb: 0.25 }}>{bidder.name}</Typography>
          <Typography sx={{ fontSize: 12, color: tokens.muted, fontFamily: tokens.mono, wordBreak: 'break-all' }}>
            {bidder.document?.file_name}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 1.25 }}>
            <Typography sx={{ fontSize: 12.5, color: tokens.muted }}>
              Quoted price: <Box component="span" sx={{ color: tokens.ink, fontWeight: 600 }}>{bidder.price_display ?? (bidder.price_conflict ? 'conflicting figures' : 'not found')}</Box>
            </Typography>
            {bidder.price_citation && (
              <CitationTag href={pdfLinks.bidderDoc(bidder.price_citation.document_id, bidder.price_citation.page)}
                page={bidder.price_citation.page} tooltip={bidder.price_citation.quote} />
            )}
          </Stack>
          <Typography sx={{ fontSize: 12, color: tokens.faint, mt: 0.75 }}>
            Evidence located for {bidder.criteria_with_evidence} of {criteriaCount} criteria · {bidder.document?.page_count} pages
          </Typography>
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
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!tender) return null;
  const bidders = tender.bidders;
  const empty = extraSlots ?? Math.max(0, 3 - bidders.length);

  const uploadTo = async (slot: number, file: File) => {
    setUploading((u) => ({ ...u, [slot]: file.name }));
    setError(null);
    try {
      await api.addBidder(tender.id, file);
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
  const busy = isBusy(tender) || Object.keys(uploading).length > 0;
  const canRun = ready > 0 && !busy && !evaluating;
  const upToDate = tender.evaluated && !tender.evaluation_stale;

  return (
    <Box sx={{ animation: 'te-fade-in 0.3s ease' }}>
      <PageTitle
        title="Upload Bidder Submissions"
        subtitle={`One PDF per bidder. Each is evaluated against all ${tender.criteria.length} extracted criteria.`}
        actions={bidders.length === 0 && (
          <Button size="small" onClick={loadSamples} disabled={busy} sx={{ color: tokens.muted, fontWeight: 500 }}>
            Load the 3 seeded sample bids
          </Button>
        )}
      />
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {bidders.length >= SOFT_LIMIT && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {bidders.length} bidders — above the {SOFT_LIMIT}-bidder soft limit. Evaluation still runs; expect longer extraction times.
        </Alert>
      )}

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 2, mb: 3 }}>
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
                <Typography sx={{ fontSize: 13, color: tokens.faint, mb: 1.25 }}>No document selected</Typography>
                <PdfPickerButton fullWidth onFile={(f) => uploadTo(i, f)}
                  sx={{ bgcolor: tokens.hover, color: tokens.ink, border: `1px dashed #C6CBCF`, '&:hover': { bgcolor: '#E8EBEC' } }}>
                  Choose File &amp; Upload
                </PdfPickerButton>
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
