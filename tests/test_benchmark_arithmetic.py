import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


class BenchmarkArithmetic(unittest.TestCase):
    def test_rank_two_credit_and_query_with_no_positive(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "input.zip"
            result = Path(temporary) / "report.json"
            docs = [{"_id": k, "title": "", "text": text} for k, text in
                    [("a", "melon"), ("b", "melon")]]
            queries = [{"_id": k, "text": "melon"} for k in ("only-a", "only-b", "none")]
            with zipfile.ZipFile(data, "w") as archive:
                for filename, rows in [("corpus", docs), ("queries", queries)]:
                    archive.writestr(f"fixture/{filename}.jsonl", "\n".join(map(json.dumps, rows)))
                archive.writestr("fixture/qrels/test.tsv",
                                 "q\td\ts\nonly-a\ta\t1\nonly-b\tb\t2\nnone\ta\t0\n")
            subprocess.run([sys.executable, str(root / "bench/real_eval.py"),
                            "--runtime", str(root / "runtime"), "--dataset", str(data),
                            "--out", str(result)], capture_output=True, text=True, check=True)
            report = json.loads(result.read_text())
            self.assertCountEqual([row["ap10_fraction"] for row in report["rankings"]],
                                  ["1", "1/2", "0"])
            self.assertEqual(report["map10_fraction"], "1/2")
            self.assertEqual(report["map10"], 0.5)
