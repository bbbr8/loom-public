"""Normalization oracles for exact values, meaning retention and near misses."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research import normalization as N,identity,intake
from loom_research.store import GraphStore
from loom_research.engine import records_batch

class Values(unittest.TestCase):
    def test_raw_survives_unicode_and_spacing(self):
        raw='  Martha\u00a0McGraw  ';r=N.normalize('name',raw);self.assertEqual(r['raw'],raw);self.assertEqual(r['canonical'],'martha mcgraw')
    def test_negation_and_conditions_survive(self):
        r=N.normalize('text','If approved, funds were NOT applied.');self.assertIn('not',r['canonical']);self.assertIn('if approved',r['canonical'])
    def test_name_suffixes_remain_separate(self):
        self.assertNotEqual(N.normalize('name','Alex Example Jr.')['canonical'],N.normalize('name','Alex Example Sr.')['canonical'])
    def test_company_suffix_spelling(self):
        self.assertEqual(N.normalize('business_name','Example Homes, L.L.C.')['canonical'],N.normalize('business_name','Example Homes LLC')['canonical'])
    def test_company_forms_remain_separate(self):
        self.assertNotEqual(N.normalize('business_name','Example LLC')['canonical'],N.normalize('business_name','Example Inc')['canonical'])
    def test_id_leading_zeros(self):self.assertEqual(N.normalize('identifier','000673')['canonical'],'000673')
    def test_id_declared_zero_mask(self):self.assertEqual(N.normalize('identifier','673',{'identifier_width':6})['canonical'],'000673')
    def test_id_width_never_truncates(self):self.assertEqual(N.normalize('identifier','000673',{'identifier_width':4})['state'],'OPEN_IDENTIFIER_WIDTH')
    def test_id_requires_text(self):
        with self.assertRaises(ValueError):N.normalize('identifier',673)
    def test_id_separators_require_profile(self):
        self.assertEqual(N.normalize('identifier','900-000-123')['canonical'],'900-000-123')
        self.assertEqual(N.normalize('identifier','900-000-123',{'identifier_separators':True})['canonical'],'900000123')
    def test_id_ocr_lookalikes(self):self.assertIn('OCR_LOOKALIKE_REVIEW_OPEN',N.normalize('identifier','600OO0673')['warnings'])
    def test_multiline_ids_stay_open(self):self.assertEqual(N.normalize('identifier','673\n393',{'identifier_separators':True})['state'],'OPEN_MULTILINE_IDENTIFIER')
    def test_email_domain_case_only_by_default(self):self.assertEqual(N.normalize('email','User@EXAMPLE.TEST')['canonical'],'User@example.test')
    def test_email_local_case_is_distinct(self):self.assertNotEqual(N.normalize('email','User@example.test')['canonical'],N.normalize('email','user@example.test')['canonical'])
    def test_email_declared_provider_policy(self):self.assertEqual(N.normalize('email','User@example.test',{'email_casefold_domains':['example.test']})['canonical'],'user@example.test')
    def test_email_unicode_domain(self):self.assertEqual(N.normalize('email','user@bücher.test')['canonical'],'user@xn--bcher-kva.test')
    def test_phone_country_is_explicit(self):self.assertEqual(N.normalize('phone','801-555-0101')['state'],'OPEN_PHONE_COUNTRY')
    def test_phone_country_profile(self):self.assertEqual(N.normalize('phone','(801) 555-0101',{'phone_country':'US'})['canonical'],'+18015550101')
    def test_phone_extensions_stay_separate(self):self.assertNotEqual(N.normalize('phone','+1 801 555 0101 x1')['canonical'],N.normalize('phone','+1 801 555 0101 x2')['canonical'])
    def test_iso_date(self):self.assertEqual(N.normalize('date','2018-02-07')['canonical'],'2018-02-07')
    def test_written_date(self):self.assertEqual(N.normalize('date','7 Feb 2018')['canonical'],'2018-02-07')
    def test_ambiguous_date(self):
        r=N.normalize('date','03/04/2018');self.assertEqual(r['state'],'AMBIGUOUS_DATE');self.assertEqual(r['candidates'],['2018-03-04','2018-04-03'])
    def test_date_order_profile(self):self.assertEqual(N.normalize('date','03/04/2018',{'date_order':'DMY'})['canonical'],'2018-04-03')
    def test_unambiguous_numeric_date(self):self.assertEqual(N.normalize('date','31/01/2018')['canonical'],'2018-01-31')
    def test_two_digit_year_open(self):self.assertEqual(N.normalize('date','02/07/18')['state'],'AMBIGUOUS_CENTURY')
    def test_two_digit_year_profile(self):self.assertEqual(N.normalize('date','02/07/18',{'date_order':'MDY','two_digit_century':2000})['canonical'],'2018-02-07')
    def test_invalid_date(self):self.assertEqual(N.normalize('date','2018-02-30')['state'],'INVALID_DATE')
    def test_datetime_zone(self):self.assertEqual(N.normalize('datetime','2018-02-07T15:23:42-07:00')['canonical'],'2018-02-07T22:23:42Z')
    def test_naive_datetime(self):self.assertEqual(N.normalize('datetime','2018-02-07T15:23:42')['state'],'OPEN_TIMEZONE')
    def test_date_only_never_invents_time(self):
        r=N.normalize('datetime','2018-02-07');self.assertEqual(r['state'],'DATE_ONLY');self.assertEqual(r['canonical'],'2018-02-07')
    def test_exact_cents(self):
        r=N.normalize('money','$1,234.56',{'dollar_currency':'USD'});self.assertEqual(r['minor_units'],123456);self.assertEqual(r['exact_rational'],'30864/25')
    def test_currency_symbol_ambiguity(self):self.assertEqual(N.normalize('money','$1.00')['state'],'OPEN_CURRENCY')
    def test_negative_parentheses(self):self.assertEqual(N.normalize('money','(USD 1,234.56)')['minor_units'],-123456)
    def test_european_decimal_profile(self):self.assertEqual(N.normalize('money','EUR 1.234,56',{'decimal_separator':',','group_separator':'.'})['minor_units'],123456)
    def test_invalid_grouping(self):self.assertEqual(N.normalize('money','USD 12,34.56')['state'],'INVALID_GROUPING')
    def test_currency_conflict(self):self.assertEqual(N.normalize('money','EUR 12.00',{'currency':'USD'})['state'],'CURRENCY_CONFLICT')
    def test_mixed_currency_symbols(self):self.assertEqual(N.normalize('money','€$12.00')['state'],'CURRENCY_CONFLICT')
    def test_precision_never_rounds(self):
        r=N.normalize('money','USD 1.001');self.assertEqual(r['state'],'OPEN_CURRENCY_PRECISION');self.assertNotIn('minor_units',r)
    def test_float_requires_exact_text(self):self.assertEqual(N.normalize('money',12.5,{'currency':'USD'})['state'],'EXACT_TEXT_REQUIRED')
    def test_large_numeric_budget(self):self.assertEqual(N.normalize('number','1'*129)['state'],'NUMERIC_DIGIT_BUDGET')
    def test_zero_and_empty_differ(self):self.assertEqual(N.normalize('number','0')['canonical'],'0');self.assertEqual(N.normalize('empty','')['state'],'EMPTY')
    def test_percentage_exact_ratio(self):self.assertEqual(N.normalize('percentage','3.15%')['exact_ratio'],'63/2000')
    def test_formulas_unevaluated(self):self.assertEqual(N.normalize('formula','=SUM(B2:B9)')['state'],'UNEVALUATED_FORMULA')
    def test_financial_stages_distinct(self):self.assertNotEqual(N.normalize('money_stage','refunded')['canonical'],N.normalize('money_stage','credited')['canonical'])
    def test_reimbursement_distinct(self):self.assertNotEqual(N.normalize('money_stage','reimbursed')['canonical'],N.normalize('money_stage','refunded')['canonical'])
    def test_paid_back_ambiguous(self):self.assertEqual(N.normalize('money_stage','paid back')['state'],'OPEN_STAGE_MEANING')
    def test_address_unit_and_direction(self):
        a={'line1':'1158 South 980 West','unit':'2'};b={**a,'unit':'3'}
        self.assertNotEqual(N.normalize('address',a)['canonical'],N.normalize('address',b)['canonical'])
    def test_raw_address_requires_review(self):self.assertEqual(N.normalize('address','1158 S 980 W #2')['state'],'ADDRESS_TEXT_REVIEW_OPEN')
    def test_profile_validation(self):
        with self.assertRaises(ValueError):N.normalize('number','1',{'date_order':'guess'})
    def test_falsey_nonobject_profiles_rejected(self):
        for invalid in (False,[],0,''):
            with self.assertRaises(ValueError):N.normalize('number','1',invalid)
    def test_invalid_currency_type_rejected(self):
        with self.assertRaises(ValueError):N.normalize('money','1',{'currency':[]})
    def test_negative_zero_canonical_value(self):self.assertEqual(N.normalize('money','USD -0.00')['canonical'],'0')

class GraphViews(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.s=GraphStore(self.root/'g.sqlite',create=True)
        self.s.apply(records_batch('seed',[{'id':'p','origin':'synthetic:normalization','locator':'row 1','text':'USD 1,234.56','data':{'cells':{'loan':'000673','amount':'USD 1,234.56'}}}]))
    def packet(self):return {'batch_id':'norm','expected_revision':self.s.head()[0],'records':[{'node_id':'p','fields':[{'name':'amount','kind':'money','path':['data','cells','amount'],'role':'requested'}]}]}
    def test_graph_raw_and_source_ids_preserved(self):
        before=self.s.snapshot();N.derive(self.s,self.packet());after=self.s.snapshot();p=next(n for n in after['nodes'] if n['id']=='p')
        self.assertEqual(p,next(n for n in before['nodes'] if n['id']=='p'))
        self.assertEqual(N.inspect(self.s,'p')['views'][0]['data']['normalization']['fields'][0]['value']['raw'],'USD 1,234.56')
    def test_graph_idempotency(self):N.derive(self.s,self.packet());self.assertEqual(N.derive(self.s,self.packet())['state'],'IDEMPOTENT')
    def test_source_revision_stales_view(self):
        N.derive(self.s,self.packet());from loom_research.learning import clean_node
        n=clean_node(next(n for n in self.s.snapshot()['nodes'] if n['id']=='p'));n['revision']+=1;n['text']='USD 2.00';self.s.apply({'schema':'loom.graph.batch.v1','batch_id':'revision','nodes':[n]})
        self.assertEqual(N.inspect(self.s,'p')['views'][0]['effective_status'],'stale')
    def test_graph_revision_conflict(self):
        p=self.packet();p['expected_revision']=0
        with self.assertRaises(ValueError):N.derive(self.s,p)
    def test_graph_span_locator(self):
        p=self.packet();p['records'][0]['fields']=[{'name':'amount','kind':'money','start':0,'end':len('USD 1,234.56')}];N.derive(self.s,p)
        self.assertEqual(N.inspect(self.s,'p')['views'][0]['data']['normalization']['fields'][0]['source_span']['quote'],'USD 1,234.56')
    def test_derived_view_keeps_search_slots(self):
        N.derive(self.s,self.packet());from loom_research.indexed import search
        self.assertEqual(search(self.s,'USD')['hits'][0]['id'],'p')
    def test_normalized_amount_search_returns_source(self):
        N.derive(self.s,self.packet());from loom_research.indexed import normalized_search
        r=normalized_search(self.s,'1234.56');self.assertEqual(r['hits'][0]['id'],'p')
        self.assertTrue(r['hits'][0]['normalization_matches'])
    def test_hard_clause_keeps_existing_token_semantics(self):
        N.derive(self.s,self.packet());from loom_research.indexed import normalized_search
        self.assertEqual(normalized_search(self.s,'+1234.56')['hits'][0]['id'],'p')
    def test_hard_clause_rejects_normalized_locale_alias(self):
        self.s.apply(records_batch('europe',[{'id':'eu','origin':'synthetic:eu','locator':'row 1','text':'EUR 1.234,56','data':{'cells':{'amount':'EUR 1.234,56'}}}]))
        packet=self.packet();packet['profile']={'decimal_separator':',','group_separator':'.'};packet['records'][0]['node_id']='eu'
        N.derive(self.s,packet);from loom_research.indexed import normalized_search
        self.assertEqual(normalized_search(self.s,'+1234.56')['hits'],[])
    def test_normalized_search_rejects_stale_sidecar(self):
        N.derive(self.s,self.packet());from loom_research.learning import clean_node
        from loom_research.indexed import normalized_search
        n=clean_node(next(n for n in self.s.snapshot()['nodes'] if n['id']=='p'));n['revision']+=1;n['text']='unrelated source';self.s.apply({'schema':'loom.graph.batch.v1','batch_id':'rev','nodes':[n]})
        self.assertEqual(normalized_search(self.s,'1234.56')['hits'],[])
    def test_duplicate_csv_columns_retained(self):
        p=self.root/'x.csv';p.write_text('Amount,Amount\n1,2\n')
        rows,_=intake.prepare({'batch_id':'csv','files':[{'path':str(p),'retain_duplicate_headers':True}]})
        self.assertEqual(rows[0]['data']['cells'],{'column:1':'1','column:2':'2'})
        self.assertEqual(len(rows[0]['data']['normalization']['fields']),2)
    def test_unknown_numeric_text_keeps_leading_zeros(self):
        r=N.metadata({'cells':{'unknown':'000673'}});self.assertEqual(r['fields'][0]['value']['canonical'],'000673')
    def test_csv_typed_fields_keep_raw(self):
        p=self.root/'x.csv';p.write_text('loan,amount,date\n000673,"USD 1,234.56",03/04/2018\n')
        rows,_=intake.prepare({'batch_id':'csv','files':[{'path':str(p)}]});v={f['name']:f['value'] for f in rows[0]['data']['normalization']['fields']}
        self.assertEqual(v['loan']['canonical'],'000673');self.assertEqual(v['amount']['minor_units'],123456);self.assertEqual(v['date']['state'],'AMBIGUOUS_DATE')
    def test_language_dispatch(self):
        from surd_integrations import call
        self.assertEqual(call('normalize_value',[{'kind':'identifier','value':'000673'}])['canonical'],'000673')

class Identity(unittest.TestCase):
    def node(self,ident,**data):return {'id':ident,'type':'entity','label':'Alex Person','data':{'entity_type':'person',**data},'identifiers':[]}
    def test_email_case_does_not_match(self):self.assertFalse(identity.compare(self.node('a',email='User@example.test'),self.node('b',email='user@example.test'))['features'][0]['match'])
    def test_phone_explicit_region_matches(self):
        a=self.node('a',phone='801-555-0101',normalization_profile={'phone_country':'US'});b=self.node('b',phone='+1 801 555 0101')
        self.assertTrue(identity.compare(a,b)['features'][0]['match'])
    def test_unknown_birth_dates_stay_open(self):self.assertFalse(identity.compare(self.node('a',birth_date='01/02/03'),self.node('b',birth_date='01/02/03'))['features'][0]['match'])
    def test_names_and_normalization_never_merge(self):self.assertEqual(identity.compare(self.node('a'),self.node('b'))['state'],'INSUFFICIENT_IDENTITY')


class FormattingRegression(unittest.TestCase):
    def test_cost_to_date_is_money(self):
        r=N.metadata({'cells':{'Cost to date':'15,400.00'}},{'currency':'USD'})
        self.assertEqual(r['fields'][0]['value']['kind'],'money')
        self.assertEqual(r['fields'][0]['value']['minor_units'],1540000)

    def test_updated_budget_is_money(self):
        r=N.metadata({'cells':{'Updated budget':'3,722.15'}},{'currency':'USD'})
        self.assertEqual(r['fields'][0]['value']['kind'],'money')
        self.assertEqual(r['fields'][0]['value']['minor_units'],372215)

    def test_stored_to_date_numeric_column_preserves_number(self):
        r=N.metadata({'sheet':'s','cells':{'K9':'15400.00'},'cell_types':{'K9':'n'},
                      'column_headers':{'K':'TOTAL COMPLETED AND STORED TO DATE (E+F)'}})
        self.assertEqual(r['fields'][0]['value']['kind'],'number')
        self.assertEqual(r['fields'][0]['value']['canonical'],'15400')

    def test_actual_statement_date_remains_a_date(self):
        r=N.metadata({'cells':{'Statement Date':'2026-10-13'}})
        self.assertEqual(r['fields'][0]['value']['kind'],'date')
        self.assertEqual(r['fields'][0]['value']['canonical'],'2026-10-13')

    def test_equivalent_decimal_spellings(self):
        self.assertEqual(N.normalize('money','USD 25,000.00')['canonical'],N.normalize('money','USD 25000')['canonical'])
    def test_exact_money_format_roundtrip(self):
        for cents in (0,1,10,99,100,120,123456,2500000,-1,-123456):
            self.assertEqual(N.normalize('money',f'USD {cents/100:,.2f}')['minor_units'],cents)
    def test_native_ratio_percentage(self):
        r=N.normalize('percentage','0.0315',{'percent_input':'ratio'});self.assertEqual(r['canonical'],'3.15');self.assertEqual(r['exact_ratio'],'63/2000')
    def test_native_percentage_format_beats_value_header(self):
        r=N.metadata({'sheet':'s','cells':{'C9':'0.0315'},'cell_types':{'C9':'n'},'column_headers':{'C':'Scheduled value'},'number_formats':{'C9':'0.00%'}})
        self.assertEqual(r['fields'][0]['value']['kind'],'percentage');self.assertEqual(r['fields'][0]['value']['canonical'],'3.15')
    def test_literal_percent_format_is_not_ratio(self):
        r=N.metadata({'sheet':'s','cells':{'C9':'3'},'cell_types':{'C9':'n'},'column_headers':{'C':'Value'},'number_formats':{'C9':'0"%"'}},{'currency':'USD'})
        self.assertEqual(r['fields'][0]['value']['kind'],'money')
    def test_header_label_is_not_identifier(self):
        r=N.metadata({'sheet':'s','row':8,'header_row':8,'cells':{'A8':'ITEM\nNO.'},'column_headers':{'A':'ITEM\nNO.'}})
        self.assertEqual(r['fields'][0]['value']['kind'],'text')

if __name__=='__main__':unittest.main()
