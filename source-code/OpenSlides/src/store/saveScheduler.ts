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
  let saved: D | null = null;
  let timer: unknown = null;
  let running: Promise<void> | null = null;
  let drain = false; // keep writing until nothing is pending (flush), instead of one write per interval
  let lastStart = -Infinity;

  const cancelTimer = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };

  const schedule = () => {
    if (timer !== null || running || !pending.size) return;
    const wait = Math.max(delay, lastStart + minInterval - now());
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
  const run = async () => {
    try {
      do {
        const [id, deck] = pending.entries().next().value as [string, D];
        pending.delete(id);
        lastStart = now();
        try {
          await attempt(deck);
        } catch (err) {
          if (!pending.has(id)) pending.set(id, deck); // still unsaved: the next change or flush retries
          opts.onStatus('error');
          throw err;
        }
        saved = deck;
      } while (drain && pending.size);
    } finally {
      running = null;
      drain = false;
    }
    if (pending.size) schedule();
    else opts.onStatus('saved');
  };

  const start = (): Promise<void> => {
    cancelTimer();
    if (!running && pending.size) running = run();
    return running ?? Promise.resolve();
  };

  return {
    change(deck) {
      if (deck === saved && !pending.size) return;
      const other = [...pending.keys()].some((id) => id !== deck.id);
      pending.set(deck.id, deck);
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
