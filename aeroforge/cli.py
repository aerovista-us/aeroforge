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
from .usage import scan_usage, latest_usage
from .usage_fixture import read_usage_fixture
from .sandbox import submit as sandbox_submit, run_next as sandbox_run_next, queue_status as sandbox_queue_status, SandboxDenied


def cli(argv=None):
    p=argparse.ArgumentParser(description='AeroForge: zero-hosted-minute workflow audit and bounded static validation')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    a=sub.add_parser('scan-github');a.add_argument('--owner',default='aerovista-us');a.add_argument('--max-repos',type=int,default=250)
    a=sub.add_parser('scan-usage',help='Read recent Actions runs/job timings without triggering workflows')
    a.add_argument('--owner',default='aerovista-us')
    a.add_argument('--days',type=int,default=30)
    a.add_argument('--max-repos',type=int,default=100)
    a.add_argument('--max-runs-per-repo',type=int,default=12)
    a.add_argument('--max-requests',type=int,default=400)
    a.add_argument('--start-after',default=None,help='Resume after this full repo name; slice is marked partial')
    sub.add_parser('usage-report',help='Print latest usage report with explicit coverage limitations')
    a=sub.add_parser('import-usage-fixture',help='Offline synthetic example; never claims live usage');a.add_argument('path')
    a=sub.add_parser('import-fixture');a.add_argument('path')
    a=sub.add_parser('enqueue');a.add_argument('--kind',required=True,choices=KINDS);a.add_argument('--target',required=True)
    a=sub.add_parser('sandbox-submit',help='Queue pinned Git commit for sandbox unittest execution')
    a.add_argument('--repo',required=True);a.add_argument('--commit',required=True)
    a.add_argument('--profile',choices=['python_unittest'],default='python_unittest')
    a=sub.add_parser('sandbox-run-next',help='Execute one explicitly approved isolated container job')
    a.add_argument('--approve',action='store_true');a.add_argument('--memory-mib',type=int,default=256)
    a.add_argument('--cpus',type=float,default=0.5);a.add_argument('--timeout-seconds',type=int,default=90)
    a.add_argument('--image-id',default=None)
    sub.add_parser('sandbox-status',help='List queued and completed sandbox jobs')
    sub.add_parser('run-next');sub.add_parser('dashboard');sub.add_parser('status')
    a=p.parse_args(argv)
    con=connect()
    try:
        if a.command=='init':result={'state':str(home()),'tables':stats(con)}
        elif a.command=='scan-github':
            if not 1<=a.max_repos<=1000:raise ValueError('max-repos must be between 1 and 1000')
            result=scan_github(con,a.owner,a.max_repos)
        elif a.command=='scan-usage':
            result=scan_usage(con,owner=a.owner,days=a.days,max_repos=a.max_repos,
                              max_runs_per_repo=a.max_runs_per_repo,max_requests=a.max_requests,
                              start_after=a.start_after)
        elif a.command=='import-usage-fixture':
            result=scan_usage(con, owner='example',days=30,max_repos=50,max_runs_per_repo=20,
                              max_requests=200,api=read_usage_fixture(a.path),source='fixture')
        elif a.command=='usage-report':result=latest_usage(con) or {'usage':'not scanned'}
        elif a.command=='import-fixture':
            path=pathlib.Path(a.path).expanduser().resolve(strict=True)
            if path.stat().st_size>2_000_000:raise ValueError('Fixture too large')
            result=scan_fixture(con,json.loads(path.read_text('utf-8')))
        elif a.command=='sandbox-submit':result={'queued_sandbox_job_id':sandbox_submit(con,a.repo,a.commit,a.profile)}
        elif a.command=='sandbox-run-next':result=sandbox_run_next(con,approve=a.approve,
            memory_mib=a.memory_mib,cpus=a.cpus,timeout_seconds=a.timeout_seconds,image_id=a.image_id)
        elif a.command=='sandbox-status':result={'sandbox_jobs':sandbox_queue_status(con)}
        elif a.command=='enqueue':result={'queued_job_id':enqueue(con,a.kind,a.target)}
        elif a.command=='run-next':result=run_next(con) or {'queue':'empty'}
        elif a.command=='status':result={'state':str(home()),'tables':stats(con),
            'jobs':[dict(r) for r in con.execute('SELECT id,kind,status,outcome FROM jobs ORDER BY id DESC LIMIT 12')]}
        elif a.command=='dashboard':result={'dashboard':str(build(con))}
        if a.command not in ('status','dashboard','init','usage-report'):
            result['dashboard']=str(build(con))
        print(json.dumps(result,indent=2))
    finally:con.close()

def main():
    try:
        cli()
    except (ValueError,PermissionError,OSError,json.JSONDecodeError,RuntimeError) as e:
        print('AeroForge error:',str(e),file=sys.stderr)
        sys.exit(2)


if __name__=='__main__':
    main()
