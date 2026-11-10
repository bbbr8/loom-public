from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.frontier import evaluate_frontier,dominates
from loom_research.store import GraphStore
from research_fixtures import batch


class FrontierHardening(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=GraphStore(Path(self.tmp.name)/'graph.sqlite',create=True);self.store.apply(batch())
        self.packet={'development':[{'id':'q','query':'blue invoice','family':'d','relevant':['A']}],
                     'recipes':[{'id':'z','params':{'top':1,'hops':0}}, {'id':'a','params':{'top':1,'hops':0}}]}

    def test_alias_order_does_not_change_selected_id(self):
        a=evaluate_frontier(self.store,self.packet)
        self.packet['recipes'].reverse();b=evaluate_frontier(self.store,self.packet)
        self.assertEqual(a['selected'],b['selected'])
        self.assertEqual(a['recipes'][0]['aliases'],['a','z'])

    def test_dimension_mismatch_cannot_claim_dominance(self):
        for left,right in (([1,2],[0]),([],[1])):
            with self.subTest(left=left),self.assertRaises(ValueError):dominates(left,right)

    def test_nonfinite_scores_rejected(self):
        for value in (float('nan'),float('inf'),True):
            with self.subTest(value=value),self.assertRaises(ValueError):dominates([value],[0])

    def test_labels_must_be_distinct_text(self):
        for labels in ([[]],['A','A'],[True]):
            p=deepcopy(self.packet);p['development'][0]['relevant']=labels
            with self.subTest(labels=labels),self.assertRaises(ValueError):evaluate_frontier(self.store,p)

    def test_chain_duplicates_and_answerable_types_rejected(self):
        for updates in ({'chain':['A','A']},{'answerable':1},{'relevant':['A'],'answerable':False}):
            p=deepcopy(self.packet);p['development'][0].update(updates)
            with self.subTest(updates=updates),self.assertRaises(ValueError):evaluate_frontier(self.store,p)


if __name__=='__main__':unittest.main()

