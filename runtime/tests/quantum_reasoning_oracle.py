"""Independent floating state-vector gate simulation; no production recurrence."""
import argparse
import json
import math
from pathlib import Path
import platform
import random
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from surd_quantum_core import circuit


def hadamards(state):
    state = state[:]
    stride = 1
    while stride < len(state):
        for base in range(0,len(state),2*stride):
            for j in range(stride):
                a,b = state[base+j],state[base+j+stride]
                state[base+j]=(a+b)/math.sqrt(2)
                state[base+j+stride]=(a-b)/math.sqrt(2)
        stride *= 2
    return state


def grover_step(state, marked):
    state = [-a if i in marked else a for i, a in enumerate(state)]
    state = hadamards(state)
    state = [a if i == 0 else -a for i, a in enumerate(state)]
    return hadamards(state)


def run(cases, seed, max_wires=8):
    rng=random.Random(seed);checks=0;failures=[];start=time.monotonic()
    def check(name,actual,expected):
        nonlocal checks
        checks += 1
        if abs(actual-expected)>1e-10 and len(failures)<20:
            failures.append({"check":name,"actual":actual,"expected":expected})
    for case in range(cases):
        bits=rng.randint(1,max_wires);n=1<<bits
        marked=set(rng.sample(range(n),rng.randrange(n+1)))
        turns=None if case%2 else rng.randrange(17)
        result=circuit(bits,marked,turns)
        state=hadamards([1+0j]+[0j]*(n-1))
        check("initial",sum(abs(state[i])**2 for i in marked),float(result["initial_marked_share"]["numerator"])/result["initial_marked_share"]["denominator"])
        history = [sum(abs(state[i])**2 for i in marked)]
        simulated = 0
        for _ in range(16 if turns is None else turns):
            candidate = grover_step(state, marked)
            share = sum(abs(candidate[i])**2 for i in marked)
            simulated += 1
            # Tolerance accounts for floating gate noise at exact plateaus.
            if turns is None and share <= history[-1] + 1e-12:
                break
            state = candidate
            history.append(share)
        check("selected iterations", result["iterations"], len(history)-1)
        check("selection steps", result["selection_simulated_steps"], simulated)
        check("history length", len(result["history"]), len(history))
        for actual, expected in zip(result["history"], history):
            check("history share", actual["numerator"]/actual["denominator"], expected)
        for i,row in enumerate(result["state"]):
            c=row["amplitude_coefficient"]
            check("amplitude",state[i].real,(c["numerator"]/c["denominator"])/math.sqrt(n))
            p=row["measurement_share"]
            check("probability",abs(state[i])**2,p["numerator"]/p["denominator"])
        state=hadamards([1+0j]+[0j]*(n-1))
        state=[-a if i in marked else a for i,a in enumerate(state)]
        state=hadamards(state)
        for i,row in enumerate(result["interference"]["spectrum"]):
            a=row["amplitude"]
            check("interference",state[i].real,a["numerator"]/a["denominator"])
        check("norm",sum(abs(a)**2 for a in state),1)
    return {"schema":"surd.quantum.circuit.oracle.v1","ok":not failures,"cases":cases,"checks":checks,"seed":seed,"max_wires":max_wires,
            "failures":failures,"host":platform.node(),"seconds":round(time.monotonic()-start,4),
            "scope":"Independent floating H-gate state-vector simulation of exact Grover and Walsh circuits; synthetic marked sets, no case truth or quantum hardware."}


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--cases",type=int,default=1000);parser.add_argument("--seed",type=int,default=20261012)
    parser.add_argument("--max-wires",type=int,choices=range(1,9),default=8)
    args=parser.parse_args()
    if not 1<=args.cases<=10000: parser.error("cases must be 1..10000")
    result=run(args.cases,args.seed,args.max_wires);print(json.dumps(result,indent=2));sys.exit(0 if result["ok"] else 1)
