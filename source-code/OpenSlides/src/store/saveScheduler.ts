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
  /** A deck was loaded from storage and opened unchanged: storage holds it, so an older unsaved copy of it is dropped. Finishes what is pending for other decks. */
  open(deck: D): void;
  /** The deck is being deleted: nothing of it is written from now on. Resolves once no write of it is under way. */
  forget(id: string): Promise<void>;
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
  // Ids opened or deleted while a write is under way: that write must not bring them back.
  const dropped = new Set<string>();
  // The object storage is known to hold, so putting it back (undo) is not written twice.
  // Only trusted while no write is under way: see change().
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
          if (!dropped.has(id)) saved = deck;
        } catch (cause) {
          if (dropped.has(id)) continue; // deleted or reloaded meanwhile: nothing left to save
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
      dropped.clear();
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

  // Stops tracking a deck id: storage, not this scheduler, now says what the deck is.
  const drop = (id: string) => {
    pending.delete(id);
    failed.delete(id);
    if (saved?.id === id) saved = null;
    if (running) dropped.add(id);
    else if (!pending.size) opts.onStatus('saved');
  };

  return {
    change(deck) {
      // During a write `saved` is one write behind: an undo or redo that puts the saved
      // object back while a newer one is being written must be written again.
      if (deck === saved && !pending.size && !running) return;
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
      drop(deck.id);
      saved = deck;
      if (pending.size) {
        drain = true;
        void start().catch(() => {});
      }
    },
    async forget(id) {
      drop(id);
      while (running) await running.catch(() => {});
    },
    async flush() {
      if (!pending.size && !running) return;
      drain = true;
      await start();
    },
  };
}
