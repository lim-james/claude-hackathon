"""Redaction removes form fields and comments under the area, not only page content."""
import re
import tempfile
import unittest
from pathlib import Path
import fitz
from core import PDF, removed_summary
from pen_tools import add_stroke
from rich_boxes import purge_overlays

AREA = fitz.Rect(60, 190, 320, 250)
INSIDE = fitz.Rect(72, 200, 300, 240)
OUTSIDE = fitz.Rect(72, 400, 300, 440)


class RedactionOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = PDF()
        self.pdf.doc.new_page()

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def export(self):
        path = self.root / 'redacted.pdf'
        self.pdf.save(path)
        return fitz.open(path)

    def has(self, doc, secret):
        # The secret in any object or stream, as literal, UTF-16 or hex string.
        forms = (secret.encode(), secret.encode('utf-16-be'),
                 secret.encode('utf-16-be').hex().encode(), secret.encode().hex().encode())
        blobs = [doc.xref_object(x).encode() + (doc.xref_stream(x) or b'')
                 for x in range(1, doc.xref_length())]
        return any(form.lower() in blob.lower() for blob in blobs for form in forms)

    def assert_clean(self, *secrets):
        with self.export() as doc:
            page = doc[0]
            self.assertEqual([w.field_name for w in page.widgets() if w.rect.intersects(AREA)], [])
            self.assertEqual([a.type[1] for a in page.annots() if a.rect.intersects(AREA)], [])
            for secret in secrets:
                self.assertFalse(self.has(doc, secret), secret)
            return page.get_text()

    def test_issue_script_field_and_note_are_gone(self):
        self.pdf.doc[0].insert_text((72, 215), 'PAGE-TEXT-SECRET', fontsize=12)
        self.pdf.add_field(0, fitz.Rect(72, 220, 300, 245), 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET-123-45-6789')
        self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
        self.pdf.redact(0, AREA)
        text = self.assert_clean('FIELD-SECRET', 'NOTE-SECRET', 'PAGE-TEXT-SECRET')
        self.assertEqual(text.strip(), '')
        with self.export() as doc:
            self.assertEqual(doc[0].search_for('FIELD-SECRET'), [])
            self.assertEqual(list(doc[0].widgets()), [])
            self.assertEqual(list(doc[0].annots()), [])

    def test_every_field_kind_is_removed(self):
        cases = {'text': ('TEXT-SECRET', None), 'checkbox': ('yes', None),
                 'dropdown': ('DROP-SECRET', ['DROP-SECRET', 'DROP-OTHER']),
                 'listbox': ('LIST-SECRET', ['LIST-SECRET', 'LIST-OTHER']),
                 'signature': (None, None)}
        self.assertEqual(set(cases), set(PDF.FIELD_TYPES))
        for kind, (value, choices) in cases.items():
            with self.subTest(kind=kind):
                self.pdf.restore(self.blank())
                name = f'FIELDNAME-{kind}'
                self.pdf.add_field(0, INSIDE, name, kind, choices)
                if value:
                    self.pdf.fill_field(0, name, value)
                self.pdf.redact(0, AREA)
                self.assert_clean(name, *(choices or []), *([value] if kind == 'text' else []))
                self.assertEqual(self.pdf.fields(), [])
                catalog = self.pdf.doc.pdf_catalog()
                self.assertEqual(self.pdf.doc.xref_get_key(catalog, 'AcroForm/Fields')[1], '[]')

    def blank(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        return data

    def test_every_annotation_kind_is_removed(self):
        def markup(kind):
            def make():
                page = self.pdf.doc[0]
                page.insert_text((80, 220), 'marked words')
                annot = self.pdf.markup(0, page.search_for('marked words', quads=True), kind, 'AUTHOR-SECRET')
                self.pdf.doc.xref_set_key(annot.xref, 'Contents', '(ANNOT-SECRET)')  # its comment
            return make
        makers = {kind: markup(kind) for kind in PDF.MARKUP}
        makers.update({
            'note': lambda: self.pdf.note(0, fitz.Point(200, 195), 'ANNOT-SECRET', 'AUTHOR-SECRET'),
            'callout': lambda: self.pdf.callout(0, INSIDE, 'ANNOT-SECRET', 'AUTHOR-SECRET'),
            'stamp': lambda: self.pdf.stamp(0, INSIDE, 2, 'AUTHOR-SECRET'),
            'ink': lambda: add_stroke(self.pdf.doc[0], [(80, 200), (120, 230), (200, 210)]),
        })
        for shape in ('line', 'arrow', 'rectangle', 'oval'):
            makers[shape] = lambda shape=shape: self.pdf.shape(0, shape, INSIDE, author='AUTHOR-SECRET')
        for kind, make in makers.items():
            with self.subTest(kind=kind):
                self.pdf.restore(self.blank())
                make()
                self.assertEqual(len(self.pdf.annotations()), 1)
                self.pdf.redact(0, AREA)
                self.assert_clean('ANNOT-SECRET', 'AUTHOR-SECRET', 'Freehand pen')
                self.assertEqual(self.pdf.annotations(), [])

    def test_reply_to_a_redacted_note_is_removed(self):
        note = self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
        reply = self.pdf.note(0, fitz.Point(200, 500), 'REPLY-SECRET')  # sits outside the area
        self.pdf.doc.xref_set_key(reply.xref, 'IRT', f'{note.xref} 0 R')
        self.pdf.redact(0, AREA)
        self.assert_clean('NOTE-SECRET', 'REPLY-SECRET')
        self.assertEqual(self.pdf.annotations(), [])

    def test_partial_overlap_counts_as_inside(self):
        self.pdf.add_field(0, fitz.Rect(300, 240, 500, 300), 'edge')  # only a corner is inside
        self.pdf.fill_field(0, 'edge', 'EDGE-FIELD-SECRET')
        self.pdf.callout(0, fitz.Rect(10, 100, 70, 200), 'EDGE-NOTE-SECRET')
        self.pdf.redact(0, AREA)
        self.assert_clean('EDGE-FIELD-SECRET', 'EDGE-NOTE-SECRET')
        self.assertEqual(self.pdf.fields(), [])
        self.assertEqual(self.pdf.annotations(), [])

    def test_field_and_note_outside_the_area_are_kept(self):
        self.pdf.add_field(0, INSIDE, 'gone')
        self.pdf.fill_field(0, 'gone', 'FIELD-SECRET')
        self.pdf.add_field(0, OUTSIDE, 'kept')
        self.pdf.fill_field(0, 'kept', 'KEPT-VALUE')
        self.pdf.note(0, fitz.Point(200, 500), 'KEPT-NOTE')
        self.pdf.redact(0, AREA)
        with self.export() as doc:
            self.assertEqual([(w.field_name, w.field_value) for w in doc[0].widgets()],
                             [('kept', 'KEPT-VALUE')])
            self.assertEqual([a.info['content'] for a in doc[0].annots()], ['KEPT-NOTE'])
            self.assertFalse(self.has(doc, 'FIELD-SECRET'))

    def test_value_held_on_a_parent_field_is_removed_with_its_last_widget(self):
        # A field whose /V lives on a parent, as radio groups and multi-widget fields do.
        doc = self.pdf.doc
        self.pdf.add_field(0, INSIDE, 'first')
        self.pdf.add_field(0, OUTSIDE, 'second')
        kids = [w.xref for w in doc[0].widgets()]
        parent = doc.get_new_xref()
        doc.update_object(parent, '<</FT/Tx/T(group)/V(PARENT-SECRET)/Kids[%d 0 R %d 0 R]>>' % tuple(kids))
        for kid in kids:
            doc.xref_set_key(kid, 'Parent', f'{parent} 0 R')
            doc.xref_set_key(kid, 'T', 'null')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/Fields', f'[{parent} 0 R]')
        self.pdf.redact(0, AREA)
        # One widget is left outside the area, so the shared field and value stay.
        self.assertEqual([(name, value) for _, name, value, _, _ in self.pdf.fields()],
                         [('group', 'PARENT-SECRET')])
        self.pdf.redact(0, OUTSIDE)
        self.assertEqual(self.pdf.fields(), [])
        self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/Fields')[1], '[]')
        with self.export() as saved:
            self.assertFalse(self.has(saved, 'PARENT-SECRET'))

    def test_redacted_field_is_absent_from_form_data_export(self):
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.add_field(0, OUTSIDE, 'kept')
        self.pdf.fill_field(0, 'kept', 'KEPT-VALUE')
        self.pdf.redact(0, AREA)
        path = self.root / 'form.xfdf'
        self.pdf.export_form_data(path)
        data = path.read_text(encoding='utf-8')
        self.assertNotIn('FIELD-SECRET', data)
        self.assertNotIn('ssn', data)
        self.assertIn('KEPT-VALUE', data)

    def test_selected_text_redaction_removes_field_and_note_over_the_text(self):
        page = self.pdf.doc[0]
        page.insert_text((80, 225), 'SELECTED-SECRET stays-public')
        quads = page.search_for('SELECTED-SECRET', quads=True)
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.note(0, fitz.Point(90, 215), 'NOTE-SECRET')
        self.pdf.redact_quads(0, quads)
        text = self.assert_clean('FIELD-SECRET', 'NOTE-SECRET', 'SELECTED-SECRET')
        self.assertIn('stays-public', text)
        self.assertEqual(self.pdf.fields(), [])
        self.assertEqual(self.pdf.annotations(), [])

    def test_undo_snapshot_brings_field_and_note_back(self):
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
        before = self.pdf.bytes()  # what App.change keeps for undo
        self.pdf.redact(0, AREA)
        self.assertEqual(self.pdf.fields(), [])
        self.pdf.restore(before)
        self.assertEqual([(name, value) for _, name, value, _, _ in self.pdf.fields()],
                         [('ssn', 'FIELD-SECRET')])
        self.assertEqual([a['text'] for a in self.pdf.annotations()], ['NOTE-SECRET'])

    def tag(self, xref, kind):
        # A tagged PDF: the structure tree points at the object through an OBJR.
        doc = self.pdf.doc
        root, elem = doc.get_new_xref(), doc.get_new_xref()
        doc.update_object(elem, f'<</Type/StructElem/S/{kind}/P {root} 0 R/Pg {doc[0].xref} 0 R'
                                f'/K<</Type/OBJR/Obj {xref} 0 R>>>>')
        doc.update_object(root, f'<</Type/StructTreeRoot/K[{elem} 0 R]>>')
        doc.xref_set_key(doc.pdf_catalog(), 'StructTreeRoot', f'{root} 0 R')
        return elem

    def test_tagged_pdf_field_and_note_leave_no_value_behind(self):
        doc = self.pdf.doc
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        field = self.tag(next(doc[0].widgets()).xref, 'Form')
        self.pdf.redact(0, AREA)
        note = self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET', 'AUTHOR-SECRET')
        annot = self.tag(note.xref, 'Annot')
        self.pdf.redact(0, AREA)
        for elem in (field, annot):
            self.assertNotIn('OBJR', doc.xref_object(elem))
        self.assert_clean('FIELD-SECRET', 'NOTE-SECRET', 'AUTHOR-SECRET', 'ssn')

    def test_field_in_the_calculation_order_leaves_no_value_behind(self):
        doc = self.pdf.doc
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.add_field(0, OUTSIDE, 'kept')
        gone, kept = [w.xref for w in doc[0].widgets()]
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/CO', f'[{gone} 0 R {kept} 0 R]')
        self.pdf.redact(0, AREA)
        self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/CO')[1].replace(' ', ''), f'[{kept}0R]')
        self.assert_clean('FIELD-SECRET', 'ssn')

    def test_xfa_copy_of_the_form_is_dropped_with_a_redacted_field(self):
        doc = self.pdf.doc
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        xfa = doc.get_new_xref()
        doc.update_object(xfa, '<<>>')
        doc.update_stream(xfa, b'<xfa:data><ssn>XFA-SECRET</ssn></xfa:data>')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/XFA', f'{xfa} 0 R')
        self.pdf.redact(0, fitz.Rect(400, 600, 500, 700))  # nothing under it: the form is left alone
        self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/XFA')[0], 'xref')
        self.pdf.redact(0, AREA)
        self.assert_clean('FIELD-SECRET', 'XFA-SECRET')

    def test_sibling_reference_with_a_generation_number_is_kept(self):
        doc = self.pdf.doc
        self.pdf.add_field(0, INSIDE, 'gone')
        self.pdf.add_field(0, OUTSIDE, 'kept')
        gone, kept = [w.xref for w in doc[0].widgets()]
        first, second = doc.get_new_xref(), doc.get_new_xref()
        doc.update_object(first, f'<</FT/Tx/T(a)/Kids[{gone} 0 R 9999 1 R]>>')
        doc.update_object(second, f'<</FT/Tx/T(b)/Kids[{kept} 0 R]>>')
        doc.xref_set_key(gone, 'Parent', f'{first} 0 R')
        doc.xref_set_key(kept, 'Parent', f'{second} 0 R')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/Fields', f'[{first} 0 R 9998 2 R {second} 0 R]')
        fitz.TOOLS.mupdf_display_errors(False)  # the made-up siblings do not exist
        self.addCleanup(fitz.TOOLS.mupdf_display_errors, True)
        refs = lambda xref, key: re.findall(r'\d+ \d+ R', doc.xref_get_key(xref, key)[1])
        self.pdf.redact(0, AREA)
        self.assertEqual(refs(first, 'Kids'), ['9999 1 R'])
        self.pdf.redact(0, OUTSIDE)
        self.assertEqual(refs(doc.pdf_catalog(), 'AcroForm/Fields'), [f'{first} 0 R', '9998 2 R'])

    def test_rotated_and_cropped_pages_use_the_same_area_as_the_screen(self):
        for rotation, crop in ((90, None), (180, fitz.Rect(20, 30, 500, 700)), (270, None)):
            with self.subTest(rotation=rotation):
                self.pdf.restore(self.blank())
                page = self.pdf.doc[0]
                if crop:
                    page.set_cropbox(crop)
                page.set_rotation(rotation)
                self.pdf.add_field(0, INSIDE, 'ssn')
                self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
                self.pdf.add_field(0, OUTSIDE, 'kept')
                self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
                self.pdf.note(0, fitz.Point(200, 500), 'KEPT-NOTE')
                self.assertEqual(self.pdf.redact(0, AREA), (1, 1))
                self.assertEqual([name for _, name, *_ in self.pdf.fields()], ['kept'])
                self.assertEqual([a['text'] for a in self.pdf.annotations()], ['KEPT-NOTE'])

    def test_redaction_reports_what_it_removed(self):
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.add_field(0, fitz.Rect(300, 240, 500, 300), 'edge')
        note = self.pdf.note(0, fitz.Point(200, 195), 'NOTE')
        reply = self.pdf.note(0, fitz.Point(200, 500), 'REPLY')
        self.pdf.doc.xref_set_key(reply.xref, 'IRT', f'{note.xref} 0 R')
        self.pdf.doc[0].insert_text((80, 600), 'plain words')
        self.assertEqual(self.pdf.redact(0, fitz.Rect(70, 590, 200, 610)), (0, 0))
        self.assertEqual(self.pdf.redact(0, AREA), (2, 2))  # popups are not counted as comments
        self.assertEqual(removed_summary(0, 0), '')
        self.assertEqual(removed_summary(1, 0), '1 form field under it was removed. ')
        self.assertEqual(removed_summary(2, 1), '2 form fields and 1 comment under it were removed. ')
        self.assertEqual(removed_summary(0, 3), '3 comments under it were removed. ')

    def test_loads_only_what_is_under_the_area(self):
        for n in range(30):
            self.pdf.add_field(0, fitz.Rect(72, 300 + n * 10, 300, 308 + n * 10), f'f{n}')
            self.pdf.note(0, fitz.Point(400, 300 + n * 10), f'n{n}')
        self.pdf.add_field(0, INSIDE, 'ssn')
        page = self.pdf.doc[0]
        loaded = []
        load_widget = page.load_widget
        page.load_widget = lambda xref: loaded.append(xref) or load_widget(xref)
        page.widgets = page.annots = lambda *a, **k: self.fail('walks every object on the page')
        page.add_redact_annot(AREA, fill=(0, 0, 0))
        self.assertEqual(purge_overlays(page), (1, 0))
        self.assertEqual(len(loaded), 1)

    def raw(self, body, page=0, listed=True):
        # An object as another tool writes it: no appearance stream, any /Rect.
        doc = self.pdf.doc
        xref = doc.get_new_xref()
        doc.update_object(xref, body.replace('PAGE', f'{doc[page].xref} 0 R'))
        if listed:
            kind, array = doc.xref_get_key(doc[page].xref, 'Annots')
            array = array if kind == 'array' else '[]'
            doc.xref_set_key(doc[page].xref, 'Annots', f'{array[:-1]} {xref} 0 R]')
        return xref

    def reopen(self):
        # As the app has it at the time of a redaction: read from a file and shown once.
        self.pdf.restore(self.pdf.doc.tobytes())
        self.pdf.doc[0].get_pixmap()

    def test_objects_other_tools_write_are_found_under_the_area(self):
        # AREA is x 60 to 320, y 592 to 652 in the PDF's own coordinates.
        note = '<</Type/Annot/Subtype/Text/P PAGE/Rect[%s]/Contents(RAW-SECRET)/T(AUTHOR-SECRET)>>'
        field = '<</Type/Annot/Subtype/Widget/P PAGE/Rect[%s]/FT/Tx/T(h)/V(RAW-SECRET)>>'
        cases = {
            'note at a point': (note % '200 622 200 622', (0, 1)),
            'note icon reaching into the area': (note % '322 620 323 621', (0, 1)),
            'note with its corners swapped': (note % '220 640 200 620', (0, 1)),
            'line with a flat rectangle': ('<</Type/Annot/Subtype/Line/P PAGE/Rect[100 622 280 622]'
                                           '/L[100 622 280 622]/Contents(RAW-SECRET)>>', (0, 1)),
            'line drawn away from its rectangle': ('<</Type/Annot/Subtype/Line/P PAGE/Rect[400 100 500 110]'
                                                   '/L[100 622 280 622]/Contents(RAW-SECRET)>>', (0, 1)),
            'ink drawn away from its rectangle': ('<</Type/Annot/Subtype/Ink/P PAGE/Rect[400 100 500 110]'
                                                  '/InkList[[100 610 200 630]]/Contents(RAW-SECRET)>>', (0, 1)),
            'subtype MuPDF does not know': ('<</Type/Annot/Subtype/Foo/P PAGE/Rect[200 620 220 640]'
                                            '/Contents(RAW-SECRET)>>', (0, 1)),
            'popup with no comment': ('<</Type/Annot/Subtype/Popup/P PAGE/Rect[100 600 300 640]'
                                      '/Contents(RAW-SECRET)>>', (0, 0)),
            'link at a point': ('<</Type/Annot/Subtype/Link/P PAGE/Rect[200 622 200 622]'
                                '/A<</S/URI/URI(mailto:RAW-SECRET@example.com)>>>>', (0, 0)),
            'field at a point': (field % '200 622 200 622', (1, 0)),
            'hidden field': (field % '100 610 300 640' + '/F 2', (1, 0)),
        }
        fitz.TOOLS.mupdf_display_errors(False)  # MuPDF says it cannot draw the unknown subtype
        self.addCleanup(fitz.TOOLS.mupdf_display_errors, True)
        for name, (body, removed) in cases.items():
            with self.subTest(name):
                self.pdf.restore(self.blank())
                self.raw(body)
                self.reopen()
                self.assertEqual(self.pdf.redact(0, AREA), removed)
                self.assert_clean('RAW-SECRET', 'AUTHOR-SECRET')
                self.assertEqual(self.pdf.doc[0].annot_xrefs(), [])
                # A second redaction in the session finds the page in order.
                self.assertEqual(self.pdf.redact(0, OUTSIDE), (0, 0))

    def test_objects_away_from_the_area_are_kept(self):
        note = '<</Type/Annot/Subtype/Text/P PAGE/Rect[%s]/Contents(KEPT)>>'
        for name, rect in (('point', '200 300 200 300'), ('off the page', '2000 2000 2020 2020'),
                           ('icon clear of the area', '350 620 351 621')):
            with self.subTest(name):
                self.pdf.restore(self.blank())
                self.raw(note % rect)
                self.raw('<</Type/Annot/Subtype/Widget/P PAGE/Rect[0 0 0 0]/F 2/FT/Tx/T(h)/V(KEPT)>>')
                self.reopen()
                self.assertEqual(self.pdf.redact(0, AREA), (0, 0))
                self.assertEqual(len(self.pdf.doc[0].annot_xrefs()), 2)

    def test_popup_under_the_area_goes_and_its_comment_stays(self):
        for listed in (True, False):  # in /Annots, or reachable only through /Popup
            with self.subTest(listed=listed):
                self.pdf.restore(self.blank())
                note = self.raw('<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(KEPT-NOTE)>>')
                popup = self.raw(f'<</Type/Annot/Subtype/Popup/P PAGE/Rect[100 600 300 640]/Parent {note} 0 R'
                                 '/Contents(POPUP-SECRET)>>', listed=listed)
                self.pdf.doc.xref_set_key(note, 'Popup', f'{popup} 0 R')
                self.reopen()
                self.assertEqual(self.pdf.redact(0, AREA), (0, 0))
                with self.export() as doc:
                    self.assertFalse(self.has(doc, 'POPUP-SECRET'))
                    self.assertEqual([(a.info['content'], a.popup_xref) for a in doc[0].annots()], [('KEPT-NOTE', 0)])

    def test_comment_reachable_only_through_a_reply_is_removed(self):
        hidden = self.raw('<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(NOTE-SECRET)>>',
                          listed=False)
        self.raw(f'<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(REPLY-SECRET)/IRT {hidden} 0 R>>')
        self.reopen()
        self.assertEqual(self.pdf.redact(0, AREA), (0, 2))
        self.assert_clean('NOTE-SECRET', 'REPLY-SECRET')

    def test_comments_that_answer_each_other_are_removed(self):
        first = self.raw('<</Type/Annot/Subtype/Text/P PAGE/Rect[200 620 220 640]/Contents(NOTE-SECRET)>>')
        second = self.raw(f'<</Type/Annot/Subtype/Text/P PAGE/Rect[400 100 420 120]/Contents(REPLY-SECRET)'
                          f'/IRT {first} 0 R>>')
        self.pdf.doc.xref_set_key(first, 'IRT', f'{second} 0 R')
        self.reopen()
        self.assertEqual(self.pdf.redact(0, AREA), (0, 2))
        self.assert_clean('NOTE-SECRET', 'REPLY-SECRET')

    def test_value_is_removed_when_the_widgets_left_show_nothing(self):
        doc = self.pdf.doc
        doc.new_page()
        parent = doc.get_new_xref()
        self.raw(f'<</Type/Annot/Subtype/Widget/P PAGE/Rect[100 610 300 640]/Parent {parent} 0 R>>')
        unseen = self.raw(f'<</Type/Annot/Subtype/Widget/P PAGE/Rect[0 0 0 0]/F 2/Parent {parent} 0 R>>', page=1)
        doc.update_object(parent, f'<</FT/Tx/T(two)/V(PARENT-SECRET)/Kids[{unseen - 1} 0 R {unseen} 0 R]>>')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm', f'<</Fields[{parent} 0 R]>>')
        self.reopen()
        self.assertEqual(self.pdf.redact(0, AREA), (1, 0))
        self.assert_clean('PARENT-SECRET')
        self.assertEqual(self.pdf.doc[1].annot_xrefs(), [])
        self.assertEqual(self.pdf.fields(), [])

    def test_text_edit_under_a_note_keeps_the_note(self):
        # replace_text uses a redact annotation only to delete glyphs.
        page = self.pdf.doc[0]
        page.insert_text((80, 225), 'draft-wording')
        self.pdf.note(0, page.search_for('draft-wording')[0].tl, 'KEPT-NOTE')
        self.pdf.add_field(0, INSIDE, 'kept')
        self.pdf.fill_field(0, 'kept', 'KEPT-VALUE')
        self.pdf.replace_text(0, 'draft-wording', 'final', 8)
        self.assertNotIn('draft-wording', self.pdf.doc[0].get_text())
        self.assertEqual([a['text'] for a in self.pdf.annotations()], ['KEPT-NOTE'])
        self.assertEqual([(name, value) for _, name, value, _, _ in self.pdf.fields()],
                         [('kept', 'KEPT-VALUE')])


if __name__ == '__main__':
    unittest.main()
