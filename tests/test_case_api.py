import sys,unittest,io
from pathlib import Path
from email.message import EmailMessage
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tests'))
import test_casework as case_test
from loom_research import intake
class CaseApiTests(unittest.TestCase):
    setUp=case_test.CaseTests.setUp
    mapping=case_test.CaseTests.mapping
    put=case_test.CaseTests.put
    def test_api_read_case_episode_and_readonly_role(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from unittest.mock import patch
        import worker,access
        self.put(self.mapping())
        with patch.dict('os.environ',{'LOOM_DATA_DIR':str(self.root)}):
            r=worker.run({'operation':'graph_case_episode','graph':'case','query':'budget'})
        self.assertEqual(r['state'],'CASE_EPISODE_DRAFT')
        self.assertIn('graph_case_episode',access.READ_OPERATIONS)
        self.assertNotIn('graph_case_map',access.READ_OPERATIONS)

class AttachmentTests(unittest.TestCase):
    def test_misdeclared_workbook_fingerprint_and_cells(self):
        from openpyxl import Workbook
        b=io.BytesIO();w=Workbook();w.active.append(['Item','Budget']);w.active.append(['Slab',123]);w.save(b)
        m=EmailMessage();m['From']='Builder <b@example.test>';m['To']='Bank <r@example.test>'
        m['Date']='Wed, 07 Feb 2018 15:23:42 -0700';m.set_content('Please see revised budget.')
        m.add_attachment(b.getvalue(),maintype='text',subtype='plain',filename='budget.xlsx')
        parts,warnings=intake.sections(Path('mail.eml'),m.as_bytes(),{'attachments':True})
        body=parts[0];self.assertIn('Please see',body[1]);self.assertNotIn('PK',body[1])
        self.assertEqual(body[2]['attachments'][0]['detected_format'],'xlsx')
        rows=[p for p in parts if p[2].get('cells')];self.assertEqual(rows[1][2]['cells']['B2'],'123')
        self.assertEqual(rows[1][2]['declared_content_type'],'text/plain')


if __name__=='__main__':unittest.main()
