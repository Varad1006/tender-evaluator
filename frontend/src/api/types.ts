import type { Verdict } from '../theme';

export interface Criterion {
  id: string;
  key: string;
  label: string;
  bucket: string;
  tier: 'deterministic' | 'llm_judge';
  datatype: string;
  operator: string;
  threshold_display: string;
  gating: boolean;
  source_page: number | null;
  source_quote: string | null;
  source_clause: string | null;
  source_section: string | null;
  extraction_method: 'deterministic' | 'llm' | 'pmc_standard' | 'checklist';
  source_document_id: string | null;
  source_document_name: string | null;
  status: 'located' | 'located_marathi' | 'derived';
  notes: string[];
  derivation: Record<string, unknown>;
}

export type PageKind = 'english' | 'marathi' | 'garbled' | 'image' | 'blank' | 'ocr_en' | 'ocr_mr';

export interface TenderDocumentInfo {
  id: string;
  file_name: string;
  origin: string;
  doc_type: string;
  page_count: number | null;
  page_kinds: PageKind[];
  summary: Partial<Record<PageKind, number>>;
  ocr_pages: number;
  error: string | null;
}

export interface FactSourceInfo {
  document_id: string;
  doc_name: string;
  page: number | null;
  quote: string;
  value: unknown;
  method: string;
}

export interface TenderFact {
  key: string;
  label: string;
  kind: 'money' | 'months' | 'text';
  value: unknown;
  display: string;
  status: 'consistent' | 'single_source' | 'conflict' | 'missing' | 'amended' | 'lossy';
  note: string | null;
  sources: FactSourceInfo[];
}

export interface PriceCitation {
  document_id: string;
  page: number;
  quote: string;
}

export interface BidderSummary {
  id: string;
  name: string;
  status: 'Registered' | 'Extracting' | 'ClaimsReady' | 'Failed';
  error: string | null;
  criteria_with_evidence: number;
  document: { id: string; file_name: string; page_count: number | null } | null;
  price: number | null;
  price_display: string | null;
  price_citation: PriceCitation | null;
  price_conflict: boolean;
}

export interface TenderDetail {
  id: string;
  title: string | null;
  ref_no: string | null;
  authority: string | null;
  estimated_value: number | null;
  estimated_value_display: string | null;
  issue_date: string | null;
  doc_name: string;
  page_count: number | null;
  award_mechanism: 'L1' | 'QCBS' | 'unknown' | null;
  award_mechanism_label: string | null;
  award_mechanism_citation: { page: number; quote: string; clause: string | null; document_id?: string | null } | null;
  status: string;
  extraction_status: 'pending' | 'extracting' | 'ready' | 'failed';
  extraction_error: string | null;
  created_at: string | null;
  criteria: Criterion[];
  bidders: BidderSummary[];
  evaluated: boolean;
  evaluation_stale: boolean;
  llm_enabled: boolean;
  documents: TenderDocumentInfo[];
  primary_document_id: string | null;
  facts: TenderFact[];
  warnings: string[];
  ocr_status: 'not_needed' | 'queued' | 'running' | 'done' | 'failed' | 'unavailable';
  ocr_progress: { done?: number; total?: number; cached?: number; errors?: string[] };
  timings: { local_seconds?: number; ocr_seconds?: number };
}

export interface TenderListItem {
  id: string;
  title: string | null;
  ref_no: string | null;
  authority: string | null;
  status: string;
  extraction_status: TenderDetail['extraction_status'];
  criteria_count: number;
  bidder_count: number;
  created_at: string | null;
}

export interface Evidence {
  document_id: string | null;
  document_name: string | null;
  page: number;
  quote: string;
  section: string;
  value: string;
  tier: 'deterministic' | 'llm';
}

export interface VerdictCell {
  id: string;
  criterion_id: string;
  bidder_id: string;
  verdict: Verdict;
  tier: 'deterministic' | 'ai_assisted' | 'rule_based';
  unknown_kind: 'not_found' | 'conflict' | 'low_confidence' | 'unparseable' | null;
  reason: string;
  computation: string | null;
  requirement: {
    page: number | null;
    quote: string | null;
    clause: string | null;
    section: string | null;
    document_id: string | null;
    status: Criterion['status'];
  };
  evidence: Evidence[];
}

export type Standing = 'eligible' | 'pending' | 'disqualified';

export interface Issue {
  criterion_id: string;
  criterion_label: string;
  verdict: Verdict;
  reason: string;
  requirement: VerdictCell['requirement'];
  evidence: Evidence[];
}

export interface EvaluatedBidder extends BidderSummary {
  overall: Standing;
  counts: Record<Verdict, number>;
  issues: Issue[];
}

export interface RankingEntry {
  rank_label: string;
  kind: 'qualified' | 'pending' | 'disqualified';
  is_top: boolean;
  bidder_id: string;
  name: string;
  price: number | null;
  price_display: string;
  price_citation: PriceCitation | null;
  basis: string;
  issues: Issue[];
}

export interface Evaluation {
  tender: Omit<TenderDetail, 'criteria' | 'bidders' | 'documents'>;
  criteria: Criterion[];
  bidders: EvaluatedBidder[];
  verdicts: VerdictCell[];
  evaluated: boolean;
  eligibility: Record<Standing, number>;
  ranking?: {
    mechanism: string;
    mechanism_label: string | null;
    warning: string | null;
    recommended_bidder_id: string | null;
    entries: RankingEntry[];
  };
}

export interface SampleFile {
  name: string;
  kind: 'tender' | 'bidder';
  size: number;
}
