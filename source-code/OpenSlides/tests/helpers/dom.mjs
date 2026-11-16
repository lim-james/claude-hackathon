// A small stand-in for the browser DOM, covering only what the PowerPoint import and export
// code touches: XML parsing with namespaces, rich-text HTML parsing, inline styles and images.
// It is a test double, not a browser: anything outside that subset throws instead of guessing.

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
const decode = (s) =>
  s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) =>
    e[0] === '#' ? String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : +e.slice(1)) : (ENTITIES[e] ?? m),
  );
const encode = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
const VOID = new Set(['br', 'hr', 'img']);
const kebab = (prop) => prop.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

class Node {
  parentNode = null;
  childNodes = [];
  get firstChild() {
    return this.childNodes[0] ?? null;
  }
  remove() {
    const list = this.parentNode?.childNodes;
    if (list) list.splice(list.indexOf(this), 1);
    this.parentNode = null;
  }
  replaceWith(node) {
    this.parentNode.insertBefore(node, this);
    this.remove();
  }
}

class Text extends Node {
  nodeType = 3;
  constructor(data) {
    super();
    this.textContent = data;
  }
}

class Element extends Node {
  nodeType = 1;
  attrs = new Map();
  constructor(name, html, namespaces = {}) {
    super();
    this.html = html;
    this.namespaces = namespaces;
    this.localName = html ? name.toLowerCase() : name.slice(name.indexOf(':') + 1);
    this.tagName = html ? name.toUpperCase() : name;
  }
  get children() {
    return this.childNodes.filter((n) => n.nodeType === 1);
  }
  get firstElementChild() {
    return this.children[0] ?? null;
  }
  get textContent() {
    return this.childNodes.map((n) => n.textContent).join('');
  }
  insertBefore(node, ref) {
    node.remove();
    this.childNodes.splice(ref ? this.childNodes.indexOf(ref) : this.childNodes.length, 0, node);
    node.parentNode = this;
    return node;
  }
  appendChild(node) {
    return this.insertBefore(node, null);
  }
  removeChild(node) {
    node.remove();
    return node;
  }
  getAttribute(name) {
    return this.attrs.get(name) ?? null;
  }
  getAttributeNS(ns, local) {
    for (const [name, value] of this.attrs) {
      const colon = name.indexOf(':');
      if (colon > 0 && name.slice(colon + 1) === local && this.namespaces[name.slice(0, colon)] === ns) return value;
    }
    return null;
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
  }
  removeAttribute(name) {
    this.attrs.delete(name);
  }
  descendants(out = []) {
    for (const c of this.children) {
      out.push(c);
      c.descendants(out);
    }
    return out;
  }
  getElementsByTagNameNS(ns, local) {
    if (ns !== '*') throw new Error('test DOM: only the "*" namespace is supported');
    return this.descendants().filter((e) => e.localName === local);
  }
  querySelectorAll(selector) {
    const tags = selector.split(',').map((s) => s.trim());
    if (!tags.every((t) => /^[a-z]+$/i.test(t))) throw new Error(`test DOM: unsupported selector ${selector}`);
    return this.descendants().filter((e) => tags.some((t) => t.toUpperCase() === e.tagName));
  }
  /** Inline style as the browser exposes it: camelCase reads and writes backed by the style attribute. */
  get style() {
    const read = () =>
      new Map(
        (this.getAttribute('style') ?? '')
          .split(';')
          .filter((d) => d.includes(':'))
          .map((d) => [d.slice(0, d.indexOf(':')).trim().toLowerCase(), d.slice(d.indexOf(':') + 1).trim()]),
      );
    const write = (map) => this.setAttribute('style', [...map].map(([k, v]) => `${k}: ${v};`).join(' '));
    return new Proxy(
      {},
      {
        get: (_, prop) => read().get(kebab(prop)) ?? '',
        set: (_, prop, value) => {
          write(read().set(kebab(prop), value));
          return true;
        },
      },
    );
  }
  get innerHTML() {
    return this.childNodes.map(serialize).join('');
  }
  set innerHTML(markup) {
    this.childNodes = [];
    parseInto(this, markup, true);
  }
}

function serialize(node) {
  if (node.nodeType === 3) return encode(node.textContent);
  const attrs = [...node.attrs].map(([k, v]) => ` ${k}="${encode(v).replace(/"/g, '&quot;')}"`).join('');
  const open = `<${node.localName}${attrs}>`;
  return VOID.has(node.localName) ? open : `${open}${node.innerHTML}</${node.localName}>`;
}

const TOKEN = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[([\s\S]*?)\]\]>|<!DOCTYPE[^>]*>|<\/([^\s>]+)\s*>|<([^\s/>]+)((?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*)\s*(\/?)>|([^<]+)/g;
const ATTR = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g;

function parseInto(root, markup, html) {
  let cur = root;
  let consumed = 0;
  for (const m of markup.matchAll(TOKEN)) {
    if (m.index !== consumed) throw new Error(`malformed markup at ${consumed}`);
    consumed = m.index + m[0].length;
    const [, cdata, close, open, attrText, selfClose, text] = m;
    if (cdata !== undefined) cur.appendChild(new Text(cdata));
    else if (text !== undefined) {
      if (html || cur !== root) cur.appendChild(new Text(decode(text)));
      else if (text.trim()) throw new Error('text outside the root element');
    } else if (close) {
      if (cur === root || (html ? cur.localName !== close.toLowerCase() : cur.tagName !== close)) throw new Error(`unexpected </${close}>`);
      cur = cur.parentNode;
    } else if (open) {
      const attrs = [...attrText.matchAll(ATTR)].map((a) => [html ? a[1].toLowerCase() : a[1], decode(a[2] ?? a[3] ?? '')]);
      const namespaces = { ...cur.namespaces };
      for (const [k, v] of attrs) if (k.startsWith('xmlns:')) namespaces[k.slice(6)] = v;
      const el = new Element(open, html, namespaces);
      for (const [k, v] of attrs) el.setAttribute(k, v);
      cur.appendChild(el);
      if (!selfClose && !(html && VOID.has(el.localName))) cur = el;
    }
  }
  if (consumed !== markup.length || cur !== root) throw new Error('malformed markup: unclosed element');
}

class XmlDocument extends Element {
  constructor() {
    super('#document', false);
  }
  get documentElement() {
    return this.firstElementChild;
  }
}

class DOMParser {
  parseFromString(text, type) {
    if (type !== 'application/xml') throw new Error(`test DOM: unsupported type ${type}`);
    const doc = new XmlDocument();
    try {
      parseInto(doc, text, false);
      if (doc.children.length !== 1) throw new Error('expected one root element');
    } catch (err) {
      // Browsers do not throw on bad XML; they hand back a document holding a <parsererror>.
      doc.childNodes = [];
      doc.appendChild(new Element('parsererror', false)).appendChild(new Text(String(err.message)));
    }
    return doc;
  }
}

/** Loads PNG data URLs (reading the size from the header) and fails everything else, like an undecodable image. */
class Image {
  naturalWidth = 0;
  naturalHeight = 0;
  set src(url) {
    const bytes = Buffer.from(String(url).split(',')[1] ?? '', 'base64');
    const png = /^data:image\/png;base64,/.test(url) && bytes.length >= 24 && bytes.readUInt32BE(0) === 0x89504e47;
    if (png) [this.naturalWidth, this.naturalHeight] = [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
    queueMicrotask(() => (png ? this.onload?.() : this.onerror?.()));
  }
}

/** Puts the stand-ins on globalThis, where the app code expects the browser's. */
export function installDom() {
  Object.assign(globalThis, { document: { createElement: (tag) => new Element(tag, true) }, DOMParser, Image });
}
