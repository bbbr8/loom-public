import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkEncodingTests(unittest.TestCase):
    def test_utf8_bom_in_benchmark_members(self):
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / 'fixture.zip'
            report = Path(scratch) / 'report.json'
            with ZipFile(archive, 'w') as zipped:
                for name, text in {
                    'corpus.jsonl': '{"_id":"d","title":"Glacier","text":"Glacier ice."}',
                    'queries.jsonl': '{"_id":"q","text":"glacier"}',
                    'qrels/test.tsv': 'query-id\tcorpus-id\tscore\nq\td\t1',
                }.items():
                    zipped.writestr('fixture/' + name, text.encode('utf-8-sig'))
            subprocess.run([sys.executable, str(ROOT / 'bench/real_eval.py'),
                            '--runtime', str(ROOT / 'runtime'), '--dataset', str(archive),
                            '--out', str(report)], capture_output=True, check=True)
            self.assertEqual(json.loads(report.read_text())['recall10'], 1)
