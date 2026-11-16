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
