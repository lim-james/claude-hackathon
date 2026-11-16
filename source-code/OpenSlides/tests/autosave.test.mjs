import test from 'node:test';
import assert from 'node:assert/strict';
import { createSaveScheduler } from '../src/store/saveScheduler.ts';

// Lets promise callbacks run; setImmediate is not one of the mocked timers.
const settle = () => new Promise((resolve) => setImmediate(resolve));

function setup(t, write) {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const writes = [];
  const statuses = [];
  const scheduler = createSaveScheduler({
    write: write ?? (async (deck) => void writes.push({ deck, at: Date.now() })),
    onStatus: (s) => statuses.push(s),
  });
  const advance = async (ms) => {
    for (let i = 0; i < ms / 50; i++) {
      t.mock.timers.tick(50);
      await settle();
    }
  };
  return { scheduler, writes, statuses, advance };
}

test('continuous editing is saved about once a second, not only when it pauses', async (t) => {
  const { scheduler, writes, advance } = setup(t);
  const changedAt = [];
  let oldest = 0;
  for (let n = 0; n < 80; n++) {
    changedAt.push(Date.now());
    scheduler.change({ id: 'a', n });
    await advance(200);
    const storedN = writes.length ? writes.at(-1).deck.n : -1;
    if (storedN < n) oldest = Math.max(oldest, Date.now() - changedAt[storedN + 1]);
  }
  assert.ok(writes.length >= 14 && writes.length <= 16, `${writes.length} writes in 16 s`);
  assert.ok(oldest <= 2000, `oldest unsaved edit was ${oldest} ms old`);
  for (let i = 1; i < writes.length; i++) assert.ok(writes[i].at - writes[i - 1].at >= 1000);
});

test('a single edit is written within 1,100 ms, also right after another write', async (t) => {
  const { scheduler, writes, statuses, advance } = setup(t);
  scheduler.change({ id: 'a', n: 1 });
  await advance(500);
  assert.deepEqual(writes.map((w) => w.deck.n), [1]);
  assert.equal(statuses.at(-1), 'saved');
  scheduler.change({ id: 'a', n: 2 });
  assert.equal(statuses.at(-1), 'saving');
  await advance(1100);
  assert.deepEqual(writes.map((w) => w.deck.n), [1, 2]);
  assert.equal(statuses.at(-1), 'saved');
});

test('changes during an outstanding write get exactly one follow-up write of the newest deck', async (t) => {
  const release = [];
  const written = [];
  const { scheduler, statuses, advance } = setup(t, (deck) => new Promise((resolve) => {
    written.push(deck.n);
    release.push(resolve);
  }));
  scheduler.change({ id: 'a', n: 1 });
  await advance(500);
  assert.deepEqual(written, [1]);
  scheduler.change({ id: 'a', n: 2 });
  scheduler.change({ id: 'a', n: 3 });
  await advance(3000);
  assert.deepEqual(written, [1], 'one write at a time');
  release[0]();
  await settle();
  assert.equal(statuses.at(-1), 'saving', 'not "saved" while a newer edit is unwritten');
  await advance(3000);
  assert.deepEqual(written, [1, 3]);
  release[1]();
  await settle();
  assert.equal(statuses.at(-1), 'saved');
});

test('a failed write reports an error, keeps the deck pending, and the next edit retries', async (t) => {
  let fail = true;
  const written = [];
  const { scheduler, statuses, advance } = setup(t, async (deck) => {
    if (fail) throw new Error('storage full');
    written.push(deck.n);
  });
  scheduler.change({ id: 'a', n: 1 });
  await advance(500);
  assert.equal(statuses.at(-1), 'error');
  await assert.rejects(scheduler.flush(), /storage full/);
  assert.equal(statuses.at(-1), 'error');
  fail = false;
  scheduler.change({ id: 'a', n: 2 });
  await advance(1100);
  assert.deepEqual(written, [2]);
  assert.equal(statuses.at(-1), 'saved');
});

test('switching decks writes each deck under its own id', async (t) => {
  const { scheduler, writes, advance } = setup(t);
  scheduler.change({ id: 'a', n: 1 });
  await advance(100);
  scheduler.open({ id: 'b', n: 0 }); // B opened unchanged: A's edit is written, B is not
  await settle();
  assert.deepEqual(writes.map((w) => [w.deck.id, w.deck.n]), [['a', 1]]);
  scheduler.change({ id: 'b', n: 1 });
  await advance(100);
  scheduler.change({ id: 'a', n: 2 }); // back to A while B is still unsaved
  await advance(1100);
  assert.deepEqual(writes.map((w) => [w.deck.id, w.deck.n]), [['a', 1], ['b', 1], ['a', 2]]);
});

test('flush writes pending edits at once and nothing when all is saved', async (t) => {
  const { scheduler, writes, statuses, advance } = setup(t);
  await scheduler.flush();
  assert.equal(writes.length, 0);
  scheduler.change({ id: 'a', n: 1 });
  await scheduler.flush();
  assert.deepEqual(writes.map((w) => w.deck.n), [1]);
  assert.equal(statuses.at(-1), 'saved');
  await advance(2000);
  assert.equal(writes.length, 1, 'the flushed edit is not written again');
});

test('a system clock set back does not postpone the next save', async (t) => {
  const { scheduler, writes, advance } = setup(t);
  t.mock.timers.tick(700_000); // the mocked clock starts at 0
  scheduler.change({ id: 'a', n: 1 });
  await scheduler.flush();
  t.mock.timers.setTime(Date.now() - 600_000); // moves the clock only; timers do not fire
  scheduler.change({ id: 'a', n: 2 });
  await advance(1000);
  assert.deepEqual(writes.map((w) => w.deck.n), [1, 2]);
});

test('a deck whose write keeps failing does not hold up another deck', async (t) => {
  const attempts = [];
  const { scheduler, statuses, advance } = setup(t, async (deck) => {
    attempts.push({ key: `${deck.id}${deck.n}`, at: Date.now() });
    if (deck.id === 'a') throw new Error('storage full');
  });
  const keys = () => attempts.map((w) => w.key);
  scheduler.change({ id: 'a', n: 1 });
  await advance(500);
  scheduler.open({ id: 'b', n: 0 });
  await settle();
  assert.deepEqual(keys(), ['a1', 'a1']);
  scheduler.change({ id: 'b', n: 1 });
  await advance(1100);
  assert.deepEqual(keys(), ['a1', 'a1', 'b1', 'a1'], "B's edit is written within 1,100 ms");
  assert.equal(statuses.at(-1), 'error', 'A is still unsaved');
  await advance(10_000);
  assert.equal(attempts.length, 4, 'no retry without an edit');

  attempts.length = 0;
  for (let n = 2; n <= 5; n++) {
    scheduler.change({ id: 'b', n });
    await advance(50);
  }
  await advance(2000);
  assert.deepEqual(keys(), ['b5', 'a1'], 'one write per deck for a burst of edits');
  await assert.rejects(scheduler.flush(), /storage full/);
});

// A write that stays outstanding until the test releases it.
function held(t) {
  const release = [];
  const written = [];
  const ctx = setup(t, (deck) => new Promise((resolve, reject) => {
    written.push(`${deck.id}${deck.n}`);
    release.push({ ok: resolve, fail: () => reject(new Error('storage full')) });
  }));
  return { ...ctx, release, written };
}

test('an undo during an outstanding write is written, not taken for saved', async (t) => {
  const { scheduler, statuses, advance, release, written } = held(t);
  const d1 = { id: 'a', n: 1 };
  const d2 = { id: 'a', n: 2 };
  scheduler.change(d1);
  await advance(500);
  release[0].ok();
  await settle();
  scheduler.change(d2);
  await advance(1000);
  assert.deepEqual(written, ['a1', 'a2'], 'the write of the edit is outstanding');
  scheduler.change(d1); // undo puts the same object back
  assert.equal(statuses.at(-1), 'saving');
  release[1].ok();
  await advance(1100);
  assert.deepEqual(written, ['a1', 'a2', 'a1']);
  release[2].ok();
  await settle();
  assert.equal(statuses.at(-1), 'saved');
});

test('a redo during the write of the undone deck is written', async (t) => {
  const { scheduler, statuses, advance, release, written } = held(t);
  const d1 = { id: 'a', n: 1 };
  const d2 = { id: 'a', n: 2 };
  scheduler.change(d1);
  await advance(500);
  release[0].ok();
  await settle();
  scheduler.change(d2);
  await advance(1000);
  release[1].ok();
  await settle(); // d2 is stored
  scheduler.change(d1); // undo
  await advance(1000);
  assert.deepEqual(written, ['a1', 'a2', 'a1'], 'the write of the undo is outstanding');
  scheduler.change(d2); // redo: d2 is the last object that was stored
  release[2].ok();
  await advance(1100);
  assert.deepEqual(written, ['a1', 'a2', 'a1', 'a2']);
  release[3].ok();
  await settle();
  assert.equal(statuses.at(-1), 'saved');
});

test('a deleted deck whose write failed is not written again', async (t) => {
  const attempts = [];
  const { scheduler, statuses, advance } = setup(t, async (deck) => {
    attempts.push(`${deck.id}${deck.n}`);
    if (deck.id === 'a') throw new Error('storage full');
  });
  scheduler.change({ id: 'a', n: 1 });
  await advance(500);
  assert.equal(statuses.at(-1), 'error');
  await scheduler.forget('a'); // deleted on Home
  assert.equal(statuses.at(-1), 'saved', 'nothing is unsaved any more');
  scheduler.open({ id: 'b', n: 0 });
  scheduler.change({ id: 'b', n: 1 });
  await advance(1100);
  await scheduler.flush();
  assert.deepEqual(attempts, ['a1', 'b1']);
});

test('a deck deleted during its own write is deleted after it and not retried', async (t) => {
  const { scheduler, statuses, advance, release, written } = held(t);
  scheduler.change({ id: 'a', n: 1 });
  await advance(500);
  scheduler.change({ id: 'a', n: 2 }); // unsaved edit behind the outstanding write
  let forgotten = false;
  const done = scheduler.forget('a').then(() => (forgotten = true));
  await settle();
  assert.equal(forgotten, false, 'the delete waits for the outstanding write');
  release[0].fail();
  await done;
  await advance(3000);
  await scheduler.flush();
  assert.deepEqual(written, ['a1']);
  assert.equal(statuses.at(-1), 'saved');
});

test('reopening a deck whose write failed does not store the old copy over the one on screen', async (t) => {
  let fail = true;
  const attempts = [];
  const { scheduler, statuses, advance } = setup(t, async (deck) => {
    attempts.push(`${deck.id}${deck.n}`);
    if (fail) throw new Error('storage full');
  });
  scheduler.change({ id: 'a', n: 2 });
  await advance(500); // fails: storage still holds a1
  scheduler.open({ id: 'b', n: 0 });
  await settle();
  fail = false;
  attempts.length = 0;
  scheduler.open({ id: 'a', n: 1 }); // a1 loaded from storage and shown
  await advance(3000);
  await scheduler.flush();
  assert.deepEqual(attempts, [], 'a2 is not written behind the screen');
  assert.equal(statuses.at(-1), 'saved');
});

// The editor as storage.ts wires it, with random use: after every run, storage must hold what
// is on screen for every deck and nothing for a deleted one. Storage is a queue, like IndexedDB.
test('random editing, undo, redo, restore, deck switches, deletes and failed writes leave storage equal to the screen', async (t) => {
  for (let seed = 1; seed <= 100; seed++) {
    t.mock.timers.reset();
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
    let r = seed * 2654435761 % 4294967296;
    const rnd = (n) => ((r = (r * 1664525 + 1013904223) % 4294967296) >>> 8) % n;
    const stored = new Map();
    const failing = new Set();
    const log = [];
    let tail = Promise.resolve();
    const queue = (ms, fn) => (tail = tail.then(() => new Promise((res) => setTimeout(res, ms))).then(fn));
    let status = 'saved';
    const scheduler = createSaveScheduler({
      write: (deck) => {
        const fail = failing.has(deck.id);
        const run = queue(rnd(4) * 100, () => {
          if (fail) throw new Error('storage full');
          stored.set(deck.id, deck.n);
        });
        tail = run.catch(() => {});
        return run;
      },
      onStatus: (s) => (status = s),
    });
    const wait = async (p) => {
      let done = false;
      p.then(() => (done = true), () => (done = true));
      for (let i = 0; !done; i++) {
        assert.ok(i < 1000, `seed ${seed}: stuck after ${log.join(' ')}`);
        t.mock.timers.tick(10);
        await settle();
      }
      return p;
    };
    const advance = (ms) => wait(new Promise((res) => setTimeout(res, ms)));

    let n = 0;
    let ids = 0;
    const shown = new Map(); // what the user last saw of each deck
    const deleted = new Set();
    let deck = null;
    let past = [];
    let future = [];
    const show = (next, edited) => {
      const prev = deck;
      deck = next;
      shown.set(next.id, next.n);
      if (prev && prev.id !== next.id && !edited) scheduler.open(next);
      else scheduler.change(next);
    };
    const load = async (id) => {
      void scheduler.flush().catch(() => {}); // the editor page of the previous deck unmounts
      await advance(400); // the user reaches the next deck after any outstanding write has ended
      const stn = await wait(queue(0, () => stored.get(id)));
      past = [];
      future = [];
      show({ id, n: stn });
    };
    const create = async () => {
      const id = `d${++ids}`;
      stored.set(id, ++n);
      await load(id);
    };
    await create();
    for (let step = 0; step < 40; step++) {
      const op = rnd(12);
      if (op < 4) {
        log.push('edit');
        past.push(deck);
        future = [];
        show({ id: deck.id, n: ++n }, true);
      } else if (op === 4 && past.length) {
        log.push('undo');
        future.unshift(deck);
        show(past.pop(), true);
      } else if (op === 5 && future.length) {
        log.push('redo');
        past.push(deck);
        show(future.shift(), true);
      } else if (op === 6) {
        log.push('restore');
        past = [];
        future = [];
        show({ id: deck.id, n: ++n }, true); // openDeck with a copy under the same id
      } else if (op === 7) {
        const live = [...shown.keys()].filter((id) => !deleted.has(id));
        const id = live[rnd(live.length)];
        log.push(`open:${id}`);
        await load(id);
      } else if (op === 8) {
        log.push('new');
        await create();
      } else if (op === 9) {
        log.push(`delete:${deck.id}`);
        const id = deck.id;
        void scheduler.flush().catch(() => {});
        await advance(400);
        await wait((scheduler.forget?.(id) ?? Promise.resolve()).then(() => queue(0, () => stored.delete(id))));
        deleted.add(id);
        failing.delete(id);
        await create();
      } else if (op === 10) {
        log.push(`fail:${deck.id}`);
        failing.add(deck.id);
      } else if (op === 11) {
        log.push('works');
        failing.clear();
      }
      const ms = rnd(8) * 150;
      log.push(`+${ms}`);
      await advance(ms);
    }
    failing.clear();
    await wait(scheduler.flush().catch(() => {}));
    await advance(3000);
    await wait(scheduler.flush());
    const where = `seed ${seed}: ${log.join(' ')}`;
    for (const id of deleted) assert.equal(stored.has(id), false, `deleted ${id} is stored again; ${where}`);
    for (const [id, seen] of shown) if (!deleted.has(id)) assert.equal(stored.get(id), seen, `${id} stored differs from the screen; ${where}`);
    assert.equal(status, 'saved', where);
  }
});
