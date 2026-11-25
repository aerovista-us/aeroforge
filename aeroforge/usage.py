"""Read-only GitHub Actions usage observation.

Job wall-clock duration is NOT the same as GitHub-billed minutes. No changes to
workflows, Actions configuration, billing, secrets or repositories are made.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re

from .github import GithubReader, GithubError
from .storage import home, now

MAX_RESPONSE_ROWS = 100


def ensure_schema(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS usage_scans (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner TEXT NOT NULL,
      source TEXT NOT NULL,
      started_at TEXT NOT NULL,
      completed_at TEXT,
      window_since TEXT NOT NULL,
      repositories_seen INTEGER NOT NULL DEFAULT 0,
      repositories_with_history INTEGER NOT NULL DEFAULT 0,
      runs_seen INTEGER NOT NULL DEFAULT 0,
      jobs_seen INTEGER NOT NULL DEFAULT 0,
      request_count INTEGER NOT NULL DEFAULT 0,
      errors INTEGER NOT NULL DEFAULT 0,
      partial INTEGER NOT NULL DEFAULT 1,
      notes TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE IF NOT EXISTS usage_runs (
      run_id INTEGER NOT NULL,
      repo TEXT NOT NULL,
      workflow_id INTEGER,
      workflow_name TEXT NOT NULL,
      event TEXT NOT NULL,
      status TEXT NOT NULL,
      conclusion TEXT,
      created_at TEXT,
      html_url TEXT,
      scan_id INTEGER NOT NULL,
      PRIMARY KEY(scan_id,run_id)
    );
    CREATE TABLE IF NOT EXISTS usage_jobs (
      job_id INTEGER NOT NULL,
      run_id INTEGER NOT NULL,
      repo TEXT NOT NULL,
      status TEXT,
      conclusion TEXT,
      runner_kind TEXT NOT NULL,
      duration_seconds INTEGER,
      started_at TEXT,
      completed_at TEXT,
      scan_id INTEGER NOT NULL,
      PRIMARY KEY(scan_id,job_id),
      FOREIGN KEY(scan_id,run_id) REFERENCES usage_runs(scan_id,run_id)
    );
    CREATE INDEX IF NOT EXISTS usage_runs_scan ON usage_runs(scan_id,repo);
    CREATE INDEX IF NOT EXISTS usage_jobs_scan ON usage_jobs(scan_id,repo);
    ''')
    con.commit()


def parsed_time(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def job_duration_seconds(job):
    start = parsed_time(job.get('started_at'))
    end = parsed_time(job.get('completed_at'))
    if not start or not end or end < start:
        return None
    return int((end-start).total_seconds())


def runner_kind(job):
    # Do not infer hosted compute from runner_name alone. GitHub self-hosted
    # runners can have arbitrary display names; labels are more reliable.
    labels = job.get('labels') or []
    if any(str(label).lower() == 'self-hosted' for label in labels):
        return 'self-hosted'
    return 'unknown'  # GitHub-hosted is not proven by the absence of a label.


def _api_repo_pages(api, owner, max_repos, request_budget, start_after=None):
    """Yield permitted owned repos while keeping the API call budget strict."""
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', owner):
        raise ValueError('Invalid owner')
    page = 1
    seen = 0
    if start_after is not None and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',start_after):
        raise ValueError('Invalid start-after repository')
    while seen < max_repos:
        if api.calls >= request_budget:
            raise GithubError('Read request budget exhausted')
        path = (f'/user/repos?affiliation=owner&per_page=100&page={page}&sort=full_name&direction=asc'
                if api.token else f'/users/{owner}/repos?per_page=100&page={page}&type=owner&sort=full_name&direction=asc')
        items = api.get(path)
        if not isinstance(items, list):
            raise GithubError('Unexpected repository response')
        for item in items:
            if not isinstance(item,dict):continue
            if item.get('owner', {}).get('login', '').lower() != owner.lower():
                continue
            if start_after and item.get('full_name','').lower() <= start_after.lower():continue
            yield item
            seen += 1
            if seen >= max_repos:
                return
        if len(items) < 100:
            return
        page += 1


def _summary(con, scan_id):
    row = con.execute('SELECT * FROM usage_scans WHERE id=?', (scan_id,)).fetchone()
    jobs = con.execute('''
      SELECT r.repo, r.workflow_name, count(DISTINCT r.run_id) as runs,
        count(j.job_id) as jobs,
        SUM(CASE WHEN j.duration_seconds IS NOT NULL THEN j.duration_seconds ELSE 0 END) as seconds,
        SUM(CASE WHEN j.duration_seconds IS NOT NULL THEN 1 ELSE 0 END) as timed_jobs,
        SUM(CASE WHEN j.job_id IS NOT NULL AND j.duration_seconds IS NULL THEN 1 ELSE 0 END) as untimed_jobs
      FROM usage_runs r LEFT JOIN usage_jobs j ON j.run_id=r.run_id AND j.scan_id=?
      WHERE r.scan_id=?
      GROUP BY r.repo, r.workflow_name
      ORDER BY seconds DESC, runs DESC, r.repo, r.workflow_name
      LIMIT 500
    ''', (scan_id, scan_id)).fetchall()
    workflows = [dict(item) for item in jobs]
    for item in workflows:
        item['observed_job_wall_minutes'] = round((item['seconds'] or 0)/60, 2)
        item['unit'] = 'observed job wall-minutes, NOT GitHub-billed minutes'
        del item['seconds']
        candidates=con.execute('''SELECT count(*) FROM findings f JOIN workflows w
            ON f.repo=w.repo AND f.path=w.path
            WHERE w.repo=? AND w.name=?''', (item['repo'],item['workflow_name'])).fetchone()[0]
        item['workflow_review_findings'] = candidates
    return {
      'schema': 'aeroforge.usage.evidence.v1',
      'scan': dict(row),
      'workflows': workflows,
      'interpretation': 'Only observed jobs in sampled GitHub runs. Elapsed job time is NOT GitHub-billed minutes: billing rounds job duration, depends on platform/multipliers, runner type and other policy. Sample may omit runs, retries, or data due to limits or permissions.',
      'estimated_savings_minutes': None,
      'authorization': 'Read-only observation; no automated workflow modifications',
    }


def scan_usage(con, owner='aerovista-us', *, days=30, max_repos=100, max_runs_per_repo=12,
               max_requests=400, api=None, clock=None, source='github', start_after=None):
    """Bounded single-pass sample of run/job history; never triggers Actions.

    Returns an explicit coverage report and deterministic evidence receipt. A
    private token is optional but an unauthenticated run is marked partial.
    """
    if start_after is not None and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',start_after):
        raise ValueError('Invalid start-after repository')
    if not 1 <= days <= 90: raise ValueError('days must be 1..90')
    if not 1 <= max_repos <= 1000: raise ValueError('max-repos must be 1..1000')
    if not 1 <= max_runs_per_repo <= 100: raise ValueError('max-runs-per-repo must be 1..100')
    if not 1 <= max_requests <= 2500: raise ValueError('max-requests must be 1..2500')
    ensure_schema(con)
    api=api or GithubReader()
    start_time=clock or dt.datetime.now(dt.timezone.utc)
    since=(start_time-dt.timedelta(days=days)).date().isoformat()
    cur=con.execute('''INSERT INTO usage_scans(owner,source,started_at,window_since)
        VALUES(?,?,?,?)''',(owner,source,now(),since))
    scan_id=cur.lastrowid
    con.commit()
    stats={'repositories_seen':0,'repositories_with_history':0,'runs_seen':0,'jobs_seen':0,'errors':0}
    notes=[]
    partial=not bool(api.token) or source!='github'
    if source!='github':notes.append('Fixture or synthetic history, not live GitHub observations')
    if start_after:
        partial=True;notes.append('Scans only repositories after '+start_after)
    if not api.token:notes.append('Public-only: no authenticated private repository coverage')
    try:
        for repo in _api_repo_pages(api,owner,max_repos,max_requests,start_after):
            name=repo.get('full_name','')
            stats['repositories_seen']+=1
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',name):
                stats['errors']+=1;partial=True;notes.append('Invalid repository name skipped');continue
            if repo.get('archived') or repo.get('disabled'):
                continue
            if api.calls >= max_requests:
                partial=True;notes.append('Read request cap reached before listing all repositories');break
            # ISO date is percent-safe; avoid user-controlled query injection.
            path=(f'/repos/{name}/actions/runs?per_page=100&page=1&'
                  f'created=%3E%3D{since}')
            try:
                data=api.get(path)
                if not isinstance(data,dict) or not isinstance(data.get('workflow_runs'),list):
                    raise GithubError('Unexpected Actions runs response')
            except GithubError:
                stats['errors']+=1;partial=True
                notes.append('Actions run history unavailable for '+name)
                continue
            runs=data['workflow_runs']
            count=data.get('total_count',len(runs))
            if count>max_runs_per_repo:
                partial=True;notes.append('Run sample capped for '+name)
            if runs:stats['repositories_with_history']+=1
            for run in runs[:max_runs_per_repo]:
                if not isinstance(run,dict):
                    stats['errors']+=1;partial=True;continue
                rid=run.get('id')
                if not isinstance(rid,int) or rid<=0:
                    stats['errors']+=1;partial=True;continue
                con.execute('''INSERT INTO usage_runs
                  (run_id,repo,workflow_id,workflow_name,event,status,conclusion,created_at,html_url,scan_id)
                  VALUES (?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(scan_id,run_id) DO UPDATE SET repo=excluded.repo, workflow_id=excluded.workflow_id,
                  workflow_name=excluded.workflow_name,event=excluded.event,status=excluded.status,
                  conclusion=excluded.conclusion,created_at=excluded.created_at,
                  html_url=excluded.html_url,scan_id=excluded.scan_id''',
                  (rid,name,run.get('workflow_id'),str(run.get('name') or '(unnamed)')[:220],
                   str(run.get('event') or '')[:64],str(run.get('status') or '')[:48],
                   str(run.get('conclusion') or '')[:48],run.get('created_at'),
                   str(run.get('html_url') or '')[:500],scan_id))
                stats['runs_seen']+=1
                if api.calls >= max_requests:
                    partial=True;notes.append('Request cap reached before reading all job details');break
                try:
                    detail=api.get(f'/repos/{name}/actions/runs/{rid}/jobs?per_page=100&page=1')
                    if not isinstance(detail,dict) or not isinstance(detail.get('jobs'),list):
                        raise GithubError('Unexpected jobs response')
                except GithubError:
                    stats['errors']+=1;partial=True
                    notes.append('Job history unavailable for '+name+' run '+str(rid))
                    continue
                job_list=detail['jobs']
                if detail.get('total_count',len(job_list))>MAX_RESPONSE_ROWS:
                    partial=True;notes.append('Jobs per run capped at 100 for '+str(rid))
                for job in job_list[:MAX_RESPONSE_ROWS]:
                    if not isinstance(job,dict):
                        stats['errors']+=1;partial=True;continue
                    jid=job.get('id')
                    if not isinstance(jid,int) or jid<=0:
                        stats['errors']+=1;partial=True;continue
                    con.execute('''INSERT INTO usage_jobs(job_id,run_id,repo,status,conclusion,runner_kind,
                      duration_seconds,started_at,completed_at,scan_id) VALUES(?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(scan_id,job_id) DO UPDATE SET run_id=excluded.run_id,repo=excluded.repo,
                      status=excluded.status,conclusion=excluded.conclusion,
                      runner_kind=excluded.runner_kind,duration_seconds=excluded.duration_seconds,
                      started_at=excluded.started_at,completed_at=excluded.completed_at,scan_id=excluded.scan_id''',
                      (jid,rid,name,str(job.get('status') or '')[:40],str(job.get('conclusion') or '')[:40],
                       runner_kind(job),job_duration_seconds(job),job.get('started_at'),
                       job.get('completed_at'),scan_id))
                    stats['jobs_seen']+=1
            con.commit()
            if api.calls >= max_requests:
                partial=True;notes.append('Global GitHub read request budget exhausted');break
        if stats['repositories_seen'] >= max_repos:
            partial=True;notes.append('Repository cap reached; full account coverage not proven')
    except GithubError as exc:
        partial=True;stats['errors']+=1;notes.append(str(exc))
    finally:
        con.execute('''UPDATE usage_scans SET completed_at=?,repositories_seen=?,repositories_with_history=?,
          runs_seen=?,jobs_seen=?,request_count=?,errors=?,partial=?,notes=? WHERE id=?''',
          (now(),stats['repositories_seen'],stats['repositories_with_history'],stats['runs_seen'],
           stats['jobs_seen'],api.calls,stats['errors'],int(partial),json.dumps(sorted(set(notes))),scan_id))
        con.commit()
    report=_summary(con,scan_id)
    output=home()/'reports'/f'usage-{scan_id:06d}.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False).encode('utf-8')
    output.write_bytes(payload)
    receipt={'report_path':str(output),'report_sha256':hashlib.sha256(payload).hexdigest(),
             'scan_id':scan_id,'repositories_seen':stats['repositories_seen'],
             'runs_seen':stats['runs_seen'],'jobs_seen':stats['jobs_seen'],
             'read_requests':api.calls,'errors':stats['errors'],'partial':bool(partial),
             'source':source,'estimated_savings_minutes':None}
    return receipt


def latest_usage(con):
    ensure_schema(con)
    row=con.execute('SELECT id FROM usage_scans ORDER BY id DESC LIMIT 1').fetchone()
    return _summary(con,row['id']) if row else None
