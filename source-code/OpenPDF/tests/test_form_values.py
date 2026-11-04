import unittest
import fitz
from core import PDF


class FormValueTests(unittest.TestCase):
    def test_filling_unchanged_custom_checkbox_value_keeps_it_checked(self):
        pdf = PDF()
        self.addCleanup(pdf.close)
        pdf.doc.new_page()
        pdf.add_field(0, fitz.Rect(50, 50, 70, 70), 'Consent', checkbox=True)
        page = pdf.doc[0]
        widget = next(page.widgets())
        # Imported forms may use any PDF name for their checked appearance.
        off = pdf.doc.xref_get_key(widget.xref, 'AP/N/Off')[1]
        on = pdf.doc.xref_get_key(widget.xref, 'AP/N/Yes')[1]
        pdf.doc.xref_set_key(widget.xref, 'AP/N', f'<< /Off {off} /Accepted {on} >>')
        pdf.doc.xref_set_key(widget.xref, 'V', '/Accepted')
        pdf.doc.xref_set_key(widget.xref, 'AS', '/Accepted')
        pdf.restore(pdf.bytes())
        self.assertEqual(pdf.fields()[0][2], 'Accepted')

        # Fill form pre-populates its dialog with this value (app.py:801-806).
        index, name, value, _ = pdf.fields()[0]
        pdf.fill_field(index, name, value)
        pdf.restore(pdf.bytes())

        self.assertEqual(pdf.fields()[0][2], 'Accepted')
        page = pdf.doc[0]
        widget = next(page.widgets())
        self.assertEqual(pdf.doc.xref_get_key(widget.xref, 'AS'), ('name', '/Accepted'))

        pdf.fill_field(0, 'Consent', 'no')
        self.assertEqual(pdf.fields()[0][2], 'Off')
        pdf.fill_field(0, 'Consent', 'yes')
        self.assertEqual(pdf.fields()[0][2], 'Accepted')
