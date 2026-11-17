import type {
  Animation,
  Asset,
  Background,
  ChartElement,
  Deck,
  DeckSize,
  ElementType,
  ImageElement,
  Layout,
  PlaceholderLink,
  ShapeElement,
  Slide,
  SlideElement,
  TableElement,
  TextElement,
  TextStyle,
  Theme,
} from './types';

// Type imports only, so `node --test` can load this file without a bundler.
//
// A deck file is usable when it has the shape `types.ts` declares, because that
// is the shape every reader of a deck (drawing, the deck check, the outline,
// thumbnails, export) is compiled against. The description below is tied to
// those types: leaving a field out, or describing it with another type, fails
// `tsc`. So a field added to a deck type has to be described here too.

const isObj = (v: unknown): v is Record<string, any> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Returns what is wrong with `v`, which is `field` of `owner`, or null. `field` is '' when `v` is `owner` itself. */
type Check<T> = { (v: unknown, owner: string, field: string): string | null; readonly __type?: (x: T) => T };
type Fields<T> = { [K in keyof T]-?: Check<T[K]> };

const missing = (owner: string, field: string) => (field ? `${owner} has no "${field}"` : `${owner} is missing`);
const wrong = (owner: string, field: string, kind: string) => (field ? `${owner} has a "${field}" that is not ${kind}` : `${owner} is not ${kind}`);

const leaf = <T>(kind: string, ok: (v: unknown) => boolean): Check<T> => (v, owner, field) =>
  v === undefined ? missing(owner, field) : ok(v) ? null : wrong(owner, field, kind);

const str = leaf<string>('text', (v) => typeof v === 'string');
const num = leaf<number>('a number', (v) => typeof v === 'number' && Number.isFinite(v));
const bool = leaf<boolean>('true or false', (v) => typeof v === 'boolean');
const oneOf = <T extends string>(...allowed: T[]): Check<T> => leaf<T>('one of the known values', (v) => allowed.includes(v as T));

const optional = <T>(check: Check<T>): Check<T | undefined> => (v, owner, field) => (v === undefined ? null : check(v, owner, field));

const list = <T>(item: Check<T>): Check<T[]> => (v, owner, field) => {
  if (v === undefined) return missing(owner, field);
  if (!Array.isArray(v)) return wrong(owner, field, 'a list');
  for (let i = 0; i < v.length; i++) {
    const problem = item(v[i] === undefined ? null : v[i], owner, `${field}[${i + 1}]`);
    if (problem) return problem;
  }
  return null;
};

const group = <T>(fields: Fields<T>): Check<T> => (v, owner, field) => {
  if (v === undefined) return missing(owner, field);
  if (!isObj(v)) return wrong(owner, field, 'a group of values');
  for (const key of Object.keys(fields)) {
    // Own values only, so a missing field is never read off Object.prototype.
    const value = Object.hasOwn(v, key) ? v[key] : undefined;
    const problem = (fields as Record<string, Check<unknown>>)[key](value, owner, field ? `${field}.${key}` : key);
    if (problem) return problem;
  }
  return null;
};

const background: Check<Background> = (v, owner, field) => {
  const name = field || 'background';
  const kinds: { [K in Background['kind']]: Check<Extract<Background, { kind: K }>> } = {
    solid: group({ kind: oneOf('solid'), color: str }),
    gradient: group({ kind: oneOf('gradient'), from: str, to: str, angle: num }),
    image: group({ kind: oneOf('image'), assetId: str, dim: num }),
  };
  if (!isObj(v) || typeof v.kind !== 'string' || !Object.hasOwn(kinds, v.kind)) return `${owner} has no ${name} of a known kind`;
  return (kinds as Record<string, Check<unknown>>)[v.kind](v, owner, name);
};

const base: Fields<Omit<Pick<SlideElement, keyof SlideElement>, 'type'>> = {
  id: str,
  x: num,
  y: num,
  w: num,
  h: num,
  rotation: num,
  opacity: num,
  locked: optional(bool),
  groupId: optional(str),
  name: optional(str),
  alt: optional(str),
  anim: optional(
    group<Animation>({
      effect: oneOf('none', 'fade', 'fly-up', 'fly-left', 'zoom', 'wipe'),
      trigger: oneOf('click', 'with-previous', 'after-previous'),
      duration: num,
      delay: num,
      order: num,
    }),
  ),
  ph: optional(group<PlaceholderLink>({ ref: str, geom: bool, style: bool })),
  placeholder: optional(oneOf('title', 'subtitle', 'body', 'body2', 'image', 'number', 'caption')),
  prompt: optional(str),
};

const elementOfType: { [K in ElementType]: Check<Extract<SlideElement, { type: K }>> } = {
  text: group<TextElement>({
    ...base,
    type: oneOf('text'),
    html: str,
    style: group<TextStyle>({
      fontFamily: str,
      fontSize: num,
      color: str,
      bold: bool,
      italic: bool,
      underline: bool,
      align: oneOf('left', 'center', 'right', 'justify'),
      valign: oneOf('top', 'middle', 'bottom'),
      lineHeight: num,
      letterSpacing: num,
    }),
    fill: str,
    padding: num,
    autoHeight: bool,
  }),
  shape: group<ShapeElement>({
    ...base,
    type: oneOf('shape'),
    shape: oneOf('rect', 'roundRect', 'ellipse', 'triangle', 'diamond', 'line', 'arrow', 'star', 'chevron', 'hexagon'),
    fill: str,
    stroke: str,
    strokeWidth: num,
    radius: num,
  }),
  image: group<ImageElement>({
    ...base,
    type: oneOf('image'),
    assetId: str,
    fit: oneOf('cover', 'contain'),
    radius: num,
    filters: group({ brightness: num, contrast: num, saturate: num, grayscale: num }),
  }),
  chart: group<ChartElement>({
    ...base,
    type: oneOf('chart'),
    chartType: oneOf('column', 'bar', 'line', 'area', 'pie', 'doughnut'),
    data: group({ labels: list(str), series: list(group({ name: str, values: list(num) })) }),
    title: str,
    showLegend: bool,
    showValues: bool,
    showGrid: bool,
    colors: list(str),
    fontFamily: str,
    fontSize: num,
    textColor: str,
  }),
  table: group<TableElement>({
    ...base,
    type: oneOf('table'),
    rows: list(list(str)),
    headerRow: bool,
    style: group({
      fontFamily: str,
      fontSize: num,
      color: str,
      headerFill: str,
      headerColor: str,
      borderColor: str,
      stripe: bool,
      stripeFill: str,
      fill: str,
    }),
  }),
};

/** The loose check, for a deck already saved in this browser: the list and its entries are there. */
function elementsProblem(owner: string, elements: unknown): string | null {
  if (!Array.isArray(elements)) return `${owner} has no list of elements`;
  for (let i = 0; i < elements.length; i++) {
    if (!isObj(elements[i])) return `element ${i + 1} of ${owner} is not an element`;
  }
  return null;
}

const elements: Check<SlideElement[]> = (v, owner) => {
  const loose = elementsProblem(owner, v);
  if (loose) return loose;
  const all = v as Record<string, unknown>[];
  for (let i = 0; i < all.length; i++) {
    const name = `element ${i + 1} of ${owner}`;
    const type = all[i].type;
    if (typeof type !== 'string' || !Object.hasOwn(elementOfType, type)) return `${name} has no known type`;
    const problem = (elementOfType as Record<string, Check<unknown>>)[type](all[i], name, '');
    if (problem) return problem;
  }
  return null;
};

const layout = group<Layout>({ id: str, name: str, background, elements });
const slide = group<Slide>({
  id: str,
  layoutId: str,
  elements,
  background: optional(background),
  notes: str,
  transition: oneOf('none', 'fade', 'slide', 'zoom'),
  hidden: optional(bool),
});
const size = group<DeckSize>({ w: num, h: num, preset: str });
const theme = group<Theme>({
  id: str,
  name: str,
  colors: group({ background: str, surface: str, text: str, muted: str, primary: str, secondary: str, accent: str }),
  fonts: group({ heading: str, body: str }),
  logoAssetId: optional(str),
});
const asset = group<Asset>({ id: str, dataUrl: str, w: num, h: num, name: str });

/** What a deck file has to hold. `readDeckFile` sets the rest of a `Deck` itself. */
type DeckFromFile = Omit<Deck, 'id' | 'title' | 'version' | 'createdAt' | 'updatedAt'>;
const fromFile: Fields<DeckFromFile> = {
  size: (v) => size(v, 'its slide size', ''),
  theme: (v) => theme(v, 'its theme', ''),
  layouts: (v) => each(v, 'layout', layout),
  slides: (v) => each(v, 'slide', slide),
  assets: (v) => {
    // The key is the file's own text, so an image is named by its position.
    const all = Object.values(v as object);
    for (let i = 0; i < all.length; i++) {
      const problem = asset(all[i], `image ${i + 1}`, '');
      if (problem) return problem;
    }
    return null;
  },
};

function each<T>(v: unknown, noun: string, check: Check<T>): string | null {
  const all = v as unknown[];
  for (let i = 0; i < all.length; i++) {
    const problem = check(all[i], `${noun} ${i + 1}`, '');
    if (problem) return problem;
  }
  return null;
}

/** The strict check, for a deck file: every field every reader of a deck uses is there and of the right type. */
function fileProblem(data: Record<string, any>): string | null {
  for (const key of Object.keys(fromFile) as (keyof DeckFromFile)[]) {
    const problem = fromFile[key](data[key] ?? (key === 'assets' ? {} : undefined), 'it', key);
    if (problem) return problem;
  }
  if (!(data.size.w > 0 && data.size.h > 0)) return 'its slide size is not a width and height above zero';
  if (data.slides.length === 0) return 'it has no slides';
  const layoutIds = new Set<unknown>(data.layouts.map((l: Layout) => l.id));
  for (let i = 0; i < data.slides.length; i++) {
    if (!layoutIds.has(data.slides[i].layoutId)) return `slide ${i + 1} uses a layout the deck doesn't have`;
  }
  return null;
}

/**
 * Says in plain words why a deck can't be drawn, or returns null when it can.
 *
 * By default this checks a deck file someone is opening, and is strict. With
 * `stored: true` it checks a deck already saved in this browser and only looks
 * for the structure that drawing depends on, so a deck that works today is
 * never flagged for a value that merely looks odd.
 */
export function deckProblem(data: unknown, opts: { stored?: boolean } = {}): string | null {
  if (!isObj(data)) return 'it is not a deck';

  if (!isObj(data.size)) return 'it has no slide size';
  if (!isObj(data.theme)) return 'it has no theme';
  if (!isObj(data.theme.colors)) return 'its theme has no colours';
  if (!isObj(data.theme.fonts)) return 'its theme has no fonts';
  if (data.assets !== undefined && !isObj(data.assets)) return 'its images are not readable';

  if (!Array.isArray(data.layouts)) return 'it has no layouts';
  for (let i = 0; i < data.layouts.length; i++) {
    if (!isObj(data.layouts[i])) return `layout ${i + 1} is not a layout`;
    const problem = elementsProblem(`layout ${i + 1}`, data.layouts[i].elements);
    if (problem) return problem;
  }

  if (!Array.isArray(data.slides)) return 'it has no slides';
  for (let i = 0; i < data.slides.length; i++) {
    if (!isObj(data.slides[i])) return `slide ${i + 1} is not a slide`;
    const problem = elementsProblem(`slide ${i + 1}`, data.slides[i].elements);
    if (problem) return problem;
  }

  // A stored deck with a stale layoutId or an odd value still draws, so only a file gets the strict check.
  return opts.stored ? null : fileProblem(data);
}
