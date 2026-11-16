// Decides when the open deck is written to storage. It has no imports, so a
// test can drive it with a fake clock; storage.ts wires it to IndexedDB.
//
// A change never postpones a write that is already scheduled: the first
// unsaved change schedules one `delay` ms later, and never sooner than
// `minInterval` ms after the previous write started. Continuous editing is
// therefore saved about once per `minInterval`, not only when it pauses.

export type SaveState = 'saved' | 'saving' | 'error';

export interface SaveSchedulerOptions<D extends { id: string }> {
  write: (deck: D) => Promise<void>;
  onStatus: (status: SaveState) => void;
  delay?: number;
  minInterval?: number;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: any) => void;
}

export interface SaveScheduler<D> {
  /** The deck was edited: write this object soon. */
  change(deck: D): void;
  /** Another deck was opened unchanged: nothing to write for it, but finish what is pending. */
  open(deck: D): void;
  /** Writes everything pending now. Rejects if a write fails; the deck stays pending. */
  flush(): Promise<void>;
}

export function createSaveScheduler<D extends { id: string }>(opts: SaveSchedulerOptions<D>): SaveScheduler<D> {
  const delay = opts.delay ?? 500;
  const minInterval = opts.minInterval ?? 1000;
  const now = opts.now ?? (() => Date.now());
  const setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h));

  // Newest unsaved object per deck id, so a deck is always written under its own id.
  const pending = new Map<string, D>();
  // Decks whose last write failed and that were not edited since. They stay pending and are
  // retried with the next write, but they neither set a timer nor hold up another deck.
  const failed = new Set<string>();
  let saved: D | null = null;
  let timer: unknown = null;
  let running: Promise<void> | null = null;
  let drain = false; // keep writing until nothing is pending (flush), instead of one write per interval
  let lastStart = -Infinity;

  const cancelTimer = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };

  const retryable = () => [...pending.keys()].some((id) => !failed.has(id));

  const schedule = () => {
    if (timer !== null || running || !retryable()) return;
    // Capped at minInterval: now() is the system clock, which can be set back.
    const wait = Math.max(delay, Math.min(minInterval, lastStart + minInterval - now()));
    timer = setTimer(() => {
      timer = null;
      void start().catch(() => {});
    }, wait);
  };

  const attempt = (deck: D): Promise<void> => {
    try {
      return Promise.resolve(opts.write(deck));
    } catch (err) {
      return Promise.reject(err);
    }
  };

  // One write at a time. A change that arrives during a write is written by the next one.
  // A run writes every pending deck once (all of them until nothing is left when draining);
  // a deck whose write fails does not stop the others.
  const run = async () => {
    const attempted = new Set<string>();
    const failedNow = new Set<string>();
    let error: { cause: unknown } | null = null;
    const next = () => {
      const ids = [...pending.keys()].filter((id) => !failedNow.has(id) && (drain || !attempted.has(id)));
      return ids.find((id) => !failed.has(id)) ?? ids[0]; // decks that failed before go last
    };
    try {
      for (let id = next(); id !== undefined; id = next()) {
        const deck = pending.get(id) as D;
        pending.delete(id);
        attempted.add(id);
        lastStart = now();
        try {
          await attempt(deck);
          failed.delete(id);
          saved = deck;
        } catch (cause) {
          failedNow.add(id);
          error ??= { cause };
          // Still unsaved: the next change or flush retries. A newer edit is retried on the timer.
          if (!pending.has(id)) {
            pending.set(id, deck);
            failed.add(id);
          }
        }
      }
    } finally {
      running = null;
      drain = false;
    }
    if (error) opts.onStatus('error');
    else if (!pending.size) opts.onStatus('saved');
    schedule();
    if (error) throw error.cause;
  };

  const start = (): Promise<void> => {
    cancelTimer();
    if (!running && pending.size) running = run();
    return running ?? Promise.resolve();
  };

  return {
    change(deck) {
      if (deck === saved && !pending.size) return;
      const other = [...pending.keys()].some((id) => id !== deck.id && !failed.has(id));
      pending.set(deck.id, deck);
      failed.delete(deck.id);
      opts.onStatus('saving');
      if (other) {
        // The previous deck still has unsaved edits: write it now, under its own id.
        drain = true;
        void start().catch(() => {});
      } else schedule();
    },
    open(deck) {
      if (!pending.has(deck.id)) saved = deck;
      if (pending.size) {
        drain = true;
        void start().catch(() => {});
      }
    },
    async flush() {
      if (!pending.size && !running) return;
      drain = true;
      await start();
    },
  };
}
