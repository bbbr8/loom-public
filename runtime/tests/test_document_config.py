"""Synthetic regressions for opt-in document access and exact amount matching."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from fractions import Fraction
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import surd_docs as D
import surd_timeline as T


class DocumentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'documents.json'
        self.env = patch.dict(os.environ, {'LOOM_DOCUMENT_CONFIG': str(self.config)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, data):
        self.config.write_text(json.dumps(data), encoding='utf-8')

    def test_unconfigured_never_reads_home(self):
        with patch.dict(os.environ, {'LOOM_DOCUMENT_CONFIG': ''}):
            self.assertEqual(D.kinds(), [])
            with self.assertRaises(D.DocsError): D.docs('invoices')
            with self.assertRaises(D.DocsError): D.stages(1)
            with self.assertRaises(D.DocsError): D.anchor_check('DOC_00001', 'test')

    def test_exact_money_and_project_filter(self):
        self.write({'families': {'invoices': {'rows': [
            {'id': 'A', 'amount': '0.01', 'loan_file': 'project-A'},
            {'id': 'B', 'amount': '0.01', 'loan_file': 'project-B'}]},
            'banks': {'rows': [{'id': 'C', 'amount': '-0.01', 'loan_file': 'project-A'}]}}})
        self.assertEqual(D.docs('invoices')[0]['amount'], Fraction(1, 100))
        result = D.stages(Fraction(1, 100), 'project-A')
        self.assertEqual(result['reached'], ['billed', 'paid'])
        self.assertEqual([r['id'] for r in result['stages']['billed']], ['A'])
        self.assertEqual(len(D.docs('invoices', {'id': 'a'})), 1)

    def test_neighbor_pages(self):
        self.write({'pages': {'DOC_00001': 'Example title', 'DOC_00002': 'Example total $12.34'}})
        self.assertEqual(D.anchor_check('DOC_00001', '12.34')['found_on'], ['DOC_00002'])
        self.assertTrue(D.anchor_check('DOC_00002', '12.34')['prints_it'])

    def test_sqlite_explicit_readonly(self):
        db = self.root / 'fixture.sqlite'
        with closing(sqlite3.connect(db)) as con:
            con.execute('create table sample (id text, amount text)')
            con.execute("insert into sample values ('A', '12.34')")
            con.commit()
        self.write({'families': {'invoices': {'path': 'fixture.sqlite', 'query': 'select * from sample'}}})
        self.assertEqual(D.docs('invoices')[0]['amount'], Fraction(617, 50))
        self.write({'families': {'invoices': {'path': 'fixture.sqlite', 'query': 'delete from sample'}}})
        with self.assertRaises(D.DocsError): D.docs('invoices')
        with closing(sqlite3.connect(db)) as con:
            self.assertEqual(con.execute('select count(*) from sample').fetchone()[0], 1)

    def test_invalid_config_reports_error(self):
        self.write({'families': []})
        with self.assertRaises(D.DocsError): D.kinds()
        self.write({'families': {'invoices': {'rows': [{'amount': 'not money'}]}}})
        with self.assertRaises(D.DocsError): D.docs('invoices')

    def test_timeline_requires_explicit_ledger(self):
        with patch.dict(os.environ, {'LOOM_LEDGER': '', 'LOOM_TIMELINE_STORE': ''}):
            with self.assertRaisesRegex(ValueError, 'explicitly'):
                T.run('money ledger')


if __name__ == '__main__':
    unittest.main()
