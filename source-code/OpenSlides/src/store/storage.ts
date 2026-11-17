import { del, get, keys, set } from 'idb-keyval';
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
  await del(deckKey(id));
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
