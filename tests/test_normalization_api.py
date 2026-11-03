import io,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import worker,access
from loom_research import normalization as N,intake
from loom_research.store import GraphStore
from loom_research.engine import records_batch

class Integration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_pure_api_exact_money(self):
        r=worker.run({'operation':'normalize_value','packet':{'kind':'money','value':'USD 1,234.56'}})
        self.assertEqual(r['minor_units'],123456)
    def test_api_persist_and_inspect(self):
        s=GraphStore(self.root/'case.sqlite',create=True);s.apply(records_batch('seed',[{'id':'p','origin':'synthetic:test','locator':'row1','text':'USD 1,234.56'}]))
        with patch.dict(os.environ,{'LOOM_DATA_DIR':str(self.root)}):
            packet={'batch_id':'n','expected_revision':1,'records':[{'node_id':'p','fields':[{'name':'amount','kind':'money','start':0,'end':12}]}]}
            r=worker.run({'operation':'graph_normalize','graph':'case','packet':packet});self.assertEqual(r['normalized'],1)
            r=worker.run({'operation':'graph_normalization','graph':'case','query':'p'});self.assertEqual(len(r['views']),1)
            r=worker.run({'operation':'graph_normalized_search','graph':'case','query':'1234.56'});self.assertEqual(r['hits'][0]['id'],'p')
        self.assertNotIn('graph_normalize',access.READ_OPERATIONS);self.assertIn('normalize_value',access.READ_OPERATIONS)
    def test_workbook_layout_formula_and_identifier_format(self):
        from openpyxl import Workbook
        w=Workbook();s=w.active;s.append(['Loan','Budget','Formula']);s.append([673,1234.56,'=B2*2']);s['A2'].number_format='000000';s.merge_cells('A3:C3');s['A3']='Merged heading';s.row_dimensions[2].hidden=True;s['B2'].number_format='$#,##0.00'
        p=self.root/'rows.xlsx';w.save(p)
        records,_=intake.prepare({'batch_id':'xlsx','files':[{'path':str(p),'sheets':{'Sheet':{'header_row':1,'start_row':1,'end_row':3}},'normalization_profile':{'currency':'USD'}}]})
        row=next(r for r in records if r['data']['row']==2);meta=row['data'];values={v['name']:v['value'] for v in meta['normalization']['fields']}
        self.assertEqual(values['A2']['canonical'],'000673');self.assertEqual(values['A2']['raw'],'673');self.assertEqual(values['B2']['minor_units'],123456);self.assertEqual(values['C2']['state'],'UNEVALUATED_FORMULA')
        self.assertEqual(meta['layout']['merged_ranges'],['A3:C3']);self.assertEqual(meta['layout']['hidden_rows'],[2]);self.assertEqual(meta['number_formats']['B2'],'$#,##0.00')
        last=next(r for r in records if r['data']['row']==3);self.assertEqual(last['data']['cells']['B3'],'')
    def test_record_roles_are_preserved(self):
        r=worker.run({'operation':'normalize_record','packet':{'record_id':'x','fields':[
            {'name':'a','kind':'money','value':'USD 100','role':'requested'},
            {'name':'b','kind':'money','value':'USD 100','role':'credited'}]}})
        self.assertEqual([f['role'] for f in r['fields']],['requested','credited'])

if __name__=='__main__':unittest.main()
