import { del, delMany, get, getMany, keys, set } from 'idb-keyval';
import type { Asset, Deck } from '../model/types';
import { uid } from '../model/factory';
import { useEditor } from './editor';
import { createSaveScheduler } from './saveScheduler';
import { isSplitRecord, joinDeck, splitDeck, type DeckRecord } from './deckRecord';

// Decks are stored in the browser's IndexedDB, so the app works fully offline
// and nothing leaves the device unless the user exports a file.
//
// A deck's record (slides, theme, ...) is stored under `deck:<id>` and each of
// its images under `asset:<id>:<assetId>`, so a save writes only the images
// that changed (deckRecord.ts). Decks saved before that hold their images
// inline in the record; they load as they are and are split on their next save.

const deckKey = (id: string) => `deck:${id}`;
const assetPrefix = (deckId: string) => `asset:${deckId}:`;
const assetKey = (deckId: string, assetId: string) => `${assetPrefix(deckId)}${assetId}`;

// Per deck id, the asset objects storage holds under their own keys, as last
// written or loaded by this page. An asset absent here is written on the next save.
const stored = new Map<string, Map<string, Asset>>();

export async function saveDeck(deck: Deck): Promise<void> {
  const written = stored.get(deck.id) ?? new Map<string, Asset>();
  const { record, put, removed } = splitDeck(deck, written);
  // Images first, one per transaction: each put serialises only its own image,
  // so no single main-thread task holds them all. The record that lists them goes last.
  for (const [id, asset] of put) {
    await set(assetKey(deck.id, id), asset);
    written.set(id, asset);
    stored.set(deck.id, written);
  }
  await set(deckKey(deck.id), record);
  stored.set(deck.id, written);
  if (removed.length) {
    await delMany(removed.map((id) => assetKey(deck.id, id)));
    for (const id of removed) written.delete(id);
  }
}

async function readDeck(record: unknown): Promise<Deck | undefined> {
  if (!isSplitRecord(record)) return record as Deck | undefined;
  const r = record as DeckRecord<Deck>;
  const assets = (await getMany(r.assetIds.map((a) => assetKey(r.id, a)))) as (Asset | undefined)[];
  return joinDeck<Asset, Deck>(r, assets);
}

export async function loadDeck(id: string): Promise<Deck | undefined> {
  const record = await get(deckKey(id));
  const deck = await readDeck(record);
  // A split record's images are in storage as loaded; an inline one's are not yet.
  if (deck && isSplitRecord(record)) stored.set(id, new Map(Object.entries(deck.assets)));
  else stored.delete(id);
  return deck;
}

export async function deleteDeck(id: string): Promise<void> {
  await scheduler.forget(id); // an unsaved or failed edit must not store the deck again
  await del(deckKey(id));
  stored.delete(id);
  const assetKeys = (await keys()).filter((k) => typeof k === 'string' && k.startsWith(assetPrefix(id)));
  if (assetKeys.length) await delMany(assetKeys);
  for (const v of await listVersions(id)) await deleteVersion(id, v.id);
}

// Version history: named snapshots of a deck, kept alongside it in IndexedDB.
export interface DeckVersion {
  id: string;
  deckId: string;
  createdAt: number;
  label: string;
  deck: Deck;
}

const versionPrefix = (deckId: string) => `version:${deckId}:`;

export async function saveVersion(deck: Deck, label = ''): Promise<DeckVersion> {
  const version: DeckVersion = {
    id: uid(),
    deckId: deck.id,
    createdAt: Date.now(),
    label: label.trim(),
    deck: structuredClone(deck),
  };
  await set(`${versionPrefix(deck.id)}${version.id}`, version);
  return version;
}

export async function listVersions(deckId: string): Promise<DeckVersion[]> {
  const all = (await keys()).filter(
    (k) => typeof k === 'string' && k.startsWith(versionPrefix(deckId)),
  ) as string[];
  const versions = (await Promise.all(all.map((k) => get(k)))) as (DeckVersion | undefined)[];
  return versions
    .filter((v): v is DeckVersion => !!v && !!v.deck)
    .sort((a, b) => b.createdAt - a.createdAt);
}

export async function deleteVersion(deckId: string, versionId: string): Promise<void> {
  await del(`${versionPrefix(deckId)}${versionId}`);
}

export async function listDecks(): Promise<Deck[]> {
  const all = (await keys()).filter((k) => typeof k === 'string' && k.startsWith('deck:')) as string[];
  const decks = await Promise.all(all.map(async (k) => readDeck(await get(k))));
  return decks.filter((d): d is Deck => !!d && Array.isArray(d.slides)).sort((a, b) => b.updatedAt - a.updatedAt);
}

export type SaveStatus = 'saved' | 'saving' | 'error';
let status: SaveStatus = 'saved';
const listeners = new Set<(s: SaveStatus) => void>();
export function onSaveStatus(fn: (s: SaveStatus) => void) {
  listeners.add(fn);
  fn(status);
  return () => listeners.delete(fn);
}
function setStatus(s: SaveStatus) {
  status = s;
  listeners.forEach((l) => l(s));
}

const scheduler = createSaveScheduler<Deck>({
  write: (deck) => saveDeck({ ...deck, updatedAt: Date.now() }),
  onStatus: setStatus,
});

/** Autosaves the open deck while it is edited, and when the page is hidden or closed. */
export function startAutosave() {
  const unsubscribe = useEditor.subscribe((state, prev) => {
    const deck = state.deck;
    if (!deck || deck === prev.deck) return;
    if (prev.deck && prev.deck.id !== deck.id) scheduler.open(deck);
    else scheduler.change(deck);
  });
  // A hidden tab can be discarded and a closing one is gone: write pending edits now.
  const leaving = () => void flushSave().catch(() => {});
  const hidden = () => {
    if (document.visibilityState === 'hidden') leaving();
  };
  document.addEventListener('visibilitychange', hidden);
  window.addEventListener('pagehide', leaving);
  return () => {
    unsubscribe();
    document.removeEventListener('visibilitychange', hidden);
    window.removeEventListener('pagehide', leaving);
  };
}

/** Writes any unsaved edits now. */
export function flushSave(): Promise<void> {
  return scheduler.flush();
}
