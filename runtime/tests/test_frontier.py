from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.store import GraphStore
from loom_research.frontier import evaluate_frontier,dominates
from research_fixtures import batch


class RecipeFrontier(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'graph.sqlite';self.store=GraphStore(self.path,create=True);self.store.apply(batch())
        self.packet={'development':[{'id':'literal','query':'blue invoice','family':'dev','relevant':['A']},
                                    {'id':'linked','query':'blue invoice','family':'dev','relevant':['B']}],
                     'audit':[{'id':'audit','query':'payment confirmation','family':'audit','relevant':['B']}],
                     'recipes':[{'id':'literal','params':{'top':1,'hops':0}},
                                {'id':'graph','params':{'top':1,'hops':1}}]}

    def test_measured_recipe_archive_is_read_only_and_reproducible(self):
        before=self.path.read_bytes();packet=deepcopy(self.packet);r=evaluate_frontier(self.store,packet)
        self.assertEqual(r['state'],'EVALUATED');self.assertTrue(r['frontier'])
        self.assertFalse(r['activation_allowed']);self.assertEqual(r['evaluations'],5)
        self.assertEqual(r['audit_state'],'EVALUATED_AFTER_SELECTION')
        self.assertEqual(r,evaluate_frontier(self.store,packet));self.assertEqual(before,self.path.read_bytes())

    def test_duplicate_proposals_do_not_weight_selection(self):
        first=evaluate_frontier(self.store,self.packet)
        self.packet['recipes'].append({'id':'copy','params':{'top':1,'hops':0}})
        second=evaluate_frontier(self.store,self.packet)
        self.assertEqual(first['selected_params_sha256'],second['selected_params_sha256']);self.assertEqual(second['evaluations'],5)

    def test_family_leakage_and_budget(self):
        self.packet['max_evaluations']=1
        r=evaluate_frontier(self.store,self.packet);self.assertEqual(r['state'],'BUDGET_EXHAUSTED');self.assertIsNone(r['selected'])
        self.packet['audit'][0]['family']='dev'
        with self.assertRaises(ValueError):evaluate_frontier(self.store,self.packet)

    def test_audit_labels_cannot_change_selection(self):
        before=evaluate_frontier(self.store,self.packet)
        self.packet['audit'][0]['relevant']=['A']
        after=evaluate_frontier(self.store,self.packet)
        self.assertEqual(before['selected_before_audit'],after['selected_before_audit'])

    def test_pareto_tradeoffs(self):
        self.assertFalse(dominates([1,0],[0,1]));self.assertTrue(dominates([1,1],[1,0]))
        self.assertFalse(dominates([1,1],[1,1]))


if __name__=='__main__':unittest.main()
