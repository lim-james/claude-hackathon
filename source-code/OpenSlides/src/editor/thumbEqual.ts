import type { Deck, Slide } from '../model/types';

export interface ThumbProps {
  deck: Deck;
  slide: Slide;
  index: number;
}

/**
 * memo comparison for a slide-panel thumbnail. Every edit makes a new deck object, but the parts
 * the edit did not touch keep their identity, so compare only what a thumbnail draws.
 *
 * Keep this list in step with what SlideView and ElementView read from `deck`
 * (theme, layouts, assets, size, title, slides.length): a field missing here is a stale thumbnail.
 */
export function thumbEqual(a: ThumbProps, b: ThumbProps): boolean {
  return (
    a.slide === b.slide &&
    a.index === b.index &&
    a.deck.theme === b.deck.theme &&
    a.deck.layouts === b.deck.layouts &&
    a.deck.assets === b.deck.assets &&
    a.deck.size === b.deck.size &&
    a.deck.title === b.deck.title &&
    a.deck.slides.length === b.deck.slides.length
  );
}
