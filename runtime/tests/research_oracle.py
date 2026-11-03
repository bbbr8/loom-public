"""Independent finite-bundle enumeration and event-store reference/concurrency probes."""
import argparse
from fractions import Fraction
from itertools import product,combinations
import json
from pathlib import Path
import platform
import random
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loom_research.planner import plan
from loom_research.store import GraphStore

ROOT=Path(__file__).resolve().parents[1]


def planner_oracle(trials):
    rng=random.Random(2026101301);checks=0
    for _ in range(trials):
        n=rng.randint(2,4);domains=[sorted(rng.sample(range(-2,5),rng.randint(1,3))) for i in range(n)]
        cap=rng.randint(-3,8);weights=[rng.randint(-2,3) for i in range(n)]
        model={'schema':'qulap.model.v1','source_tier':'synthetic',
               'variables':[{'id':'x'+str(i),'domain':d,'unit':'integer'} for i,d in enumerate(domains)],
               'constraints':[{'id':'cap','expr':' + '.join('x'+str(i) for i in range(n))+' <= '+str(cap)}],
               'metrics':[{'id':'value','expr':' + '.join('('+str(w)+' * x'+str(i)+')' for i,w in enumerate(weights)),'unit':'integer'}],
               'sources':[],'propositions':[]}
        records=[{'id':'r'+str(i),'variables':['x'+str(i)],'cost':str(rng.randint(1,4))} for i in range(n)]
        worlds=[w for w in product(*domains) if sum(w)<=cap]
        result=plan(model,records,objective='value',max_bundle=3,seconds=5)
        if not worlds:
            assert result['state']=='INCONSISTENT_MODEL';checks+=1;continue
        assert result['exhaustive_within_declared_bundle_limit'];checks+=1
        metric=lambda w:sum(v*c for v,c in zip(w,weights))
        width=max(map(metric,worlds))-min(map(metric,worlds));actual={tuple(x['records']):x for x in result['bundles']}
        for size in range(1,min(3,n)+1):
            for bundle in combinations(range(n),size):
                grouped={}
                for w in worlds:grouped.setdefault(tuple(w[i] for i in bundle),[]).append(metric(w))
                worst=max(max(v)-min(v) for v in grouped.values());ids=tuple('r'+str(i) for i in bundle)
                got=actual[ids];assert got['guaranteed_gain']==width-worst,(model,bundle,got,worst)
                assert Fraction(got['cost'])==sum(Fraction(records[i]['cost']) for i in bundle)
                assert Fraction(got['gain_per_cost'])==Fraction(width-worst)/sum(Fraction(records[i]['cost']) for i in bundle)
                checks+=3
    return checks


def store_oracle(events):
    rng=random.Random(2026101302);checks=0
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'graph';store=GraphStore(path,create=True);reference={};history=[]
        for step in range(events):
            ident='s'+str(rng.randrange(12));rev=reference.get(ident,(0,False))[0]+1;active=rng.random()>.25
            packet={'schema':'loom.graph.batch.v1','batch_id':'b'+str(step)}
            if active:packet['nodes']=[{'id':ident,'type':'source','label':'State '+str(step),'origin':'o'+str(step%3),'locator':'SYN:'+str(step),'revision':rev}]
            else:packet['remove_nodes']=[{'id':ident,'revision':rev}]
            store.apply(packet);reference[ident]=(rev,active);history.append(dict(reference))
            snap=store.snapshot();actual={n['id']:n['revision'] for n in snap['nodes']}
            assert actual=={k:v[0] for k,v in reference.items() if v[1]};checks+=1
        for seq in range(1,len(history)+1,max(1,len(history)//20)):
            actual={n['id']:n['revision'] for n in store.snapshot(revision=seq)['nodes']}
            assert actual=={k:v[0] for k,v in history[seq-1].items() if v[1]};checks+=1
        replay=GraphStore.replay(Path(td)/'copy',store.journal());assert replay.snapshot()==store.snapshot();checks+=1
        assert store.verify()['ok'];checks+=1
    return checks


def concurrency():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'concurrent';GraphStore(path,create=True)
        code="""import sys
sys.path.insert(0,sys.argv[1])
from loom_research.store import GraphStore
s=GraphStore(sys.argv[2]);worker=sys.argv[3]
for n in range(15):
 ident=worker+'-'+str(n)
 s.apply({'schema':'loom.graph.batch.v1','batch_id':ident,'nodes':[{'id':ident,'type':'source','origin':worker,'locator':'SYN:'+ident}]})
"""
        processes=[subprocess.Popen([sys.executable,'-c',code,str(ROOT),str(path),str(w)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for w in range(4)]
        for p in processes:
            out,err=p.communicate(timeout=30);assert p.returncode==0,err
        s=GraphStore(path);assert len(s.snapshot()['nodes'])==60;assert s.verify()['ok']
    return 6


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--trials',type=int,default=1000);parser.add_argument('--events',type=int,default=250);parser.add_argument('--out',required=True)
    a=parser.parse_args();started=time.monotonic()
    try:
        checks=planner_oracle(a.trials)+store_oracle(a.events)+concurrency()
        report={'ok':True,'checks':checks,'planner_trials':a.trials,'reference_store_events':a.events,'concurrent_writers':4}
    except Exception as exc:
        import traceback
        report={'ok':False,'error':str(exc),'traceback':traceback.format_exc()}
    report.update(host=platform.node(),seconds=time.monotonic()-started,scope='Synthetic exact-model arithmetic, independent event projection and concurrent writer/replay checks; no native case accuracy claim.')
    Path(a.out).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(0 if report['ok'] else 1)
