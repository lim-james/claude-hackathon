// Text that is about to be written into an XML part (a .pptx is a zip of them).
// This file has no imports so `node --test` can load it directly.

// XML 1.0 cannot carry U+0000-U+001F other than tab, line feed and carriage return, nor U+FFFE and U+FFFF.
const FORBIDDEN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/;
const FORBIDDEN_ALL = new RegExp(FORBIDDEN.source, 'g');

/**
 * Makes text safe to write into XML. A vertical tab (PowerPoint's soft line break) and a form feed
 * (a page break in text copied from a PDF) become a line break, so the words on either side stay apart;
 * the other characters XML 1.0 cannot carry are dropped.
 */
export function xmlSafe(text: string): string {
  if (!FORBIDDEN.test(text)) return text;
  return text.replace(FORBIDDEN_ALL, (c) => (c === '\u000B' || c === '\u000C' ? '\n' : ''));
}
