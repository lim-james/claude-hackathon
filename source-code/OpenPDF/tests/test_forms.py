import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from core import PDF


class FormFieldTests(unittest.TestCase):
    def setUp(self):
        self.pdf = PDF()
        self.pdf.doc.new_page(width=400, height=600)

    def tearDown(self):
        self.pdf.close()

    def field_map(self, pdf):
        return {name: (value, kind, choices) for _, name, value, kind, choices in pdf.fields()}

    def test_dropdown_and_listbox_round_trip(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'country', 'dropdown', ['USA', 'Canada', 'Singapore'])
        self.pdf.add_field(0, fitz.Rect(50, 100, 250, 180), 'fruit', 'listbox', ['Apple', 'Pear', 'Plum'])
        self.pdf.restore(self.pdf.bytes())
        fields = self.field_map(self.pdf)
        self.assertEqual(fields['country'][1], fitz.PDF_WIDGET_TYPE_COMBOBOX)
        self.assertEqual(fields['country'][2], ['USA', 'Canada', 'Singapore'])
        self.assertEqual(fields['fruit'][1], fitz.PDF_WIDGET_TYPE_LISTBOX)
        self.assertEqual(fields['country'][0], 'USA')  # defaults to first option

    def test_signature_field_created(self):
        self.pdf.add_field(0, fitz.Rect(50, 300, 250, 360), 'approve', 'signature')
        self.pdf.restore(self.pdf.bytes())
        self.assertEqual(self.field_map(self.pdf)['approve'][1], fitz.PDF_WIDGET_TYPE_SIGNATURE)

    def test_choice_validation_and_empty_options(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'pick', 'dropdown', ['A', 'B'])
        self.pdf.fill_field(0, 'pick', 'B')
        self.assertEqual(self.field_map(self.pdf)['pick'][0], 'B')
        with self.assertRaises(ValueError):
            self.pdf.fill_field(0, 'pick', 'Z')  # not an allowed option
        with self.assertRaises(ValueError):
            self.pdf.add_field(0, fitz.Rect(50, 200, 250, 230), 'empty', 'listbox', [])
        with self.assertRaises(ValueError):
            self.pdf.add_field(0, fitz.Rect(50, 250, 250, 280), 'bad', 'radio')

    def test_duplicate_name_rejected(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'dup', 'text')
        with self.assertRaises(ValueError):
            self.pdf.add_field(0, fitz.Rect(50, 100, 250, 130), 'dup', 'text')

    def test_xfdf_export_import_reapplies_values(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'name', 'text')
        self.pdf.add_field(0, fitz.Rect(50, 100, 70, 120), 'agree', 'checkbox')
        self.pdf.add_field(0, fitz.Rect(50, 150, 250, 180), 'country', 'dropdown', ['USA', 'Canada'])
        self.pdf.fill_field(0, 'name', 'Ada')
        self.pdf.fill_field(0, 'agree', 'yes')
        self.pdf.fill_field(0, 'country', 'Canada')
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'answers.xfdf'
            self.assertEqual(self.pdf.export_form_data(data), 3)

            # A fresh, blank copy of the same form.
            blank = PDF()
            try:
                blank.doc.new_page(width=400, height=600)
                blank.add_field(0, fitz.Rect(50, 50, 250, 80), 'name', 'text')
                blank.add_field(0, fitz.Rect(50, 100, 70, 120), 'agree', 'checkbox')
                blank.add_field(0, fitz.Rect(50, 150, 250, 180), 'country', 'dropdown', ['USA', 'Canada'])
                self.assertEqual(blank.import_form_data(data), 3)
                blank.restore(blank.bytes())
                fields = self.field_map(blank)
                self.assertEqual(fields['name'][0], 'Ada')
                self.assertEqual(fields['country'][0], 'Canada')
                self.assertNotEqual(fields['agree'][0], 'Off')
            finally:
                blank.close()

    def test_imports_namespaced_acrobat_xfdf(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'name', 'text')
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'acrobat.xfdf'
            data.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<xfdf xmlns="http://ns.adobe.com/xfdf/">'
                '<fields><field name="name"><value>Grace</value></field></fields></xfdf>')
            self.assertEqual(self.pdf.import_form_data(data), 1)
            self.assertEqual(self.field_map(self.pdf)['name'][0], 'Grace')

    ISSUE_66_XFDF = (
        '<xfdf xmlns="http://ns.adobe.com/xfdf/"><fields>'
        '<field name="agree"><value>No</value></field>'
        '<field name="newsletter"><value>false</value></field>'
        '<field name="plan"><value>Enterprise</value></field>'
        '</fields></xfdf>')

    def add_consent_form(self, pdf):
        pdf.add_field(0, fitz.Rect(50, 50, 70, 70), 'agree', 'checkbox')
        pdf.add_field(0, fitz.Rect(50, 100, 70, 120), 'newsletter', 'checkbox')
        pdf.add_field(0, fitz.Rect(50, 150, 250, 180), 'plan', 'dropdown', ['Basic', 'Pro'])

    def import_text(self, pdf, xfdf, refused=None):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'answers.xfdf'
            data.write_text(xfdf)
            return pdf.import_form_data(data, refused)

    def values(self, pdf):
        return {name: value for _, name, value, _kind, _choices in pdf.fields()}

    def test_import_leaves_no_unticked_and_refuses_unknown_option(self):
        # Issue 66: "No" and "false" ticked the boxes and the dropdown took a value it does not offer.
        self.add_consent_form(self.pdf)
        refused = []
        self.assertEqual(self.import_text(self.pdf, self.ISSUE_66_XFDF, refused), 2)
        self.assertEqual(self.values(self.pdf), {'agree': 'Off', 'newsletter': 'Off', 'plan': 'Basic'})
        self.assertEqual(refused, [('plan', "'Enterprise' is not one of: Basic, Pro")])
        self.pdf.restore(self.pdf.bytes())
        self.assertEqual(self.values(self.pdf), {'agree': 'Off', 'newsletter': 'Off', 'plan': 'Basic'})

    def test_import_checkbox_ticks_only_for_yes_words_or_its_on_state(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 70, 70), 'box', 'checkbox')
        on_state = next(w.on_state() for w in self.pdf.doc[0].widgets())
        xfdf = '<xfdf><fields><field name="box"><value>{}</value></field></fields></xfdf>'
        for answer in ('YES', 'true', '1', 'on', on_state, str(on_state).upper()):
            self.pdf.fill_field(0, 'box', 'no')
            self.import_text(self.pdf, xfdf.format(answer))
            self.assertEqual(self.values(self.pdf)['box'], on_state, answer)
        for answer in ('NO', 'False', '0', 'Off', '', 'maybe'):
            self.pdf.fill_field(0, 'box', 'yes')
            self.import_text(self.pdf, xfdf.format(answer))
            self.assertEqual(self.values(self.pdf)['box'], 'Off', answer)

    def test_import_ticks_a_box_whose_on_state_is_not_a_yes_word(self):
        # A form made elsewhere can name its ticked state anything; its own export carries that name.
        self.pdf.add_field(0, fitz.Rect(50, 50, 70, 70), 'box', 'checkbox')
        xfdf = '<xfdf><fields><field name="box"><value>Accepted</value></field></fields></xfdf>'
        with patch.object(fitz.Widget, 'on_state', return_value='Accepted'):
            widget = next(iter(self.pdf.doc[0].widgets()))
            self.assertEqual(self.pdf._field_answer(widget, 'accepted', own_on_state=True), 'Accepted')
            self.assertEqual(self.pdf._field_answer(widget, 'accepted'), 'Off')

    def test_import_refuses_listbox_value_outside_its_options(self):
        self.pdf.add_field(0, fitz.Rect(50, 100, 250, 180), 'fruit', 'listbox', ['Apple', 'Pear'])
        self.pdf.add_field(0, fitz.Rect(50, 200, 250, 230), 'name', 'text')
        refused = []
        xfdf = ('<xfdf><fields><field name="fruit"><value>Durian</value></field>'
                '<field name="name"><value>Ada</value></field></fields></xfdf>')
        self.assertEqual(self.import_text(self.pdf, xfdf, refused), 1)  # the good value still applies
        self.assertEqual(self.values(self.pdf), {'fruit': 'Apple', 'name': 'Ada'})
        self.assertEqual([name for name, _reason in refused], ['fruit'])

    def test_export_then_import_reproduces_every_value(self):
        self.add_consent_form(self.pdf)
        self.pdf.add_field(0, fitz.Rect(50, 200, 250, 280), 'fruit', 'listbox', ['Apple', 'Pear'])
        self.pdf.add_field(0, fitz.Rect(50, 300, 250, 330), 'name', 'text')
        for name, answer in (('agree', 'yes'), ('newsletter', 'no'), ('plan', 'Pro'), ('fruit', 'Pear'), ('name', 'Ada')):
            self.pdf.fill_field(0, name, answer)
        filled = self.values(self.pdf)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'answers.xfdf'
            self.assertEqual(self.pdf.export_form_data(data), 5)
            # Set every field to the opposite answer, then bring the exported ones back.
            for name, answer in (('agree', 'no'), ('newsletter', 'yes'), ('plan', 'Basic'), ('fruit', 'Apple'), ('name', '')):
                self.pdf.fill_field(0, name, answer)
            refused = []
            self.assertEqual(self.pdf.import_form_data(data, refused), 5)
        self.assertEqual(refused, [])
        self.assertEqual(self.values(self.pdf), filled)

    def add_dropdown(self, pdf, choices, value, flags=0):
        # A dropdown as another tool writes it, which add_field cannot make.
        widget = fitz.Widget()
        widget.field_name, widget.field_type = 'town', fitz.PDF_WIDGET_TYPE_COMBOBOX
        widget.rect = fitz.Rect(50, 50, 250, 80)
        widget.choice_values, widget.field_flags, widget.field_value = choices, flags, value
        pdf.doc[0].add_widget(widget)
        pdf.restore(pdf.bytes())

    def town_xfdf(self, value):
        return f'<xfdf><fields><field name="town"><value>{value}</value></field></fields></xfdf>'

    def test_import_accepts_either_name_of_an_export_display_option(self):
        self.add_dropdown(self.pdf, [('sg', 'Singapore'), ('my', 'Malaysia')], 'Singapore')
        for value in ('Malaysia', 'my'):
            with self.subTest(value=value):
                refused = []
                self.assertEqual(self.import_text(self.pdf, self.town_xfdf(value), refused), 1)
                self.assertEqual((self.values(self.pdf), refused), ({'town': value}, []))
        refused = []
        self.assertEqual(self.import_text(self.pdf, self.town_xfdf('Brunei'), refused), 0)
        self.assertEqual(refused, [('town', "'Brunei' is not one of: Singapore, Malaysia")])
        self.assertEqual(self.values(self.pdf), {'town': 'my'})
        self.pdf.fill_field(0, 'town', 'Singapore')  # Fill fields follows the same rule
        with tempfile.TemporaryDirectory() as folder:  # and OpenPDF's own export comes back
            data = Path(folder) / 'answers.xfdf'
            self.pdf.export_form_data(data)
            self.pdf.fill_field(0, 'town', 'Malaysia')
            self.assertEqual(self.pdf.import_form_data(data), 1)
        self.assertEqual(self.values(self.pdf), {'town': 'Singapore'})

    def test_import_keeps_a_typed_value_in_an_editable_dropdown(self):
        self.add_dropdown(self.pdf, ['A', 'B'], 'Custom town', fitz.PDF_CH_FIELD_IS_EDIT)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'answers.xfdf'
            self.pdf.export_form_data(data)
            self.pdf.fill_field(0, 'town', 'B')
            refused = []
            self.assertEqual(self.pdf.import_form_data(data, refused), 1)
        self.assertEqual((self.values(self.pdf), refused), ({'town': 'Custom town'}, []))


    ISSUE_67_FDF = (
        '%FDF-1.2\n1 0 obj\n'
        '<< /FDF << /Fields [ << /T (full_name) /V (Ada Lovelace) >> << /T (agree) /V /Yes >> ] >> >>\n'
        'endobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n')

    def test_file_that_is_not_xfdf_gets_a_plain_message_and_changes_nothing(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'full_name', 'text')
        self.pdf.add_field(0, fitz.Rect(50, 100, 70, 120), 'agree', 'checkbox')
        before = self.values(self.pdf)
        cut_off = '<xfdf><fields><field name="full_name"><value>Ada Lovelace</value></field><field name="agr'
        files = {
            'answers.fdf': self.ISSUE_67_FDF.encode(),
            'a document.pdf': self.pdf.bytes(),
            'empty.xfdf': b'',
            'cut off.xfdf': cut_off.encode(),
            'no fields.xml': b'<?xml version="1.0"?><note><full_name>Ada Lovelace</full_name></note>',
            'database dump.xml': b'<mysqldump><database name="d"><table_data name="t"><row>'
                                 b'<field name="full_name">Bob Stone</field></row></table_data></database></mysqldump>',
            'web form.xml': b'<html><form><field name="full_name"><value>X</value></field></form></html>',
            'records.xml': b'<records><field>full_name</field></records>',
            'not utf-8.xfdf': b'<?xml version="1.0" encoding="UTF-8"?><xfdf><fields><field name="full_name">'
                              b'<value>Ada \xff</value></field></fields></xfdf>',
        }
        with tempfile.TemporaryDirectory() as folder:
            for name, content in files.items():
                with self.subTest(file=name):
                    data = Path(folder) / name
                    data.write_bytes(content)
                    with self.assertRaises(ValueError) as raised:
                        self.pdf.import_form_data(data)
                    self.assertEqual(str(raised.exception), 'This is not an XFDF file.')
                    self.assertEqual(self.values(self.pdf), before)

    def test_xfdf_with_no_fields_or_no_matching_names_imports_nothing(self):
        self.pdf.add_field(0, fitz.Rect(50, 50, 250, 80), 'full_name', 'text')
        before = self.values(self.pdf)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'empty form.xfdf'
            blank = PDF()
            try:
                blank.doc.new_page()
                self.assertEqual(blank.export_form_data(data), 0)  # what OpenPDF writes for a form with no fields
            finally:
                blank.close()
            self.assertEqual(self.pdf.import_form_data(data), 0)
        other = '<xfdf><fields><field name="someone_else"><value>x</value></field></fields></xfdf>'
        self.assertEqual(self.import_text(self.pdf, other), 0)
        self.assertEqual(self.values(self.pdf), before)

    def test_import_picker_offers_only_the_formats_that_are_read(self):
        source = (Path(__file__).resolve().parent.parent / 'app.py').read_text(encoding='utf-8')
        body = source.split('def import_form(self):', 1)[1].split('\n    def ', 1)[0]
        self.assertIn("'*.xfdf *.xml'", body)
        self.assertNotIn('.fdf ', body.replace('.xfdf', ''))

    # Issue 76: the XFDF specification writes grouped fields as nested <field> elements.
    ISSUE_76_XFDF = (
        '<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve"><fields>\n'
        '<field name="applicant"><field name="name"><value>Ada Lovelace</value></field>'
        '<field name="city"><value>London</value></field></field>\n'
        '<field name="employer"><field name="name"><value>Analytical Engines Ltd</value></field>'
        '<field name="city"><value>Turin</value></field></field>\n'
        '</fields></xfdf>')
    ISSUE_76_VALUES = {'applicant.name': 'Ada Lovelace', 'applicant.city': 'London',
                       'employer.name': 'Analytical Engines Ltd', 'employer.city': 'Turin'}

    def add_grouped(self, pdf, fields):
        # Fields grouped under parent fields (applicant.name is "name" inside "applicant"),
        # as form designers write them and add_field cannot.
        doc, groups, top = pdf.doc, {}, []
        for number, (full_name, kind, choices) in enumerate(fields):
            pdf.add_field(0, fitz.Rect(50, 20 + 30 * number, 250, 40 + 30 * number), f'field{number}', kind, choices)
            child = next(w.xref for w in doc[0].widgets() if w.field_name == f'field{number}')
            parts = full_name.split('.')
            doc.xref_set_key(child, 'T', fitz.get_pdf_str(parts[-1]))
            for depth in range(len(parts) - 1, 0, -1):
                known = tuple(parts[:depth]) in groups
                if not known:
                    groups[tuple(parts[:depth])] = doc.get_new_xref()
                    doc.update_object(groups[tuple(parts[:depth])], f'<< /T {fitz.get_pdf_str(parts[depth - 1])} /Kids [] >>')
                parent = groups[tuple(parts[:depth])]
                doc.xref_set_key(parent, 'Kids', doc.xref_get_key(parent, 'Kids')[1][:-1] + f' {child} 0 R]')
                doc.xref_set_key(child, 'Parent', f'{parent} 0 R')
                if known:
                    break
                child = parent
            else:
                top.append(child)
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/Fields', '[' + ' '.join(f'{xref} 0 R' for xref in top) + ']')
        pdf.restore(pdf.bytes())

    def add_issue_76_form(self, pdf):
        self.add_grouped(pdf, [(name, 'text', None) for name in self.ISSUE_76_VALUES])

    def test_import_fills_grouped_fields_from_nested_xfdf(self):
        self.add_issue_76_form(self.pdf)
        self.assertEqual(self.values(self.pdf), dict.fromkeys(self.ISSUE_76_VALUES, ''))
        for xfdf in (self.ISSUE_76_XFDF, self.ISSUE_76_XFDF.replace(' xmlns="http://ns.adobe.com/xfdf/"', '')):
            with self.subTest(namespaced='xmlns=' in xfdf):
                for name in self.ISSUE_76_VALUES:
                    self.pdf.fill_field(0, name, '')
                self.assertEqual(self.import_text(self.pdf, xfdf), 4)
                self.assertEqual(self.values(self.pdf), self.ISSUE_76_VALUES)
        self.pdf.restore(self.pdf.doc.tobytes(garbage=4))
        self.assertEqual(self.values(self.pdf), self.ISSUE_76_VALUES)

    def test_nested_xfdf_does_not_fill_ungrouped_fields_that_share_a_leaf_name(self):
        # It used to give "name" and "city" the employer's values; a group holds no value of its own.
        for number, name in enumerate(('name', 'city', 'applicant')):
            self.pdf.add_field(0, fitz.Rect(50, 50 + 40 * number, 250, 80 + 40 * number), name, 'text')
        self.pdf.fill_field(0, 'applicant', 'unchanged')
        self.assertEqual(self.import_text(self.pdf, self.ISSUE_76_XFDF), 0)
        self.assertEqual(self.values(self.pdf), {'name': '', 'city': '', 'applicant': 'unchanged'})

    def test_flat_dotted_xfdf_still_fills_grouped_fields(self):
        self.add_issue_76_form(self.pdf)
        flat = '<xfdf><fields>{}</fields></xfdf>'.format(''.join(
            f'<field name="{name}"><value>{value}</value></field>' for name, value in self.ISSUE_76_VALUES.items()))
        self.assertEqual(self.import_text(self.pdf, flat), 4)
        self.assertEqual(self.values(self.pdf), self.ISSUE_76_VALUES)

    def test_import_joins_three_levels_of_nested_names(self):
        self.add_grouped(self.pdf, [('a.b.c', 'text', None), ('a.b.d', 'text', None), ('a.e', 'text', None)])
        xfdf = ('<xfdf><fields><field name="a"><field name="b"><field name="c"><value>deep</value></field>'
                '<field name="d"><value>also deep</value></field></field>'
                '<field name="e"><value>middle</value></field></field></fields></xfdf>')
        self.assertEqual(self.import_text(self.pdf, xfdf), 3)
        self.assertEqual(self.values(self.pdf), {'a.b.c': 'deep', 'a.b.d': 'also deep', 'a.e': 'middle'})

    def test_export_nests_grouped_fields_and_import_reproduces_every_value(self):
        import xml.etree.ElementTree as ET
        self.add_grouped(self.pdf, [(name, 'text', None) for name in self.ISSUE_76_VALUES] + [('a.b.c', 'text', None)])
        self.pdf.add_field(0, fitz.Rect(50, 300, 250, 330), 'plain', 'text')
        self.pdf.add_field(0, fitz.Rect(50, 340, 250, 370), 'x.y', 'text')  # ungrouped, with a dot in its own name
        self.pdf.add_field(0, fitz.Rect(50, 380, 250, 410), 'odd..name', 'text')
        filled = dict(self.ISSUE_76_VALUES, **{'a.b.c': 'deep', 'plain': 'flat', 'x.y': 'dotted', 'odd..name': 'odd'})
        for name, value in filled.items():
            self.pdf.fill_field(0, name, value)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / 'answers.xfdf'
            self.assertEqual(self.pdf.export_form_data(data), 8)
            ns = {'x': 'http://ns.adobe.com/xfdf/'}
            fields = ET.parse(data).getroot().find('x:fields', ns)
            self.assertEqual([f.get('name') for f in fields.findall('x:field', ns)],
                             ['applicant', 'employer', 'a', 'plain', 'x', 'odd..name'])  # one element per group
            applicant = fields.find('x:field[@name="applicant"]', ns)
            self.assertIsNone(applicant.find('x:value', ns))
            self.assertEqual({f.get('name'): f.find('x:value', ns).text for f in applicant.findall('x:field', ns)},
                             {'name': 'Ada Lovelace', 'city': 'London'})
            self.assertEqual(fields.find('x:field[@name="a"]/x:field[@name="b"]/x:field[@name="c"]/x:value', ns).text, 'deep')
            for name in filled:
                self.pdf.fill_field(0, name, 'something else')
            refused = []
            self.assertEqual(self.pdf.import_form_data(data, refused), 8)
        self.assertEqual((self.values(self.pdf), refused), (filled, []))

    def test_nested_import_follows_the_checkbox_and_dropdown_rules(self):
        self.add_grouped(self.pdf, [('consent.agree', 'checkbox', None), ('consent.news', 'checkbox', None),
                                    ('order.plan', 'dropdown', ['Basic', 'Pro']), ('order.size', 'dropdown', ['S', 'L'])])
        self.pdf.fill_field(0, 'consent.news', 'yes')
        xfdf = ('<xfdf><fields><field name="consent"><field name="agree"><value>yes</value></field>'
                '<field name="news"><value>No</value></field></field>'
                '<field name="order"><field name="plan"><value>Enterprise</value></field>'
                '<field name="size"><value>L</value></field></field></fields></xfdf>')
        refused = []
        self.assertEqual(self.import_text(self.pdf, xfdf, refused), 3)
        values = self.values(self.pdf)
        self.assertNotEqual(values.pop('consent.agree'), 'Off')
        self.assertEqual(values, {'consent.news': 'Off', 'order.plan': 'Basic', 'order.size': 'L'})
        self.assertEqual(refused, [('order.plan', "'Enterprise' is not one of: Basic, Pro")])

if __name__ == '__main__':
    unittest.main()
