import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Evaluation } from '../api/types';

/** Comparison + ranking are computed live from the verdict table on every load (Build Brief). */
export function useEvaluation(tenderId: string, version: unknown) {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setError(null);
    api
      .comparison(tenderId)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [tenderId, version]);
  return { data, error };
}
