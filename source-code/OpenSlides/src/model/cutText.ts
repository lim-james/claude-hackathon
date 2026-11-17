// Shortening text to a length limit. This file has no imports so `node --test` can load it directly.

const graphemes = new Intl.Segmenter(undefined, { granularity: 'grapheme' });

/**
 * The longest start of `text` that is at most `max` UTF-16 units and ends between two characters as
 * the reader sees them: never inside a surrogate pair (half an emoji), a combining sequence (e + accent),
 * an emoji sequence or a flag. Reads only as far as the limit, so a huge text costs no more than a short one.
 */
export function cutText(text: string, max: number): string {
  if (text.length <= max) return text;
  let end = 0;
  for (const { index, segment } of graphemes.segment(text)) {
    if (index + segment.length > max) break;
    end = index + segment.length;
  }
  return text.slice(0, end);
}
