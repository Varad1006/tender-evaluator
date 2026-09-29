import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { TenderDetail } from '../api/types';

interface TenderContextValue {
  tender: TenderDetail | null;
  error: string | null;
  refresh: () => Promise<TenderDetail | null>;
}

const TenderContext = createContext<TenderContextValue | null>(null);

const POLL_MS = 700;

export function isBusy(t: TenderDetail): boolean {
  if (t.extraction_status === 'pending' || t.extraction_status === 'extracting') return true;
  return t.bidders.some((b) => b.status === 'Registered' || b.status === 'Extracting');
}

/** Loads one tender and keeps polling while any extraction job (tender or bidder) is running. */
export function TenderProvider({ tenderId, children }: { tenderId: string; children: ReactNode }) {
  const [tender, setTender] = useState<TenderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const t = await api.getTender(tenderId);
      setTender(t);
      setError(null);
      return t;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [tenderId]);

  useEffect(() => {
    setTender(null);
    refresh();
  }, [refresh]);

  // Every refresh yields a new object, so this re-arms itself until background jobs finish.
  useEffect(() => {
    if (!tender || !isBusy(tender)) return;
    const id = window.setTimeout(refresh, POLL_MS);
    return () => window.clearTimeout(id);
  }, [tender, refresh]);

  return <TenderContext.Provider value={{ tender, error, refresh }}>{children}</TenderContext.Provider>;
}

export function useTender(): TenderContextValue {
  const ctx = useContext(TenderContext);
  if (!ctx) throw new Error('useTender must be used inside <TenderProvider>');
  return ctx;
}
