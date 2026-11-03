"""Invented records and finite credit model for portable research tests."""
import copy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def batch():
    source=lambda ident,origin:{'id':ident,'type':'source','origin':origin,'locator':'SYNTHETIC:'+ident,
                               'clocks':{'event':['2020-01-01','2020-01-01'],'receipt':{'Actor A':['2020-01-02','2020-01-02']}}}
    return {'schema':'loom.graph.batch.v1','batch_id':'synthetic-base','nodes':[
        source('S1','ORIGIN1'),source('S2','ORIGIN2'),
        {'id':'A','type':'passage','label':'Blue invoice','text':'A blue invoice refers to instrument T1.',
         'source_ids':['S1'],'identifiers':[{'kind':'instrument','namespace':'SYN-account','value':'T1','unique':True}],
         'clocks':{'event':['2020-01-01','2020-01-01'],'receipt':{'Actor A':['2020-01-02','2020-01-02']}}},
        {'id':'B','type':'passage','label':'Payment confirmation','text':'Payment settled to the vendor.',
         'source_ids':['S2'],'identifiers':[{'kind':'instrument','namespace':'SYN-account','value':'T1','unique':True}],
         'clocks':{'event':['2020-01-03','2020-01-03'],'receipt':{'Actor A':['2020-02-01','2020-02-01']}}}],
        'edges':[{'id':'AB','subject':'A','predicate':'refers_to','object':'B','source_ids':['S1','S2'],
                  'clocks':{'event':['2020-01-01','2020-01-03'],'receipt':{'Actor A':['2020-02-01','2020-02-01']}}}]}


def credit_graph():
    model=json.loads((ROOT/'examples/data/qulap-credit.model.json').read_text())
    nodes=[{'id':s['id'],'type':'source','origin':s['lineage_id'],'locator':s['locator'],'label':s['id']} for s in model['sources']]
    selected=[]
    for plural,kind in [('variables','variable'),('constraints','constraint'),('metrics','metric'),('propositions','proposition')]:
        for entry in model[plural]:
            ident=kind+':'+entry['id'];selected.append(ident)
            src=set(entry.get('source_ids',[]))|set(entry.get('bridge',{}).get('source_ids',[]))
            nodes.append({'id':ident,'type':kind,'label':entry['id'],'status':'annotated','source_ids':sorted(src),
                          'data':{'reviewed':True,kind:copy.deepcopy(entry)}})
    nodes.append({'id':'credit-passage','type':'passage','label':'Credit disposition alternatives',
                  'text':'The synthetic credit memo allows applied or refunded alternatives.','source_ids':['S_credit']})
    return {'schema':'loom.graph.batch.v1','batch_id':'synthetic-credit','nodes':nodes},{'id':'credit','nodes':selected,'source_tier':'synthetic'}


def run_packet():
    b,spec=credit_graph()
    return {'schema':'loom.research.run.v1','id':'synthetic-credit-research','batch':b,
            'questions':[{'id':'credit','query':'credit disposition'}],
            'model':spec,'plan':{'objective':'unresolved_credit','max_bundle':2}}
