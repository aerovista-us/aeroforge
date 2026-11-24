"""AeroForge CLI. Never invokes hosted Actions workflows or modifies GitHub."""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
from .storage import connect,home,stats
from .inventory import scan_fixture,scan_github
from .worker import enqueue,run_next,KINDS
from .dashboard import build


def cli(argv=None):
    p=argparse.ArgumentParser(description='AeroForge: zero-hosted-minute workflow audit and bounded static validation')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    a=sub.add_parser('scan-github');a.add_argument('--owner',default='aerovista-us');a.add_argument('--max-repos',type=int,default=250)
    a=sub.add_parser('import-fixture');a.add_argument('path')
    a=sub.add_parser('enqueue');a.add_argument('--kind',required=True,choices=KINDS);a.add_argument('--target',required=True)
    sub.add_parser('run-next');sub.add_parser('dashboard');sub.add_parser('status')
    a=p.parse_args(argv)
    con=connect()
    try:
        if a.command=='init':result={'state':str(home()),'tables':stats(con)}
        elif a.command=='scan-github':
            if not 1<=a.max_repos<=1000:raise ValueError('max-repos must be between 1 and 1000')
            result=scan_github(con,a.owner,a.max_repos)
        elif a.command=='import-fixture':
            path=pathlib.Path(a.path).expanduser().resolve(strict=True)
            if path.stat().st_size>2_000_000:raise ValueError('Fixture too large')
            result=scan_fixture(con,json.loads(path.read_text('utf-8')))
        elif a.command=='enqueue':result={'queued_job_id':enqueue(con,a.kind,a.target)}
        elif a.command=='run-next':result=run_next(con) or {'queue':'empty'}
        elif a.command=='status':result={'state':str(home()),'tables':stats(con),
            'jobs':[dict(r) for r in con.execute('SELECT id,kind,status,outcome FROM jobs ORDER BY id DESC LIMIT 12')]}
        elif a.command=='dashboard':result={'dashboard':str(build(con))}
        if a.command not in ('status','dashboard','init'):
            result['dashboard']=str(build(con))
        print(json.dumps(result,indent=2))
    finally:con.close()

if __name__=='__main__':
    try:cli()
    except (ValueError,PermissionError,OSError,json.JSONDecodeError,RuntimeError) as e:
        print('AeroForge error:',str(e),file=sys.stderr)
        sys.exit(2)
