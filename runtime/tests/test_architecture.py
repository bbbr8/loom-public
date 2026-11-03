"""Boundary, reproducibility and release-isolation contracts; synthetic inputs only."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import surd_search as s


class BoundaryTests(unittest.TestCase):
    def test_money_display_is_exact_on_supported_python_versions(self):
        import surd
        from fractions import Fraction
        for value, expected in [(Fraction(900719925474099301,100), "$9,007,199,254,740,993.01"), (Fraction(5,1000), "$0.00"), (Fraction(15,1000), "$0.02"), (Fraction(-15,1000), "$-0.02"), (None,"?")]:
            self.assertEqual(surd._display_amount(value), expected)

    def test_public_documents_are_independent_snapshots(self):
        ix = s.SearchIndex([{"id":"a", "text":"source", "metadata":{"nested":{"v":1}}}])
        doc = ix.document("a"); doc["metadata"]["nested"]["v"] = 2
        rows = list(ix.documents()); rows[0]["text"] = "changed"
        self.assertEqual(ix.document("a")["metadata"]["nested"]["v"], 1)
        self.assertEqual(ix.document("a")["text"], "source")
        self.assertIsInstance(ix.document_tokens("a"), tuple)

    def test_public_scope_uses_the_search_filter_contract(self):
        ix = s.SearchIndex([{"id":"a","text":"source","version":2},{"id":"b","text":"source","version":3}])
        self.assertEqual([d["id"] for d in ix.documents({"version":2})], ["a"])
        self.assertFalse(ix.in_scope("a", {"version":3}, {}))
        for call in [lambda: list(ix.documents({}, {"x":{"gte":5,"lte":1}})), lambda: ix.in_scope("a", [], {})]:
            with self.assertRaises(s.SearchError): call()

    def test_reasoning_runs_through_the_public_port(self):
        ix = s.SearchIndex([{"id":"a","text":"Source says yes."}])
        class PublicOnly:
            search = ix.search
            document = ix.document
            documents = ix.documents
            document_tokens = ix.document_tokens
            in_scope = ix.in_scope
            manifest = ix.manifest
        r = s.reason(PublicOnly(), "yes", {"claims":[{"id":"a","text":"Yes.","support":[{"document":"a","quote":"Source says yes."}]}],"goal":"a"})
        self.assertEqual(r["assessment"]["status"], "supported")


class BuildTests(unittest.TestCase):
    def setUp(self):
        import surd_build
        self.build = surd_build

    def test_dependency_graph_is_acyclic_and_reasoning_has_no_private_access(self):
        r = self.build.architecture(ROOT)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["dependencies"]["surd_reason"], ["surd_search_core"])

    def test_planted_dependency_cycle_and_missing_module_fail(self):
        for modules in [{"surd_a.py":"import surd_b\n", "surd_b.py":"import surd_a\n"},
                        {"surd_a.py":"import surd_missing\n"}]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name, text in modules.items(): (root/name).write_text(text)
                with self.assertRaises(self.build.BuildError): self.build.architecture(root)

    def test_build_is_byte_reproducible_and_allowlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            a = self.build.build(ROOT, out/"one.tar.gz")
            b = self.build.build(ROOT, out/"two.tar.gz")
            self.assertEqual(a["archive_sha256"], b["archive_sha256"])
            self.assertEqual((out/"one.tar.gz").read_bytes(), (out/"two.tar.gz").read_bytes())
            with tarfile.open(out/"one.tar.gz") as tar:
                names = tar.getnames()
                self.assertFalse(any("LOCAL_ONLY" in x or "/state/" in x or "/case/" in x or "/_history/" in x or "/train/" in x for x in names))
            self.assertNotIn(str(ROOT), json.dumps(a["manifest"]))

    def test_clean_extracted_release_runs_without_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); archive = tmp/"release.tar.gz"
            self.build.build(ROOT, archive)
            extracted = self.build.unpack(archive, tmp/"isolated")
            env = dict(os.environ, SURD_PORTALS="0", PYTHONPATH="", CASE_LANGUAGE_ROOT=str(tmp/"absent"))
            p = subprocess.run([sys.executable, str(extracted/"surd.py"), "run", str(extracted/"examples/search_reasoning.surd")], cwd=tmp, env=env, capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            p = subprocess.run([sys.executable, str(extracted/"surd.py"), "describe", "--json"], cwd=tmp, env=env, capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            builtins = json.loads(p.stdout)["builtins"]
            self.assertTrue(all(x["verified"] for x in builtins if x["scope"] == "portable"))
            integrations = [x for x in builtins if x["scope"] != "portable"]
            self.assertEqual({x["name"] for x in integrations}, {"docs", "anchor_check", "stages"})
            self.assertTrue(all(x["verified"] is None and x["status"] == "integration_required" for x in integrations))

    def test_extracted_file_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); archive=tmp/"release.tar.gz"; self.build.build(ROOT,archive)
            extracted=self.build.unpack(archive,tmp/"out")
            (extracted/"surd_reason.py").write_text("# corrupt\n")
            with self.assertRaises(self.build.BuildError): self.build.verify_tree(extracted)

    def test_tar_path_traversal_and_symlinks_are_rejected(self):
        for name, link in [("../escape",None),("release/link","/tmp/target")]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp=Path(tmp); p=tmp/"bad.tar.gz"
                with tarfile.open(p,"w:gz") as tar:
                    info=tarfile.TarInfo(name)
                    if link: info.type=tarfile.SYMTYPE; info.linkname=link
                    else: info.size=1
                    tar.addfile(info, None if link else io.BytesIO(b"x"))
                with self.assertRaises(self.build.BuildError): self.build.unpack(p,tmp/"out")

    def test_release_checks_reject_false_or_skipped_verdicts(self):
        from surd_release import verdict
        self.assertEqual(verdict(0,'{"ok":true,"checks":3}',True), "passed")
        for code, text in [(1,'{"ok":true}'),(0,'{"ok":false}'),(0,'[]'),(0,'broken')]:
            self.assertEqual(verdict(code,text,True), "failed")
        self.assertEqual(verdict(0,'fixture: SKIP (unavailable)',False), "skipped")

    def test_documentation_cannot_pass_with_only_excluded_examples(self):
        from surd_release import builtin_verdict
        rows = [{"name": n,"scope":"native-case-integration","verified":None,"status":"integration_required"} for n in ("docs","anchor_check","stages")]
        self.assertEqual(builtin_verdict({"builtins":rows})["state"], "failed")
        rows.append({"name":"ordinary","scope":"portable","verified":True})
        self.assertEqual(builtin_verdict({"builtins":rows})["state"], "passed")
        rows[-1]["verified"] = False
        self.assertEqual(builtin_verdict({"builtins":rows})["state"], "failed")


if __name__ == "__main__":
    unittest.main()
