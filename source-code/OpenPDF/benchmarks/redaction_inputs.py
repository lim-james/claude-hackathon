"""Which hand-built annotations and form fields still hold a secret in the saved file after PDF.redact.

Run: python benchmarks/redaction_inputs.py [--tree OTHER/source-code/OpenPDF] [--whole] [NAME ...]

Each input is written as raw objects into a two-page file, reopened from bytes,
redacted with PDF.redact over (60,190)-(320,250) (--whole: the whole page), saved
with PDF.save, and the saved file is scanned for the secret: every object, every
stream raw and decoded, the trailer, the raw bytes, and every flate stream found
in the raw bytes. --tree runs another checkout's OpenPDF source (such as master).
One line per input, then a count of clean inputs. NAME runs only inputs whose
name contains it.
"""
import argparse
import os
import re
import sys
import tempfile
import zlib

_parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
_parser.add_argument('--tree', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     help='OpenPDF source folder to test (default: this one)')
_parser.add_argument('--whole', action='store_true', help='redact the whole page')
_parser.add_argument('names', nargs='*')
ARGS = _parser.parse_args()
sys.path.insert(0, os.path.abspath(ARGS.tree))

import fitz  # noqa: E402
from core import PDF  # noqa: E402

fitz.TOOLS.mupdf_display_errors(False)
AREA = fitz.Rect(60, 190, 320, 250)          # PDF space: x 60..320, y 592..652
if ARGS.whole:
    AREA = fitz.Rect(0, 0, 595, 842)
SCRATCH = tempfile.TemporaryDirectory()  # saved files, removed on exit
HERE = SCRATCH.name
S = 'SECRET-XYZZY'


def forms(secret):
    b = secret.encode()
    u = secret.encode('utf-16-be')
    return [b, u, b.hex().encode(), u.hex().encode()]


def leaks(path, secret=S):
    """Where the secret is in the saved file: every object, every stream (raw and decoded), trailer, raw bytes."""
    found = []
    raw = open(path, 'rb').read()
    doc = fitz.open(path)
    blobs = {'trailer': doc.pdf_trailer().encode(), 'file bytes': raw}
    for x in range(1, doc.xref_length()):
        try:
            blobs[f'obj {x}'] = doc.xref_object(x).encode()
            if doc.xref_is_stream(x):
                blobs[f'stream {x}'] = (doc.xref_stream(x) or b'') + (doc.xref_stream_raw(x) or b'')
        except Exception as e:
            blobs[f'obj {x}'] = b''
    # every flate stream in the raw bytes, whether or not an xref entry reaches it
    for n, m in enumerate(re.finditer(rb'stream\r?\n', raw)):
        try:
            blobs[f'raw stream #{n}'] = zlib.decompressobj().decompress(raw[m.end():])
        except Exception:
            pass
    for name, blob in blobs.items():
        low = blob.lower()
        if any(f.lower() in low for f in forms(secret)):
            found.append(name)
    doc.close()
    return found


def add(doc, body, page=0, listed=True):
    """A hand-written annotation object; listed=False keeps it out of /Annots."""
    x = doc.get_new_xref()
    doc.update_object(x, body.replace('PAGE', f'{doc[page].xref} 0 R'))
    if listed:
        pg = doc[page].xref
        kind, val = doc.xref_get_key(pg, 'Annots')
        val = val if kind == 'array' else '[]'
        doc.xref_set_key(pg, 'Annots', val[:-1] + f' {x} 0 R]')
    return x


def stream(doc, data, extra=''):
    x = doc.get_new_xref()
    doc.update_object(x, f'<<{extra}>>')
    doc.update_stream(x, data)
    return x


CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


def run(name, fn):
    pdf = PDF()
    pdf.doc.new_page()
    pdf.doc.new_page()
    try:
        prepared = fn(pdf, pdf.doc)
    except Exception as e:
        print(f'{name:44s} BUILD ERROR {e!r}')
        return False
    # reopen from bytes, as the app does with a file from another tool
    if prepared != 'noreload':
        pdf.restore(pdf.doc.tobytes())
    try:
        removed = pdf.redact(0, AREA)
    except Exception as e:
        print(f'{name:44s} REDACT ERROR {e!r}')
        return False
    out = os.path.join(tempfile.mkdtemp(dir=HERE), 'o.pdf')
    pdf.save(out)
    where = leaks(out)
    print(f'{name:44s} removed={removed}  secret in saved file: {"YES " + ", ".join(where[:4]) if where else "no"}')
    return not where


NOTE = '<</Type/Annot/Subtype/Text/P PAGE/Rect[%s]/Contents(SECRET-XYZZY)/T(a)>>'


@case
def note_zero_area_point(pdf, doc): add(doc, NOTE % '200 622 200 622')
@case
def line_zero_height(pdf, doc): add(doc, '<</Type/Annot/Subtype/Line/P PAGE/Rect[100 622 280 622]/L[100 622 280 622]/Contents(SECRET-XYZZY)>>')
@case
def line_zero_width(pdf, doc): add(doc, '<</Type/Annot/Subtype/Line/P PAGE/Rect[200 600 200 640]/L[200 600 200 640]/Contents(SECRET-XYZZY)>>')
@case
def note_inverted_rect(pdf, doc): add(doc, NOTE % '220 640 200 620')
@case
def note_rect_just_outside_icon_inside(pdf, doc): add(doc, NOTE % '322 620 323 621')
@case
def note_rect_indirect_numbers(pdf, doc):
    a = doc.get_new_xref(); doc.update_object(a, '200')
    b = doc.get_new_xref(); doc.update_object(b, '620')
    add(doc, NOTE % f'{a} 0 R {b} 0 R 220 640')
@case
def note_rect_indirect_array(pdf, doc):
    a = doc.get_new_xref(); doc.update_object(a, '[200 620 220 640]')
    add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect %d 0 R/Contents(SECRET-XYZZY)>>' % a)
@case
def note_rect_reals_and_signs(pdf, doc): add(doc, NOTE % '+200. 620.0 .22e3 640')
@case
def square_rect_three_numbers(pdf, doc): add(doc, '<</Type/Annot/Subtype/Square/P PAGE/Rect[100 600 300]/Contents(SECRET-XYZZY)>>')
@case
def note_missing_rect(pdf, doc): add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Contents(SECRET-XYZZY)>>')
@case
def line_missing_rect_geometry_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Line/P PAGE/L[100 622 280 622]/Contents(SECRET-XYZZY)>>')
@case
def line_rect_elsewhere_geometry_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Line/P PAGE/Rect[400 100 500 110]/L[100 622 280 622]/Contents(SECRET-XYZZY)>>')
@case
def highlight_rect_elsewhere_quads_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Highlight/P PAGE/Rect[400 100 500 110]/QuadPoints[100 630 280 630 100 610 280 610]/Contents(SECRET-XYZZY)>>')
@case
def ink_rect_elsewhere_inklist_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Ink/P PAGE/Rect[400 100 500 110]/InkList[[100 610 200 630 280 615]]/Contents(SECRET-XYZZY)>>')
@case
def polygon_rect_elsewhere_vertices_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Polygon/P PAGE/Rect[400 100 500 110]/Vertices[100 610 200 630 280 615]/Contents(SECRET-XYZZY)>>')
@case
def freetext_callout_line_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/FreeText/P PAGE/Rect[400 100 500 130]/CL[200 620 380 200 400 115]/DA(/Helv 10 Tf 0 g)/Contents(SECRET-XYZZY)>>')
@case
def note_off_page_rect(pdf, doc): add(doc, NOTE % '2000 2000 2020 2020')
@case
def note_hidden_flag(pdf, doc): add(doc, '<</Type/Annot/Subtype/Text/P PAGE/F 2/Rect[200 620 220 640]/Contents(SECRET-XYZZY)>>')
@case
def note_noview_flag(pdf, doc): add(doc, '<</Type/Annot/Subtype/Text/P PAGE/F 32/Rect[200 620 220 640]/Contents(SECRET-XYZZY)>>')
@case
def unknown_subtype(pdf, doc): add(doc, '<</Type/Annot/Subtype/Foo/P PAGE/Rect[200 620 220 640]/Contents(SECRET-XYZZY)>>')
@case
def no_subtype(pdf, doc): add(doc, '<</Type/Annot/P PAGE/Rect[200 620 220 640]/Contents(SECRET-XYZZY)>>')
@case
def direct_dict_in_annots(pdf, doc):
    doc.xref_set_key(doc[0].xref, 'Annots', '[<</Type/Annot/Subtype/Text/Rect[200 620 220 640]/Contents(SECRET-XYZZY)>>]')
@case
def link_uri_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Link/P PAGE/Rect[200 620 280 640]/A<</S/URI/URI(mailto:SECRET-XYZZY@example.com)>>>>')
@case
def link_zero_area_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Link/P PAGE/Rect[200 620 200 620]/A<</S/URI/URI(mailto:SECRET-XYZZY@example.com)>>>>')
@case
def fileattachment_inside(pdf, doc):
    f = stream(doc, b'attached SECRET-XYZZY bytes', '/Type/EmbeddedFile')
    add(doc, '<</Type/Annot/Subtype/FileAttachment/P PAGE/Rect[200 620 220 640]/FS<</Type/Filespec/F(a.txt)/EF<</F %d 0 R>>>>>>' % f)
@case
def fileattachment_point_rect(pdf, doc):
    f = stream(doc, b'attached SECRET-XYZZY bytes', '/Type/EmbeddedFile')
    add(doc, '<</Type/Annot/Subtype/FileAttachment/P PAGE/Rect[200 622 200 622]/FS<</Type/Filespec/F(a.txt)/EF<</F %d 0 R>>>>>>' % f)

# --- popups -----------------------------------------------------------------
@case
def popup_inside_parent_outside_own_contents(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(kept note)>>')
    p = add(doc, '<</Type/Annot/Subtype/Popup/P PAGE/Rect[100 600 300 640]/Parent %d 0 R/Contents(SECRET-XYZZY)>>' % n)
    doc.xref_set_key(n, 'Popup', f'{p} 0 R')
@case
def popup_inside_parent_outside_secret_on_parent(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(SECRET-XYZZY)>>')
    p = add(doc, '<</Type/Annot/Subtype/Popup/P PAGE/Rect[100 600 300 640]/Parent %d 0 R/Open true>>' % n)
    doc.xref_set_key(n, 'Popup', f'{p} 0 R')
@case
def orphan_popup_inside(pdf, doc): add(doc, '<</Type/Annot/Subtype/Popup/P PAGE/Rect[100 600 300 640]/Contents(SECRET-XYZZY)/T(a)>>')
@case
def note_inside_popup_unlisted(pdf, doc):
    n = add(doc, NOTE % '200 620 220 640')
    p = add(doc, '<</Type/Annot/Subtype/Popup/P PAGE/Rect[400 100 500 200]/Parent %d 0 R/Contents(SECRET-XYZZY)>>' % n, listed=False)
    doc.xref_set_key(n, 'Popup', f'{p} 0 R')
@case
def note_inside_popup_unlisted_and_tagged(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(x)>>')
    p = add(doc, '<</Type/Annot/Subtype/Popup/P PAGE/Rect[400 100 500 200]/Parent %d 0 R/Contents(SECRET-XYZZY)>>' % n, listed=False)
    doc.xref_set_key(n, 'Popup', f'{p} 0 R')
    tag(doc, p)
@case
def note_outside_unlisted_popup_inside(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(kept)>>')
    p = add(doc, '<</Type/Annot/Subtype/Popup/P PAGE/Rect[100 600 300 640]/Parent %d 0 R/Contents(SECRET-XYZZY)>>' % n, listed=False)
    doc.xref_set_key(n, 'Popup', f'{p} 0 R')

# --- replies ----------------------------------------------------------------
@case
def reply_outside_to_unlisted_note_inside(pdf, doc):
    hidden = add(doc, NOTE % '200 620 220 640', listed=False)
    add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(reply)/IRT %d 0 R>>' % hidden)
@case
def unlisted_reply_to_note_inside_tagged(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(x)>>')
    r = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(SECRET-XYZZY)/IRT %d 0 R>>' % n, listed=False)
    tag(doc, r)
@case
def reply_on_other_page_to_note_inside(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(x)>>')
    add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(SECRET-XYZZY)/IRT %d 0 R>>' % n, page=1)
@case
def reply_chain_three_deep_outside(pdf, doc):
    n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(x)>>')
    for i in range(3):
        n = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 %d 420 %d]/Contents(SECRET-XYZZY)/IRT %d 0 R>>' % (100 + 30 * i, 120 + 30 * i, n))
@case
def reply_cycle(pdf, doc):
    a = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(SECRET-XYZZY)>>')
    b = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(SECRET-XYZZY)/IRT %d 0 R>>' % a)
    doc.xref_set_key(a, 'IRT', f'{b} 0 R')

# --- appearance streams and rich text -----------------------------------------
@case
def appearance_stream_repeats_content(pdf, doc):
    ap = stream(doc, b'BT /Helv 10 Tf (SECRET-XYZZY) Tj ET', '/Type/XObject/Subtype/Form/BBox[0 0 100 20]')
    add(doc, '<</Type/Annot/Subtype/FreeText/P PAGE/Rect[100 610 300 640]/DA(/Helv 10 Tf 0 g)/Contents(x)/AP<</N %d 0 R>>>>' % ap)
@case
def appearance_stream_shared_with_kept_annot(pdf, doc):
    ap = stream(doc, b'BT /Helv 10 Tf (SECRET-XYZZY) Tj ET', '/Type/XObject/Subtype/Form/BBox[0 0 100 20]')
    add(doc, '<</Type/Annot/Subtype/Stamp/P PAGE/Rect[100 610 300 640]/Contents(x)/AP<</N %d 0 R>>>>' % ap)
    add(doc, '<</Type/Annot/Subtype/Stamp/P PAGE/Rect[400 100 500 120]/Contents(y)/AP<</N %d 0 R>>>>' % ap)
@case
def appearance_states_and_rollover(pdf, doc):
    ap = [stream(doc, b'BT /Helv 10 Tf (SECRET-XYZZY) Tj ET', '/Type/XObject/Subtype/Form/BBox[0 0 100 20]') for _ in range(3)]
    add(doc, '<</Type/Annot/Subtype/Stamp/P PAGE/Rect[100 610 300 640]/AP<</N<</On %d 0 R>>/R %d 0 R/D %d 0 R>>/AS/On>>' % tuple(ap))
@case
def appearance_stream_tagged(pdf, doc):
    ap = stream(doc, b'BT /Helv 10 Tf (SECRET-XYZZY) Tj ET', '/Type/XObject/Subtype/Form/BBox[0 0 100 20]')
    x = add(doc, '<</Type/Annot/Subtype/Stamp/P PAGE/Rect[100 610 300 640]/AP<</N %d 0 R>>>>' % ap)
    tag(doc, x)
@case
def rich_text_rc(pdf, doc): add(doc, '<</Type/Annot/Subtype/FreeText/P PAGE/Rect[100 610 300 640]/DA(/Helv 10 Tf 0 g)/Contents(x)/RC(<body><p>SECRET-XYZZY</p></body>)/DS(font: 10pt)>>')
@case
def rich_text_rc_stream(pdf, doc):
    rc = stream(doc, b'<body><p>SECRET-XYZZY</p></body>')
    add(doc, '<</Type/Annot/Subtype/FreeText/P PAGE/Rect[100 610 300 640]/DA(/Helv 10 Tf 0 g)/Contents(x)/RC %d 0 R>>' % rc)
@case
def contents_indirect_string(pdf, doc):
    s = doc.get_new_xref(); doc.update_object(s, '(SECRET-XYZZY)')
    add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents %d 0 R>>' % s)
@case
def contents_shared_indirect_string_tagged(pdf, doc):
    s = doc.get_new_xref(); doc.update_object(s, '(SECRET-XYZZY)')
    x = add(doc, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents %d 0 R>>' % s)
    tag(doc, x)

# --- fields -------------------------------------------------------------------
def widget(doc, rect, extra, page=0):
    return add(doc, '<</Type/Annot/Subtype/Widget/P PAGE/Rect[%s]%s>>' % (rect, extra), page=page)

def form(doc, fields):
    doc.xref_set_key(doc.pdf_catalog(), 'AcroForm', '<</Fields[%s]/DA(/Helv 0 Tf 0 g)>>' % ' '.join(f'{f} 0 R' for f in fields))

def tag(doc, x):
    root, elem = doc.get_new_xref(), doc.get_new_xref()
    doc.update_object(elem, f'<</Type/StructElem/S/Annot/P {root} 0 R/Pg {doc[0].xref} 0 R/K<</Type/OBJR/Obj {x} 0 R>>>>')
    doc.update_object(root, f'<</Type/StructTreeRoot/K[{elem} 0 R]>>')
    doc.xref_set_key(doc.pdf_catalog(), 'StructTreeRoot', f'{root} 0 R')

@case
def widget_zero_area_inside(pdf, doc): form(doc, [widget(doc, '200 622 200 622', '/FT/Tx/T(h)/V(SECRET-XYZZY)')])
@case
def widget_zero_rect_at_origin(pdf, doc): form(doc, [widget(doc, '0 0 0 0', '/FT/Tx/T(h)/V(SECRET-XYZZY)/F 2')])
@case
def widget_missing_rect(pdf, doc): form(doc, [widget(doc, '', '/FT/Tx/T(h)/V(SECRET-XYZZY)').__int__()]) if False else form(doc, [add(doc, '<</Type/Annot/Subtype/Widget/P PAGE/FT/Tx/T(h)/V(SECRET-XYZZY)>>')])
@case
def widget_inverted_rect(pdf, doc): form(doc, [widget(doc, '300 640 100 610', '/FT/Tx/T(h)/V(SECRET-XYZZY)')])
@case
def widget_hidden_inside(pdf, doc): form(doc, [widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V(SECRET-XYZZY)/F 2')])
@case
def widget_rich_value_rv_and_default(pdf, doc): form(doc, [widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V(x)/RV(<p>SECRET-XYZZY</p>)/DV(SECRET-XYZZY)/TU(SECRET-XYZZY)')])
@case
def widget_value_indirect_stream(pdf, doc):
    v = stream(doc, b'SECRET-XYZZY long value')
    form(doc, [widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V %d 0 R' % v)])
@case
def widget_value_stream_also_in_co(pdf, doc):
    v = stream(doc, b'SECRET-XYZZY long value')
    w = widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V %d 0 R' % v)
    form(doc, [w]); doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/CO', f'[{w} 0 R]')
@case
def widget_not_in_fields(pdf, doc): widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V(SECRET-XYZZY)')
@case
def widget_js_action_holds_value(pdf, doc): form(doc, [widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V(x)/AA<</K<</S/JavaScript/JS(var v="SECRET-XYZZY")>>>>')])
@case
def field_two_widgets_same_page_one_inside(pdf, doc):
    p = doc.get_new_xref()
    a = widget(doc, '100 610 300 640', '/Parent %d 0 R' % p); b = widget(doc, '100 100 300 130', '/Parent %d 0 R' % p)
    doc.update_object(p, '<</FT/Tx/T(two)/V(SECRET-XYZZY)/Kids[%d 0 R %d 0 R]>>' % (a, b)); form(doc, [p])
@case
def field_widgets_two_pages_one_inside(pdf, doc):
    p = doc.get_new_xref()
    a = widget(doc, '100 610 300 640', '/Parent %d 0 R' % p); b = widget(doc, '100 610 300 640', '/Parent %d 0 R' % p, page=1)
    doc.update_object(p, '<</FT/Tx/T(two)/V(SECRET-XYZZY)/Kids[%d 0 R %d 0 R]>>' % (a, b)); form(doc, [p])
@case
def field_widgets_two_pages_other_hidden(pdf, doc):
    p = doc.get_new_xref()
    a = widget(doc, '100 610 300 640', '/Parent %d 0 R' % p); b = widget(doc, '0 0 0 0', '/F 2/Parent %d 0 R' % p, page=1)
    doc.update_object(p, '<</FT/Tx/T(two)/V(SECRET-XYZZY)/Kids[%d 0 R %d 0 R]>>' % (a, b)); form(doc, [p])
@case
def field_kid_is_valueless_nonwidget_node(pdf, doc):
    top, mid = doc.get_new_xref(), doc.get_new_xref()
    a = widget(doc, '100 610 300 640', '/Parent %d 0 R' % mid)
    doc.update_object(mid, '<</T(mid)/Parent %d 0 R/Kids[%d 0 R]>>' % (top, a))
    doc.update_object(top, '<</FT/Tx/T(top)/V(SECRET-XYZZY)/Kids[%d 0 R]>>' % mid); form(doc, [top])
@case
def radio_group_all_inside(pdf, doc):
    p = doc.get_new_xref(); kids = []
    for i in range(2):
        on = stream(doc, b'q Q', '/Type/XObject/Subtype/Form/BBox[0 0 10 10]'); off = stream(doc, b'q Q', '/Type/XObject/Subtype/Form/BBox[0 0 10 10]')
        kids.append(widget(doc, '%d 620 %d 630' % (100 + 30 * i, 110 + 30 * i), '/Parent %d 0 R/AS/Off/AP<</N<</SECRET-XYZZY%d %d 0 R/Off %d 0 R>>>>' % (p, i, on, off)))
    doc.update_object(p, '<</FT/Btn/Ff 32768/T(radio)/V/SECRET-XYZZY0/Opt[(SECRET-XYZZY)(b)]/Kids[%s]>>' % ' '.join(f'{k} 0 R' for k in kids)); form(doc, [p])
@case
def radio_group_one_inside(pdf, doc):
    p = doc.get_new_xref(); kids = []
    for i in range(2):
        on = stream(doc, b'q Q', '/Type/XObject/Subtype/Form/BBox[0 0 10 10]'); off = stream(doc, b'q Q', '/Type/XObject/Subtype/Form/BBox[0 0 10 10]')
        kids.append(widget(doc, '100 %d 110 %d' % (620 - 500 * i, 630 - 500 * i), '/Parent %d 0 R/AS/Off/AP<</N<</Choice%d %d 0 R/Off %d 0 R>>>>' % (p, i, on, off)))
    doc.update_object(p, '<</FT/Btn/Ff 32768/T(radio)/V/Choice0/Opt[(SECRET-XYZZY)(b)]/Kids[%s]>>' % ' '.join(f'{k} 0 R' for k in kids)); form(doc, [p])
@case
def field_in_acroform_indirect_with_xfa_array(pdf, doc):
    w = widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V(x)')
    d = stream(doc, b'<h>SECRET-XYZZY</h>')
    f = doc.get_new_xref(); doc.update_object(f, '<</Fields[%d 0 R]/XFA[(datasets) %d 0 R]>>' % (w, d))
    doc.xref_set_key(doc.pdf_catalog(), 'AcroForm', f'{f} 0 R')
@case
def field_fields_array_indirect(pdf, doc):
    w = widget(doc, '100 610 300 640', '/FT/Tx/T(h)/V(SECRET-XYZZY)')
    arr = doc.get_new_xref(); doc.update_object(arr, '[%d 0 R]' % w)
    doc.xref_set_key(doc.pdf_catalog(), 'AcroForm', '<</Fields %d 0 R>>' % arr)

# --- file history -------------------------------------------------------------
@case
def incremental_history_in_source_file(pdf, doc):
    # Written by another tool: note saved, then its text changed in an incremental update.
    src = os.path.join(tempfile.mkdtemp(dir=HERE), 'src.pdf')
    d = fitz.open(); d.new_page(); d.new_page()
    x = add(d, '<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(SECRET-XYZZY first revision)>>')
    d.save(src); d.close()
    d = fitz.open(src); d.xref_set_key(x, 'Contents', '(second revision)'); d.save(src, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP); d.close()
    assert leaks(src), 'history should hold the secret before redaction'
    pdf.open(src) if hasattr(pdf, 'open') else pdf.restore(open(src, 'rb').read())
    return 'noreload'
@case
def object_stream_source_file(pdf, doc):
    src = os.path.join(tempfile.mkdtemp(dir=HERE), 'src.pdf')
    d = fitz.open(); d.new_page(); d.new_page()
    add(d, NOTE % '200 620 220 640'); d.save(src, use_objstms=1, deflate=True); d.close()
    pdf.restore(open(src, 'rb').read())
    return 'noreload'


if __name__ == '__main__':
    clean = total = 0
    for name, fn in CASES.items():
        if not ARGS.names or any(w in name for w in ARGS.names):
            total += 1
            clean += run(name, fn)
    print(f'{clean} of {total} inputs clean')
