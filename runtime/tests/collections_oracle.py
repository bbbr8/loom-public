"""Independent exact-rational reference pipelines over seeded synthetic records."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import platform
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import surd


def literal(value):
    if value is None: return 'none'
    if type(value) is bool: return 'true' if value else 'false'
    if isinstance(value, Fraction): return '('+str(value.numerator)+'/'+str(value.denominator)+')'
    if type(value) is str: return json.dumps(value)
    if isinstance(value, list): return '['+', '.join(literal(x) for x in value)+']'
    if isinstance(value, dict): return '{'+', '.join(json.dumps(k)+': '+literal(v) for k,v in value.items())+'}'
    raise TypeError(type(value))


def native(value):
    if value is surd.NONE: return None
    if isinstance(value,surd.R2):
        if not value.is_rational(): raise ValueError('oracle expects rational values')
        return value.a
    if isinstance(value,list): return [native(x) for x in value]
    if isinstance(value,dict): return {k:native(v) for k,v in value.items()}
    return value


def same(a,b):
    """Linear structural reference comparison; no kernel key hashing/pruning."""
    if type(a) is not type(b): return False
    if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
    return a==b


def reference(rows,size):
    groups,first=[],[]
    for row in rows:
        existing=next((group for group in groups if same(group['key'],row['key'])),None)
        if existing is None:
            existing={'key':row['key'],'items':[]};groups.append(existing);first.append(row)
        existing['items'].append(row)
    totals=[Fraction(0)]
    for row in rows: totals.append(totals[-1]+row['value'])
    return {'groups':groups,'first':first,'sides':{'matched':[r for r in rows if r['value']>=0],
            'rest':[r for r in rows if r['value']<0]},'totals':totals,
            'flat':[v for r in rows for v in (r['id'],r['locator'])],
            'batches':[rows[i:i+size] for i in range(0,len(rows),size)],'joined':rows,'rows':rows}


def main():
    p=argparse.ArgumentParser();p.add_argument('--cases',type=int,default=2000)
    p.add_argument('--seed',type=int,default=2026101207);p.add_argument('--output');args=p.parse_args()
    if not 1<=args.cases<=10000: p.error('--cases must be in 1..10000')
    rng=random.Random(args.seed);started=time.perf_counter();checks=0;failures=[]
    keys=[None,True,False,Fraction(0),Fraction(1),Fraction(1,3),'0','1','alpha','beta',
          [Fraction(1),'alpha'],[True,'alpha'],{'a':Fraction(1),'b':'x'},{'b':'x','a':Fraction(1)}]
    for case in range(args.cases):
        rows=[{'id':str(i),'key':rng.choice(keys),'value':Fraction(rng.randint(-100,100),rng.randint(1,10)),
               'locator':'fixture/'+str(case)+':'+str(i)} for i in range(rng.randrange(25))]
        size=rng.randrange(1,9)
        source='let rows = '+literal(rows)+'''\nlet groups = group_by(fn(row)=>row["key"],rows)
let first = unique_by(fn(row)=>row["key"],rows)
let sides = partition(fn(row)=>row["value"]>=0,rows)
let totals = scan(fn(acc,row)=>acc+row["value"],rows,0)
let flat = flat_map(fn(row)=>[row["id"],row["locator"]],rows)
let batches = chunk(rows,'''+str(size)+''')
let joined = flatten(batches)'''
        expected=reference(rows,size)
        try:
            it=surd.Interp(echo=False,max_steps=50000).run(source)
            for name,want in expected.items():
                checks+=1
                if not same(native(it.globals[name]),want):
                    failures.append({'case':case,'check':name,'source':source})
        except Exception as error:
            failures.append({'case':case,'error':repr(error),'source':source})
    report={'schema':'surd.collections.oracle.v1','ok':not failures,'cases':args.cases,'checks':checks,
            'seed':args.seed,'host':platform.node(),'elapsed_seconds':time.perf_counter()-started,
            'scope':'synthetic exact-rational pipelines; linear typed-key reference grouping, source locator preservation and all seven collection operations',
            'failure_count':len(failures),'failures':failures[:10]}
    text=json.dumps(report,sort_keys=True)
    if args.output: Path(args.output).write_text(text+'\n')
    print(text);return 0 if report['ok'] else 1


if __name__=='__main__':sys.exit(main())
