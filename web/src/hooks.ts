import { useState, useEffect, useRef, useCallback } from "react";
import type { BotApi, StatusSnapshot, BotEvent } from "./api";

export function useStatus(api: BotApi, intervalMs = 1000) {
  const [status, setStatus] = useState<StatusSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const s = await api.status();
        if (active) {
          setStatus(s);
          setError(null);
        }
      } catch (e: unknown) {
        if (active) setError(e instanceof Error ? e.message : String(e));
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [api, intervalMs]);

  return { status, error };
}

export function useEvents(api: BotApi, intervalMs = 2000) {
  const [events, setEvents] = useState<BotEvent[]>([]);
  const lastIdRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const newEvents = await api.events(lastIdRef.current);
        if (!active || newEvents.length === 0) return;
        lastIdRef.current = newEvents[newEvents.length - 1].id;
        setEvents((prev) => {
          const merged = [...prev, ...newEvents];
          // Dedupe by event id to avoid visual duplication on cursor races/replays.
          const seen = new Set<string>();
          const deduped: BotEvent[] = [];
          for (let i = merged.length - 1; i >= 0; i--) {
            const ev = merged[i];
            if (seen.has(ev.id)) continue;
            seen.add(ev.id);
            deduped.push(ev);
          }
          deduped.reverse();
          return deduped.slice(-500);
        });
      } catch {
        // swallow — will retry next poll
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [api, intervalMs]);

  const clearEvents = useCallback(() => {
    setEvents((prev) => {
      // "Clear" means clear from now: keep cursor at latest seen event.
      const lastSeen = prev.length > 0 ? prev[prev.length - 1].id : lastIdRef.current;
      lastIdRef.current = lastSeen;
      return [];
    });
  }, []);

  return { events, clearEvents };
}
