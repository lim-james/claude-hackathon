// Builds small .pptx files in memory, part by part, so each test states exactly the XML it feeds the importer.
import JSZip from 'jszip';

const REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
export const NS = `xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="${REL}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"`;
/** A valid 1 x 1 pixel PNG. */
export const PNG_1X1 = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

const rels = (list) =>
  `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${list
    .map(([id, type, target, external]) => `<Relationship Id="${id}" Type="${REL}/${type}" Target="${target}"${external ? ' TargetMode="External"' : ''}/>`)
    .join('')}</Relationships>`;

const THEME = `<a:theme ${NS}><a:themeElements><a:clrScheme name="Office">
<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2>
<a:accent1><a:srgbClr val="4F81BD"/></a:accent1><a:accent2><a:srgbClr val="C0504D"/></a:accent2><a:accent3><a:srgbClr val="9BBB59"/></a:accent3>
</a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Georgia"/></a:majorFont><a:minorFont><a:latin typeface="Verdana"/></a:minorFont></a:fontScheme></a:themeElements></a:theme>`;

/** Position and size in slide pixels of a default 1920 x 1080 import (6350 EMU per pixel). */
export const xfrm = (x, y, w, h, attrs = '') => `<a:xfrm${attrs}><a:off x="${x * 6350}" y="${y * 6350}"/><a:ext cx="${w * 6350}" cy="${h * 6350}"/></a:xfrm>`;

/** A shape: `ph` is the placeholder XML, `spPr` the shape properties, `text` the paragraphs. */
export const sp = ({ ph = '', spPr = '', text, bodyPr = '<a:bodyPr/>', style = '' }) =>
  `<p:sp><p:nvSpPr><p:cNvPr id="2" name="Shape"/><p:cNvSpPr/><p:nvPr>${ph}</p:nvPr></p:nvSpPr><p:spPr>${spPr}</p:spPr>${style}${text === undefined ? '' : `<p:txBody>${bodyPr}${text}</p:txBody>`}</p:sp>`;

/** One paragraph of plain runs. */
export const para = (text, pPr = '') => `<a:p>${pPr}<a:r><a:t>${text}</a:t></a:r></a:p>`;

/**
 * @param slides  [{ shapes, bg, attrs, rels: [[id, type, target]] }]; a slide's parts live in ppt/slides/.
 * @param opts    size [cx, cy] in EMU, layout / master (spTree contents), theme XML, extra { path: content }.
 */
export async function makePptx(slides, opts = {}) {
  const { size = [12192000, 6858000], layout = '', layoutBg = '', master = '', masterBg = '', masterRels = [], theme = THEME, files = {}, name = 'Fixture.pptx' } = opts;
  const zip = new JSZip();
  zip.file('ppt/presentation.xml', `<p:presentation ${NS}><p:sldIdLst>${slides.map((_, i) => `<p:sldId id="${256 + i}" r:id="rId${i + 1}"/>`).join('')}</p:sldIdLst><p:sldSz cx="${size[0]}" cy="${size[1]}"/></p:presentation>`);
  zip.file('ppt/_rels/presentation.xml.rels', rels([...slides.map((_, i) => [`rId${i + 1}`, 'slide', `slides/slide${i + 1}.xml`]), ['rIdTheme', 'theme', 'theme/theme1.xml']]));
  if (theme) zip.file('ppt/theme/theme1.xml', theme);
  zip.file('ppt/slideLayouts/slideLayout1.xml', `<p:sldLayout ${NS}><p:cSld>${layoutBg}<p:spTree>${layout}</p:spTree></p:cSld></p:sldLayout>`);
  zip.file('ppt/slideLayouts/_rels/slideLayout1.xml.rels', rels([['rId1', 'slideMaster', '../slideMasters/slideMaster1.xml']]));
  zip.file('ppt/slideMasters/slideMaster1.xml', `<p:sldMaster ${NS}><p:cSld>${masterBg}<p:spTree>${master}</p:spTree></p:cSld>${opts.txStyles ?? ''}</p:sldMaster>`);
  zip.file('ppt/slideMasters/_rels/slideMaster1.xml.rels', rels(masterRels));
  slides.forEach((s, i) => {
    if (!s.missing) zip.file(`ppt/slides/slide${i + 1}.xml`, `<p:sld ${NS}${s.attrs ?? ''}><p:cSld>${s.bg ?? ''}<p:spTree>${s.shapes ?? ''}</p:spTree></p:cSld></p:sld>`);
    zip.file(`ppt/slides/_rels/slide${i + 1}.xml.rels`, rels([['rIdLayout', 'slideLayout', '../slideLayouts/slideLayout1.xml'], ...(s.rels ?? [])]));
  });
  for (const [path, content] of Object.entries(files)) zip.file(path, content);
  return new File([await zip.generateAsync({ type: 'uint8array' })], name);
}
