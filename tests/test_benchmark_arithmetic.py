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
            # An identical run has zero change, including after removing any query.
            subprocess.run([sys.executable, str(root / "bench/real_eval.py"),
                            "--runtime", str(root / "runtime"), "--dataset", str(data),
                            "--out", str(Path(temporary) / "paired.json"), "--baseline", str(result)],
                           capture_output=True, check=True)
            paired = json.loads((Path(temporary) / "paired.json").read_text())["paired"]
            self.assertEqual((paired["tied"], paired["map10_change_fraction"]), (3, "0"))

    def test_paired_delta_and_single_query_sensitivity(self):
        from copy import deepcopy
        from bench.paired_metrics import paired_comparison
        def report(scores):
            return dict(dataset_sha256="synthetic", queries=len(scores), rankings=[
                dict(qid=str(i), query="q", relevant={"doc": 1}, ap10_fraction=score)
                for i, score in enumerate(scores)])
        a, b = report(["1", "0", "1/2"]), report(["0", "1/2", "1/2"])
        changes = paired_comparison(a, b)
        self.assertEqual(changes["map10_change_fraction"], "1/6")
        self.assertEqual(changes["leave_one_out"], {
            "minimum_mean_change": "-1/4", "maximum_mean_change": "1/2",
            "minimum_when_omitting": ["0"], "maximum_when_omitting": ["1"]})
        altered = deepcopy(b)
        altered["rankings"][0]["relevant"] = {}
        with self.assertRaises(ValueError):
            paired_comparison(a, altered)
        self.assertIsNone(paired_comparison(report(["1"]), report(["0"]))["leave_one_out"])
