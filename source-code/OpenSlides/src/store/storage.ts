import { delMany, get, getMany, keys, set, setMany } from 'idb-keyval';
import type { Deck } from '../model/types';
import { uid } from '../model/factory';
import { useEditor } from './editor';
import { createSaveScheduler } from './saveScheduler';

// Decks are stored in the browser's IndexedDB, so the app works fully offline
// and nothing leaves the device unless the user exports a file.

const deckKey = (id: string) => `deck:${id}`;

export async function saveDeck(deck: Deck): Promise<void> {
  await set(deckKey(deck.id), deck);
}

export async function loadDeck(id: string): Promise<Deck | undefined> {
  return (await get(deckKey(id))) as Deck | undefined;
}

export async function deleteDeck(id: string): Promise<void> {
  await scheduler.forget(id); // an unsaved or failed edit must not store the deck again
  const versionKeys = (await keys()).filter(
    (k) => typeof k === 'string' && (k.startsWith(versionPrefix(id)) || k.startsWith(versionDeckPrefix(id))),
  );
  await delMany([deckKey(id), ...versionKeys]);
}

// Version history: named snapshots of a deck, kept alongside it in IndexedDB.
// Each version is two records: a small one the list reads (label, date, slide
// count) and the deck itself, read only to compare or restore that version.
export interface DeckVersion {
  id: string;
  deckId: string;
  createdAt: number;
  label: string;
  slideCount: number;
}

/** A version keeps a full copy of the deck, so only the newest ones are kept. */
export const MAX_VERSIONS = 20;

const versionPrefix = (deckId: string) => `version:${deckId}:`;
// Must not start with "deck:" or "version:", which listDecks and listVersions scan for.
const versionDeckPrefix = (deckId: string) => `versiondeck:${deckId}:`;

export async function saveVersion(deck: Deck, label = ''): Promise<DeckVersion> {
  const version: DeckVersion = {
    id: uid(),
    deckId: deck.id,
    createdAt: Date.now(),
    label: label.trim(),
    slideCount: deck.slides.length,
  };
  // One transaction, so a version never exists without its deck.
  await setMany([
    [`${versionDeckPrefix(deck.id)}${version.id}`, deck],
    [`${versionPrefix(deck.id)}${version.id}`, version],
  ]);
  // Counting keys is cheap; the records are read only when some must go.
  const count = (await keys()).filter((k) => typeof k === 'string' && k.startsWith(versionPrefix(deck.id))).length;
  if (count > MAX_VERSIONS) {
    const extra = (await listVersions(deck.id)).filter((v) => v.id !== version.id).slice(MAX_VERSIONS - 1);
    for (const old of extra) await deleteVersion(deck.id, old.id);
  }
  return version;
}

export async function listVersions(deckId: string): Promise<DeckVersion[]> {
  const all = (await keys()).filter(
    (k) => typeof k === 'string' && k.startsWith(versionPrefix(deckId)),
  ) as string[];
  const records = (await getMany(all)) as ((DeckVersion & { deck?: Deck }) | undefined)[];
  const versions: DeckVersion[] = [];
  for (const [i, record] of records.entries()) {
    if (!record) continue;
    const { deck, ...version } = record;
    if (deck) {
      // Saved before versions were split in two: move the deck to its own
      // record once. Both writes share a transaction, so an interrupted
      // conversion leaves the old record as it was.
      version.slideCount = deck.slides.length;
      await setMany([
        [`${versionDeckPrefix(deckId)}${version.id}`, deck],
        [all[i], version],
      ]);
    }
    versions.push(version);
  }
  return versions.sort((a, b) => b.createdAt - a.createdAt);
}

/** The deck a version holds; read when the user compares or restores it. */
export async function loadVersionDeck(deckId: string, versionId: string): Promise<Deck | undefined> {
  const deck = (await get(`${versionDeckPrefix(deckId)}${versionId}`)) as Deck | undefined;
  if (deck) return deck;
  // A version saved before the split that has not been listed yet.
  const old = (await get(`${versionPrefix(deckId)}${versionId}`)) as { deck?: Deck } | undefined;
  return old?.deck;
}

export async function deleteVersion(deckId: string, versionId: string): Promise<void> {
  await delMany([`${versionPrefix(deckId)}${versionId}`, `${versionDeckPrefix(deckId)}${versionId}`]);
}

export async function listDecks(): Promise<Deck[]> {
  const all = (await keys()).filter((k) => typeof k === 'string' && k.startsWith('deck:')) as string[];
  const decks = (await Promise.all(all.map((k) => get(k)))) as (Deck | undefined)[];
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
