import AddRoundedIcon from '@mui/icons-material/AddRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined';
import { Alert, Box, Button, Chip, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { TenderListItem } from '../api/types';
import { AppShell } from '../components/AppShell';
import { Busy, FilePickerButton, PageTitle, Panel } from '../components/common';
import { tokens } from '../theme';

const SAMPLE_TENDER = 'PMC_SolarRooftop_Tender_2026.pdf';

export function TenderListPage() {
  const navigate = useNavigate();
  const [tenders, setTenders] = useState<TenderListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);

  const load = () => api.listTenders().then(setTenders).catch((e: Error) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const upload = async (files: File[]) => {
    setUploading(files.length === 1 ? files[0].name : `${files.length} files`);
    setError(null);
    try {
      const { id } = await api.createTender(files);
      navigate(`/tenders/${id}/criteria`);
    } catch (e) {
      setError((e as Error).message);
      setUploading(null);
    }
  };

  const loadSample = async () => {
    try {
      await upload([await api.sampleFile(SAMPLE_TENDER)]);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (t: TenderListItem) => {
    if (!window.confirm(`Delete "${t.title ?? t.ref_no}" and all its bidder documents?`)) return;
    await api.deleteTender(t.id).catch((e: Error) => setError(e.message));
    load();
  };

  const uploadButton = (
    <FilePickerButton variant="contained" startIcon={<AddRoundedIcon />} onFiles={upload} disabled={Boolean(uploading)} multiple
      accept="application/pdf,.pdf,.zip,.rar,.xls,.xlsx">
      Upload Tender
    </FilePickerButton>
  );

  return (
    <AppShell tender={null}>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {uploading ? (
        <Busy title={`Uploading ${uploading}`} detail="Extraction starts as soon as the file is stored…" />
      ) : tenders === null ? null : tenders.length === 0 ? (
        <Panel sx={{ py: 12, px: 2.5, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
          <Box sx={{ width: 56, height: 56, borderRadius: 3, bgcolor: tokens.hover, display: 'grid', placeItems: 'center', mb: 2.5 }}>
            <DescriptionOutlinedIcon sx={{ color: tokens.muted, fontSize: 28 }} />
          </Box>
          <Typography sx={{ fontSize: 19, fontWeight: 600, color: tokens.ink, mb: 0.75 }}>No tenders evaluated yet</Typography>
          <Typography sx={{ fontSize: 14, color: tokens.muted, maxWidth: 400, mb: 3.25, lineHeight: 1.5 }}>
            Upload the tender as downloaded from MahaTender — the zip, or the notice, conditions PDFs and BOQ together.
            Requirements are extracted with page citations; scanned and Marathi pages are read by OCR.
          </Typography>
          {uploadButton}
          <Button size="small" onClick={loadSample} sx={{ mt: 1.5, color: tokens.muted, fontWeight: 500 }}>
            or use the seeded sample tender
          </Button>
        </Panel>
      ) : (
        <>
          <PageTitle title="Tenders" subtitle="Each tender is evaluated once; bidders are added against its extracted criteria." actions={uploadButton} />
          <Stack spacing={1.25}>
            {tenders.map((t) => (
              <Panel key={t.id} sx={{ '&:hover': { borderColor: tokens.faint } }}>
                <Stack direction="row" spacing={2} sx={{ alignItems: 'center', p: 2.5, cursor: 'pointer' }} onClick={() => navigate(`/tenders/${t.id}/criteria`)}>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography sx={{ fontSize: 15, fontWeight: 600, color: tokens.ink }} noWrap>{t.title ?? 'Untitled tender'}</Typography>
                    <Typography sx={{ fontSize: 13, color: tokens.muted }}>
                      {[t.ref_no, t.authority, `${t.criteria_count} criteria`, `${t.bidder_count} bidders`].filter(Boolean).join(' · ')}
                    </Typography>
                  </Box>
                  <Chip size="small" label={t.extraction_status === 'failed' ? 'Extraction failed' : t.status} variant="outlined" />
                  <Tooltip title="Delete tender">
                    <IconButton size="small" onClick={(e) => { e.stopPropagation(); remove(t); }}>
                      <DeleteOutlineRoundedIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
              </Panel>
            ))}
          </Stack>
        </>
      )}
    </AppShell>
  );
}
