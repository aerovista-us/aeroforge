"""Fail-closed local validation: AST/JSON/workflow metadata only, no code execution."""
from __future__ import annotations
import ast
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import time
from .storage import home,now
from .detector import analyze

KINDS=('python_syntax','json_validate','workflow_audit','repo_inventory')
SKIP={'.git','.venv','venv','node_modules','dist','build','__pycache__','.next','coverage','.cache'}
MAX_FILES=1200
MAX_BYTES=256_000
MAX_SECONDS=45


def _allowed_roots():
    raw=os.environ.get('AEROFORGE_ALLOWED_ROOTS','')
    return [pathlib.Path(x).expanduser().resolve() for x in raw.split(os.pathsep) if x.strip()]


def validate_target(target):
    roots=_allowed_roots()
    if not roots:raise PermissionError('AEROFORGE_ALLOWED_ROOTS must be explicitly configured')
    p=pathlib.Path(target).expanduser().resolve(strict=True)
    if not p.is_dir() or not any(p==r or r in p.parents for r in roots):
        raise PermissionError('Target outside allowed roots or not a directory')
    return p


def enqueue(con,kind,target):
    if kind not in KINDS:raise ValueError('Only non-executing check profiles are supported')
    p=validate_target(target)
    cursor=con.execute('INSERT INTO jobs(kind,target,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (kind,str(p),'queued',now(),now()))
    con.commit()
    return cursor.lastrowid


def _walk(root):
    files=[];skipped=0
    for directory,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in SKIP and not (pathlib.Path(directory)/d).is_symlink())
        for name in sorted(names):
            p=pathlib.Path(directory)/name
            if name.startswith('.env') or p.is_symlink():skipped+=1;continue
            try:
                if p.stat().st_size>MAX_BYTES or not p.resolve().is_relative_to(root):
                    skipped+=1;continue
            except (OSError,ValueError):skipped+=1;continue
            if len(files)==MAX_FILES:skipped+=1;continue
            files.append(p)
    return files,skipped


def check(kind,root):
    start=time.monotonic()
    files,skipped=_walk(root)
    rows=[]
    fail=0
    seen=0
    for p in files:
        if time.monotonic()-start>MAX_SECONDS:raise TimeoutError('Inspection exceeded wall-clock limit')
        rel=p.relative_to(root).as_posix()
        if kind=='python_syntax' and p.suffix!='.py':continue
        if kind=='json_validate' and p.suffix!='.json':continue
        if kind=='workflow_audit' and (not rel.startswith('.github/workflows/') or p.suffix not in ('.yml','.yaml')):continue
        seen+=1
        item={'file':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        if kind=='repo_inventory':
            rows.append(item)
            continue
        try:
            data=p.read_text('utf-8')
            if kind=='python_syntax':ast.parse(data,filename=rel)
            elif kind=='json_validate':json.loads(data)
            elif kind=='workflow_audit':
                a=analyze('local',rel,data)
                item['triggers']=a['triggers']
                item['findings']=a['findings']
            item['status']='pass'
        except (SyntaxError,UnicodeError,json.JSONDecodeError,ValueError) as e:
            # Do not echo source code or user content from parser exceptions.
            item['status']='fail';item['reason']=type(e).__name__
            fail+=1
        rows.append(item)
    return {'kind':kind,'target':str(root),'checked':seen,'failed':fail,
            'skipped':skipped,'truncated':skipped>0,'files':rows,'generated_at':now(),
            'mode':'static inspection only: no repository code executed'}


def _write_json(path,data):
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    if os.name=='posix':os.chmod(path,0o600)


def run_next(con):
    """Single-worker advisory lock, atomic queue claim, durable verified receipt."""
    root=home();root.mkdir(parents=True,exist_ok=True)
    lockpath=root/'worker.lock'
    with open(lockpath,'a+b') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another AeroForge worker is active')
        con.execute('BEGIN IMMEDIATE')
        row=con.execute("SELECT id,kind,target FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            con.rollback();return None
        job=dict(row)
        con.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?",(now(),job['id']))
        con.commit()
        run_dir=root/'runs'/f"{job['id']:06d}"
        run_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            target=validate_target(job['target'])
            evidence=check(job['kind'],target)
            evidence_path=run_dir/'evidence.json'
            _write_json(evidence_path,evidence)
            digest=hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            status='passed' if evidence['failed']==0 else 'failed'
            receipt={'job_id':job['id'],'status':status,'profile':job['kind'],
                     'evidence_sha256':digest,'evidence':'evidence.json','created_at':now(),
                     'scope':'local static checks only; no code executed, no deployment'}
            _write_json(run_dir/'receipt.json',receipt)
            con.execute('UPDATE jobs SET status=?,report_dir=?,updated_at=?,outcome=? WHERE id=?',
                        (status,str(run_dir),now(),f"{evidence['checked']} inspected; {evidence['failed']} failed",job['id']))
            con.execute('INSERT INTO events(job_id,at,status,detail) VALUES(?,?,?,?)',
                        (job['id'],now(),status,'Evidence and receipt written'))
            con.commit()
            return receipt
        except Exception as exc:
            status='blocked'
            # Sanitize failure message: not raw exceptions or source code.
            summary=type(exc).__name__
            con.execute('UPDATE jobs SET status=?,report_dir=?,updated_at=?,outcome=? WHERE id=?',
                        (status,str(run_dir),now(),summary,job['id']))
            con.execute('INSERT INTO events(job_id,at,status,detail) VALUES(?,?,?,?)',
                        (job['id'],now(),status,summary))
            con.commit()
            return {'job_id':job['id'],'status':status,'reason':summary}
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)
