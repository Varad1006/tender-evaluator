import { Link, Tooltip } from '@mui/material';
import { tokens } from '../theme';

interface Props {
  href: string;
  page: number;
  /** Optional prefix, e.g. "Tender" or a short document name. */
  source?: string;
  tooltip?: string;
}

/** A page citation that opens the exact page of the source PDF (FR11). */
export function CitationTag({ href, page, source, tooltip }: Props) {
  const tag = (
    <Link
      href={href}
      target="_blank"
      rel="noopener"
      underline="none"
      onClick={(e) => e.stopPropagation()}
      sx={{
        fontFamily: tokens.mono,
        fontSize: 11.5,
        color: tokens.green,
        bgcolor: tokens.greenTint,
        border: `1px solid ${tokens.greenBorder}`,
        borderRadius: '4px',
        px: 0.75,
        py: '1px',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.5,
        whiteSpace: 'nowrap',
        lineHeight: 1.6,
        '&:hover': { bgcolor: '#D9F5E3', color: tokens.greenDark },
      }}
    >
      {source ? `${source} · ` : ''}p.{page}
    </Link>
  );
  return (
    <Tooltip title={tooltip ?? `Open ${source ?? 'document'} at page ${page}`} arrow>
      {tag}
    </Tooltip>
  );
}
