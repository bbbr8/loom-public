"""Local operator actions. Stop the service before job archival; locks enforce it."""
import argparse
import hashlib
import json
import sys
from jobs import Jobs


def main():
    parser=argparse.ArgumentParser(prog='python admin.py')
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('archive-jobs');p.add_argument('--data-dir',required=True);p.add_argument('--out',required=True);p.add_argument('--before',type=float,required=True)
    p=subs.add_parser('token-hash');p.add_argument('--stdin',action='store_true',required=True)
    a=parser.parse_args()
    if a.command=='token-hash':
        token=sys.stdin.readline(4096).strip()
        if not 32<=len(token)<=1024:parser.error('token must contain 32..1024 characters')
        print(hashlib.sha256(token.encode()).hexdigest());return 0
    queue=Jobs(a.data_dir,lambda *args,**kwargs: (_ for _ in ()).throw(RuntimeError('operator does not execute jobs')),workers=0)
    try:print(json.dumps(queue.archive(a.out,a.before),indent=2))
    finally:queue.close()
    return 0


if __name__=='__main__':sys.exit(main())
