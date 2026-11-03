"""Frozen real-data evaluation: independent metrics, test split only, no tuning."""
import argparse,hashlib,json,math,platform,statistics,sys,time,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--runtime',required=True);p.add_argument('--dataset',required=True);p.add_argument('--out',required=True);a=p.parse_args()
sys.path.insert(0,str(Path(a.runtime).resolve()))
from surd_search import SearchIndex
started=time.perf_counter()
with zipfile.ZipFile(a.dataset) as z:
    prefix=z.namelist()[0].split('/')[0]
    docs=[json.loads(x) for x in z.read(prefix+'/corpus.jsonl').decode().splitlines()]
    queries={q['_id']:q['text'] for q in map(json.loads,z.read(prefix+'/queries.jsonl').decode().splitlines())}
    qrels={}
    for line in z.read(prefix+'/qrels/test.tsv').decode().splitlines()[1:]:
        q,d,s=line.split('\t');qrels.setdefault(q,{})[d]=int(s)
rows=[{'id':d['_id'],'title':d.get('title',''),'text':d['text'],'locator':'BEIR:'+prefix+':'+d['_id']} for d in docs]
ix=SearchIndex(rows);nd=[];rec=[];rr=[];times=[];ranks=[];snippets=0
for qid,rel in sorted(qrels.items()):
    t=time.perf_counter();r=ix.search(queries[qid],{'k':10,'explain':False});times.append(time.perf_counter()-t);ids=[h['id'] for h in r['hits']]
    positives={d for d,g in rel.items() if g>0};ideal=sum((2**g-1)/math.log2(i+2) for i,g in enumerate(sorted(rel.values(),reverse=True)[:10]))
    dcg=sum((2**rel.get(d,0)-1)/math.log2(i+2) for i,d in enumerate(ids));nd.append(dcg/ideal if ideal else 0);rec.append(len(positives&set(ids))/len(positives))
    rr.append(next((1/(i+1) for i,d in enumerate(ids) if d in positives),0))
    for hit in r['hits']:
        doc=ix.document(hit['id']);sn=hit['snippet'];assert doc['text'][sn['start']:sn['end']]==sn['text'];snippets+=1
    ranks.append({'qid':qid,'query':queries[qid],'relevant':rel,'hits':ids,'ndcg10':nd[-1],'recall10':rec[-1]})
report={'ok':True,'host':platform.node(),'dataset':prefix,'documents':len(docs),'queries':len(qrels),
        'recall10':statistics.mean(rec),'ndcg10':statistics.mean(nd),'mrr10':statistics.mean(rr),
        'complete_misses':sum(x==0 for x in rec),'p50_ms':statistics.median(times)*1000,
        'p95_ms':sorted(times)[math.ceil(len(times)*.95)-1]*1000,'max_ms':max(times)*1000,
        'snippets_exact':snippets,'manifest':ix.manifest(),'seconds':time.perf_counter()-started,
        'dataset_sha256':hashlib.sha256(Path(a.dataset).read_bytes()).hexdigest(),'rankings':ranks}
Path(a.out).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='rankings'}))
