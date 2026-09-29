import type { Evaluation, SampleFile, TenderDetail, TenderListItem } from './types';

const BASE = '/api/v1';

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError('NETWORK', 'Cannot reach the Tender Evaluator API. Is the backend running on port 8000?');
  }
  const body = await res.json().catch(() => null);
  if (!res.ok || !body?.success) {
    throw new ApiError(body?.error?.code ?? `HTTP_${res.status}`, body?.error?.message ?? res.statusText);
  }
  return body.data as T;
}

function upload<T>(path: string, files: File | File[], field = 'file'): Promise<T> {
  const form = new FormData();
  (Array.isArray(files) ? files : [files]).forEach((f) => form.append(field, f));
  return request<T>(path, { method: 'POST', body: form });
}

export const api = {
  listTenders: () => request<TenderListItem[]>('/tenders'),
  getTender: (id: string) => request<TenderDetail>(`/tenders/${id}`),
  /** A tender is a bundle: notice + conditions PDFs, BOQ .xls, or the zip/rar MahaTender provides. */
  createTender: (files: File[]) => upload<{ id: string }>('/tenders', files, 'files'),
  deleteTender: (id: string) => request<{ id: string }>(`/tenders/${id}`, { method: 'DELETE' }),
  addBidder: (tenderId: string, file: File) => upload<{ id: string }>(`/tenders/${tenderId}/bidders`, file),
  deleteBidder: (id: string) => request<{ id: string }>(`/bidders/${id}`, { method: 'DELETE' }),
  evaluate: (tenderId: string) => request<Evaluation>(`/tenders/${tenderId}/evaluate`, { method: 'POST' }),
  comparison: (tenderId: string) => request<Evaluation>(`/tenders/${tenderId}/comparison`),
  samples: () => request<SampleFile[]>('/samples'),
  async sampleFile(name: string): Promise<File> {
    const res = await fetch(`${BASE}/samples/${encodeURIComponent(name)}`);
    if (!res.ok) throw new ApiError('SAMPLE', `Could not load sample ${name}`);
    return new File([await res.blob()], name, { type: 'application/pdf' });
  },
};

/** Deep links used by every citation tag: the browser PDF viewer honours #page=N. */
export const pdfLinks = {
  tender: (tenderId: string, page: number) => `${BASE}/tenders/${tenderId}/document#page=${page}`,
  tenderDoc: (documentId: string, page?: number | null) =>
    `${BASE}/tender-documents/${documentId}/file${page ? `#page=${page}` : ''}`,
  bidderDoc: (documentId: string, page: number) => `${BASE}/documents/${documentId}/file#page=${page}`,
};
