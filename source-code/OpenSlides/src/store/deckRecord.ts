// How a deck is laid out in storage. It has no imports, so a test can load it;
// storage.ts wires it to IndexedDB.
//
// A deck's images (`assets`) are most of its size, so each is stored under its
// own key and the deck record lists their ids. An autosave then writes the
// slides and only the images that were added or replaced, instead of
// serialising every image on each save (#134). Immer keeps objects that an
// edit did not touch, so an asset that is the same object as the one last
// written is unchanged.
//
// Decks saved before this hold their assets inline and have no `assetIds`;
// they are read as they are, and their first save moves the images out.

interface HasAssets<A> {
  assets: Record<string, A>;
}

export type DeckRecord<D extends HasAssets<unknown>> = Omit<D, 'assets'> & { assetIds: string[] };

/** True for a record whose images are stored under their own keys. */
export function isSplitRecord(record: unknown): boolean {
  return !!record && typeof record === 'object' && Array.isArray((record as { assetIds?: unknown }).assetIds);
}

/**
 * The deck record to store, the assets to write (those not in `written`, the
 * objects storage already holds by id) and the ids written before that the
 * deck no longer has.
 */
export function splitDeck<A, D extends HasAssets<A>>(
  deck: D,
  written: ReadonlyMap<string, A>,
): { record: DeckRecord<D>; put: [string, A][]; removed: string[] } {
  const { assets, ...rest } = deck;
  const put: [string, A][] = [];
  for (const [id, asset] of Object.entries(assets)) if (written.get(id) !== asset) put.push([id, asset]);
  const kept = new Set(Object.keys(assets));
  const removed = [...written.keys()].filter((id) => !kept.has(id));
  return { record: { ...rest, assetIds: [...kept] }, put, removed };
}

/**
 * The deck from a stored record. `assets` holds the stored asset for each of
 * the record's `assetIds`, in order (undefined when one is missing). A record
 * that still holds its assets inline is returned as it is.
 */
export function joinDeck<A, D extends HasAssets<A>>(record: DeckRecord<D> | D, assets: (A | undefined)[]): D {
  if (!isSplitRecord(record)) return record as D;
  const { assetIds, ...rest } = record as DeckRecord<D>;
  const joined: Record<string, A> = {};
  assetIds.forEach((id, i) => {
    const asset = assets[i];
    if (asset !== undefined) joined[id] = asset;
  });
  return { ...rest, assets: joined } as unknown as D;
}
