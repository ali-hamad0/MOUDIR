import { useCallback, useEffect, useRef, useState } from "react";

interface PollingState<T> {
  data: T | null;
  loading: boolean; // true only on the FIRST load (shows the skeleton)
  error: boolean; // last fetch failed (and we have no data to show)
  refetch: () => void;
}

/**
 * Poll an async fetcher on an interval.
 *
 * Polling (not WebSockets) is the Phase 3 choice: the order feed needs orders to
 * appear within ~10s, and a 5s poll is simple, stateless, and survives the api
 * container restarting without --reload. WebSockets become worth it only at high
 * tenant/message volume where 5s polling across many open dashboards is wasteful
 * — defer until then (ROADMAP: "WebSockets later if needed").
 *
 * Background refreshes don't toggle `loading`, so the list doesn't flicker to a
 * skeleton every 5s; only the very first load does.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const firstLoad = useRef(true);

  const run = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(false);
    } catch {
      // Keep showing stale data on a transient failure; only surface the error
      // state when we have nothing to show.
      setError(true);
    } finally {
      if (firstLoad.current) {
        firstLoad.current = false;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    run();
    const id = setInterval(run, intervalMs);
    return () => clearInterval(id);
  }, [run, intervalMs]);

  return { data, loading, error, refetch: run };
}
