import { memo, useCallback, useMemo, useEffect, useLayoutEffect, useRef, type CSSProperties, type TdHTMLAttributes, type PointerEvent as RPointerEvent, type ReactNode } from 'react';
import type { Deck, ImageElement, ShapeElement, SlideElement, TableElement, TextElement, Theme } from '../model/types';
import { fontStack, resolveColor, resolveFont } from '../model/theme';
import { applyTokens, isEmptyHtml, type TokenContext } from '../model/resolve';
import { shapePath } from './shapes';
import { ChartView } from './ChartView';
import { rememberSelection, sanitizeHtml } from './richtext';
import type { BuildInfo } from './builds';
import { xmlSafe } from '../io/xmlText';
import { clearPendingText, setPendingText } from '../store/editor';

export type RenderMode = 'edit' | 'thumb' | 'present' | 'export';

export interface AnimState {
  info: BuildInfo;
  phase: 'hidden' | 'enter' | 'shown';
  effect: string;
}

export interface ElementViewProps {
  el: SlideElement;
  /** Fields read from it here must also be listed in thumbEqual (editor/thumbEqual.ts). */
  deck: Deck;
  mode: RenderMode;
  tokens: TokenContext;
  prompt?: string;
  listDefault?: boolean;
  editing?: boolean;
  passive?: boolean; // layout decorations under a slide: no pointer events
  anim?: AnimState;
  onPointerDown?: (e: RPointerEvent, el: SlideElement) => void;
  onDoubleClick?: (el: SlideElement) => void;
  onTextCommit?: (el: TextElement, html: string) => void;
  /** Text typed so far, while the box is still being edited: autosave stores it (#123). */
  onTextDraft?: (el: TextElement, html: string) => void;
  onTextMeasure?: (el: TextElement, h: number) => void;
  onTableCommit?: (el: TableElement, rows: string[][]) => void;
  onTableDraft?: (el: TableElement, rows: string[][]) => void;
}

// Each draft re-renders the editor, so typing does not draft on every key:
// the text goes into the deck when typing pauses, and at least this often while it goes on.
const DRAFT_PAUSE_MS = 600;
const DRAFT_MAX_MS = 3000;

/**
 * Typed text reaches the deck through `put` (#123). `typed()` after each input;
 * `drop()` forgets what is pending when the edit is committed instead. Unmounting
 * or leaving the page (flushPendingText) puts what is pending at once.
 */
function useDraft(put: (() => void) | undefined) {
  const pause = useRef<ReturnType<typeof setTimeout> | null>(null);
  const max = useRef<ReturnType<typeof setTimeout> | null>(null);
  const putRef = useRef(put);
  putRef.current = put;
  const cancel = useCallback(() => {
    if (pause.current === null) return false;
    clearTimeout(pause.current);
    clearTimeout(max.current!);
    pause.current = max.current = null;
    return true;
  }, []);
  const now = useCallback(() => {
    if (!cancel()) return;
    putRef.current?.(); // into the deck first, so the save status never shows a gap
    clearPendingText(now);
  }, [cancel]);
  const typed = useCallback(() => {
    if (!putRef.current) return;
    if (pause.current !== null) clearTimeout(pause.current);
    pause.current = setTimeout(now, DRAFT_PAUSE_MS);
    if (max.current === null) {
      max.current = setTimeout(now, DRAFT_MAX_MS);
      setPendingText(now);
    }
  }, [now]);
  useEffect(() => now, [now]);
  return {
    typed,
    drop: useCallback(() => {
      cancel();
      clearPendingText(now);
    }, [cancel, now]),
  };
}

export function textCss(el: TextElement, theme: Theme): CSSProperties {
  const s = el.style;
  return {
    fontFamily: fontStack(resolveFont(s.fontFamily, theme)),
    fontSize: s.fontSize,
    color: resolveColor(s.color, theme),
    fontWeight: s.bold ? 700 : 400,
    fontStyle: s.italic ? 'italic' : 'normal',
    textDecoration: s.underline ? 'underline' : 'none',
    textAlign: s.align,
    lineHeight: s.lineHeight,
    letterSpacing: `${s.letterSpacing}em`,
  };
}

function TextEditable({ el, theme, listDefault, onCommit, onDraft, onMeasure }: { el: TextElement; theme: Theme; listDefault?: boolean; onCommit: (html: string) => void; onDraft?: (html: string) => void; onMeasure?: (h: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const committed = useRef(false);
  const draft = useDraft(onDraft && (() => ref.current && !committed.current && onDraft(sanitizeHtml(ref.current.innerHTML))));
  useLayoutEffect(() => {
    const node = ref.current!;
    node.innerHTML = el.html && !isEmptyHtml(el.html) ? sanitizeHtml(el.html) : listDefault ? '<ul><li><br></li></ul>' : '<p><br></p>';
    node.focus();
    document.execCommand('defaultParagraphSeparator', false, 'p');
    const range = document.createRange();
    range.selectNodeContents(node);
    if (!isEmptyHtml(el.html)) {
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    } else {
      range.collapse(false);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
    return () => {
      // React StrictMode re-runs effects on the same node; only commit if the editor really left the page.
      setTimeout(() => {
        if (!committed.current && !node.isConnected) {
          draft.drop();
          committed.current = true;
          onCommit(sanitizeHtml(node.innerHTML));
        }
      }, 0);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div
      ref={ref}
      className="rt rt-editing"
      contentEditable
      suppressContentEditableWarning
      data-richtext="true"
      spellCheck
      onPointerDown={(e) => e.stopPropagation()}
      onInput={() => {
        if (onMeasure && ref.current) onMeasure(ref.current.offsetHeight);
        draft.typed();
      }}
      onPaste={(e) => {
        // Keep pasted text clean: plain text only, line breaks preserved, nothing a .pptx cannot carry.
        const text = xmlSafe(e.clipboardData.getData('text/plain'));
        if (text) {
          e.preventDefault();
          document.execCommand('insertText', false, text);
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          (e.currentTarget as HTMLElement).blur();
        }
        if (e.key === 'Tab') {
          e.preventDefault();
          document.execCommand(e.shiftKey ? 'outdent' : 'indent');
        }
        e.stopPropagation();
      }}
      onKeyUp={() => rememberSelection(ref.current!)}
      onMouseUp={() => rememberSelection(ref.current!)}
      onBlur={(e) => {
        rememberSelection(ref.current!);
        // Focus moving into the text toolbar keeps the edit session open.
        const next = e.relatedTarget as HTMLElement | null;
        if (next && next.closest('[data-keep-editing]')) return;
        if (committed.current) return;
        draft.drop();
        committed.current = true;
        onCommit(sanitizeHtml(ref.current!.innerHTML));
      }}
      style={{ outline: 'none', minHeight: '1em', cursor: 'text' }}
    />
  );
}

function TextBlock({ el, deck, mode, tokens, prompt, editing, listDefault, onTextCommit, onTextDraft, onTextMeasure }: ElementViewProps & { el: TextElement }) {
  const theme = deck.theme;
  const innerRef = useRef<HTMLDivElement>(null);
  const empty = isEmptyHtml(el.html);
  const cleanHtml = useMemo(() => sanitizeHtml(el.html), [el.html]);
  const html = applyTokens(cleanHtml, tokens);
  useEffect(() => {
    if (mode !== 'edit' || editing || !el.autoHeight || !onTextMeasure || !innerRef.current) return;
    const h = innerRef.current.offsetHeight + el.padding * 2;
    if (Math.abs(h - el.h) > 1) onTextMeasure(el, h);
  });
  const justify = el.style.valign === 'middle' ? 'center' : el.style.valign === 'bottom' ? 'flex-end' : 'flex-start';
  return (
    <div
      className="text-box"
      style={{
        ...textCss(el, theme),
        width: '100%',
        height: '100%',
        padding: el.padding,
        background: resolveColor(el.fill, theme),
        display: 'flex',
        flexDirection: 'column',
        justifyContent: justify,
        boxSizing: 'border-box',
        overflow: 'visible',
      }}
    >
      {editing ? (
        <TextEditable
          el={el}
          theme={theme}
          listDefault={listDefault}
          onCommit={(h) => onTextCommit?.(el, h)}
          onDraft={onTextDraft && ((h) => onTextDraft(el, h))}
          onMeasure={el.autoHeight && onTextMeasure ? (h) => onTextMeasure(el, h + el.padding * 2) : undefined}
        />
      ) : empty && mode === 'edit' && prompt ? (
        <div ref={innerRef} className="rt rt-prompt">
          {prompt}
        </div>
      ) : (
        <div ref={innerRef} className="rt" dangerouslySetInnerHTML={{ __html: html }} />
      )}
    </div>
  );
}

function ShapeBlock({ el, theme }: { el: ShapeElement; theme: Theme }) {
  const fill = resolveColor(el.fill, theme);
  const stroke = resolveColor(el.stroke, theme);
  const sw = el.strokeWidth;
  if (el.shape === 'line' || el.shape === 'arrow') {
    const y = el.h / 2;
    const head = Math.max(sw * 3.2, 14);
    return (
      <svg width="100%" height="100%" viewBox={`0 0 ${el.w} ${el.h}`} preserveAspectRatio="none" style={{ overflow: 'visible' }}>
        <line x1={0} y1={y} x2={el.shape === 'arrow' ? el.w - head * 0.8 : el.w} y2={y} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />
        {el.shape === 'arrow' && <path d={`M${el.w - head} ${y - head * 0.6} L${el.w} ${y} L${el.w - head} ${y + head * 0.6} Z`} fill={stroke} />}
      </svg>
    );
  }
  const inset = sw / 2;
  return (
    <svg width="100%" height="100%" viewBox={`${-inset} ${-inset} ${el.w + sw} ${el.h + sw}`} preserveAspectRatio="none" style={{ overflow: 'visible', display: 'block' }}>
      <path d={shapePath(el.shape, el.w, el.h, el.radius)} fill={fill} stroke={sw > 0 ? stroke : 'none'} strokeWidth={sw} strokeLinejoin="round" />
    </svg>
  );
}

function ImageBlock({ el, deck, mode, prompt }: { el: ImageElement; deck: Deck; mode: RenderMode; prompt?: string }) {
  const id = el.assetId === 'theme:logo' ? deck.theme.logoAssetId : el.assetId;
  const asset = id ? deck.assets[id] : undefined;
  if (!asset) {
    if (el.assetId === 'theme:logo') return null;
    if (mode !== 'edit') return null;
    return (
      <div className="img-placeholder" style={{ borderRadius: el.radius }}>
        <span>{prompt ?? 'Drop an image here'}</span>
      </div>
    );
  }
  const f = el.filters;
  return (
    <img
      src={asset.dataUrl}
      alt={el.alt ?? ''}
      draggable={false}
      style={{
        width: '100%',
        height: '100%',
        objectFit: el.fit,
        borderRadius: el.radius,
        display: 'block',
        filter: `brightness(${f.brightness}) contrast(${f.contrast}) saturate(${f.saturate}) grayscale(${f.grayscale})`,
      }}
    />
  );
}

/** A cell being edited: React sets its text once, so a draft reaching the deck cannot move the caret. */
function EditingCell({ text, ...props }: { text: string } & TdHTMLAttributes<HTMLTableCellElement>) {
  const ref = useRef<HTMLTableCellElement>(null);
  useLayoutEffect(() => {
    ref.current!.textContent = text;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <td ref={ref} contentEditable suppressContentEditableWarning {...props} />;
}

function TableBlock({ el, theme, editing, onCommit, onDraft }: { el: TableElement; theme: Theme; editing?: boolean; onCommit?: (rows: string[][]) => void; onDraft?: (rows: string[][]) => void }) {
  const s = el.style;
  const rowsRef = useRef<string[][]>(el.rows.map((r) => [...r]));
  useEffect(() => {
    rowsRef.current = el.rows.map((r) => [...r]);
  }, [el.rows]);
  const cols = Math.max(1, ...el.rows.map((r) => r.length));
  const cellText = (td: HTMLElement) => (td.innerText ?? '').replace(/\n+$/, '');
  // The rows with one cell's text in place, or null when that is what they hold already.
  const withCell = (ri: number, ci: number, text: string) => {
    const rows = rowsRef.current.map((r) => [...r]);
    while (rows[ri].length < cols) rows[ri].push('');
    if (rows[ri][ci] === text) return null;
    rows[ri][ci] = text;
    return rows;
  };
  const typing = useRef<{ ri: number; ci: number; td: HTMLElement } | null>(null);
  const drafted = useRef(false); // a draft went into the deck since the last commit
  const draft = useDraft(
    onDraft &&
      (() => {
        const t = typing.current;
        const rows = t && withCell(t.ri, t.ci, cellText(t.td));
        if (!rows) return;
        rowsRef.current = rows;
        drafted.current = true;
        onDraft(rows);
      }),
  );
  const border = resolveColor(s.borderColor, theme);
  return (
    <table
      className="el-table"
      style={{
        width: '100%',
        height: '100%',
        borderCollapse: 'collapse',
        tableLayout: 'fixed',
        fontFamily: fontStack(resolveFont(s.fontFamily, theme)),
        fontSize: s.fontSize,
        color: resolveColor(s.color, theme),
        background: resolveColor(s.fill, theme),
      }}
      onPointerDown={editing ? (e) => e.stopPropagation() : undefined}
    >
      <tbody>
        {el.rows.map((row, ri) => {
          const header = el.headerRow && ri === 0;
          const bg = header ? resolveColor(s.headerFill, theme) : s.stripe && ri % 2 === (el.headerRow ? 0 : 1) ? resolveColor(s.stripeFill, theme) : 'transparent';
          return (
            <tr key={ri} style={{ background: bg, height: `${100 / el.rows.length}%` }}>
              {Array.from({ length: cols }, (_, ci) => {
                const style: CSSProperties = {
                  border: `2px solid ${border}`,
                  padding: `${s.fontSize * 0.35}px ${s.fontSize * 0.55}px`,
                  fontWeight: header ? 700 : 400,
                  color: header ? resolveColor(s.headerColor, theme) : undefined,
                  verticalAlign: 'middle',
                  overflow: 'hidden',
                  wordBreak: 'break-word',
                  outline: 'none',
                  cursor: editing ? 'text' : undefined,
                };
                if (!editing) {
                  return (
                    <td key={ci} style={style}>
                      {row[ci] ?? ''}
                    </td>
                  );
                }
                return (
                  <EditingCell
                    key={`edit-${ci}`}
                    text={row[ci] ?? ''}
                    style={style}
                    onInput={(e) => {
                      typing.current = { ri, ci, td: e.currentTarget };
                      draft.typed();
                    }}
                    onBlur={(e) => {
                      draft.drop();
                      const rows = withCell(ri, ci, cellText(e.currentTarget));
                      if (rows) rowsRef.current = rows;
                      // A drafted cell already matches the deck; the commit still makes its undo step.
                      if (rows || drafted.current) onCommit?.(rows ?? rowsRef.current.map((r) => [...r]));
                      drafted.current = false;
                    }}
                    onKeyDown={(e) => e.stopPropagation()}
                  />
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function animStyle(anim: AnimState | undefined): CSSProperties {
  if (!anim) return {};
  if (anim.phase === 'hidden') return { visibility: 'hidden' };
  if (anim.phase === 'enter') {
    return {
      animationName: `df-${anim.effect}`,
      animationDuration: `${anim.info.duration}ms`,
      animationDelay: `${anim.info.delay}ms`,
      animationFillMode: 'both',
      animationTimingFunction: 'cubic-bezier(.2,.7,.2,1)',
    };
  }
  return {};
}

function ElementViewInner(props: ElementViewProps) {
  const { el, deck, mode, editing, passive, anim, onPointerDown, onDoubleClick } = props;
  const theme = deck.theme;
  const style: CSSProperties = {
    position: 'absolute',
    left: el.x,
    top: el.y,
    width: el.w,
    height: el.h,
    transform: el.rotation ? `rotate(${el.rotation}deg)` : undefined,
    opacity: el.opacity,
    pointerEvents: passive || mode !== 'edit' ? 'none' : 'auto',
    cursor: mode === 'edit' && !passive ? (el.locked ? 'default' : 'move') : undefined,
    zIndex: editing ? 5 : undefined,
    ...animStyle(anim),
  };
  let content: ReactNode = null;
  switch (el.type) {
    case 'text':
      content = <TextBlock {...props} el={el} />;
      break;
    case 'shape':
      content = <ShapeBlock el={el} theme={theme} />;
      break;
    case 'image':
      content = <ImageBlock el={el} deck={deck} mode={mode} prompt={props.prompt} />;
      break;
    case 'chart':
      content = <ChartView el={el} theme={theme} />;
      break;
    case 'table':
      content = (
        <TableBlock
          el={el}
          theme={theme}
          editing={editing}
          onCommit={(rows) => props.onTableCommit?.(el, rows)}
          onDraft={props.onTableDraft && ((rows) => props.onTableDraft!(el, rows))}
        />
      );
      break;
  }
  return (
    <div
      className={`el el-${el.type}${mode === 'edit' && props.prompt && el.type === 'text' && isEmptyHtml(el.html) && !editing ? ' el-ph-empty' : ''}`}
      data-el-id={el.id}
      style={style}
      onPointerDown={onPointerDown && !passive ? (e) => onPointerDown(e, el) : undefined}
      onDoubleClick={onDoubleClick && !passive ? () => onDoubleClick(el) : undefined}
    >
      {content}
    </div>
  );
}

export const ElementView = memo(ElementViewInner);
