// Text that is about to be written into an XML part (a .pptx is a zip of them).
// This file has no imports so `node --test` can load it directly.

// XML 1.0 cannot carry U+0000-U+001F other than tab, line feed and carriage return, nor U+FFFE and U+FFFF,
// nor half of a surrogate pair (a lone U+D800-U+DFFF, e.g. from text cut through an emoji): it has no UTF-8
// form, so the part would not parse. With the `u` flag the class matches only lone halves; a whole emoji
// is one code point and does not match.
const FORBIDDEN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uD800-\uDFFF\uFFFE\uFFFF]/u;
const FORBIDDEN_ALL = new RegExp(FORBIDDEN.source, 'gu');

/**
 * Makes text safe to write into XML. A vertical tab (PowerPoint's soft line break) and a form feed
 * (a page break in text copied from a PDF) become a line break, so the words on either side stay apart;
 * half of a surrogate pair becomes U+FFFD (the replacement character), so the loss stays visible;
 * the other characters XML 1.0 cannot carry are dropped.
 */
export function xmlSafe(text: string): string {
  if (!FORBIDDEN.test(text)) return text;
  return text.replace(FORBIDDEN_ALL, (c) => (c === '\u000B' || c === '\u000C' ? '\n' : c >= '\uD800' && c <= '\uDFFF' ? '\uFFFD' : ''));
}
