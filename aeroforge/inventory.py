"""Read-only GitHub or JSON fixture scan; deduplicated finding ledger."""
from __future__ import annotations
import hashlib
import json
from .storage import now,upsert_repository,update_workflow
from .detector import analyze
from .github import GithubReader,GithubError


def scan_fixture(con,fixture):
    """A documented fixture is useful when GitHub Actions runner minutes are exhausted."""
    if not isinstance(fixture,dict) or fixture.get('schema')!='aeroforge.fixture.v1':
        raise ValueError('Unexpected inventory fixture schema')
    repos=fixture.get('repositories',[])
    if not isinstance(repos,list) or len(repos)>500:raise ValueError('Invalid fixture size')
    total=0;errors=0
    for repo in repos:
        if not isinstance(repo,dict) or not isinstance(repo.get('full_name'),str):raise ValueError('Invalid repository')
        upsert_repository(con,repo,source='fixture')
        for wf in repo.get('workflows',[]):
            path=wf['path'];content=wf['content']
            if len(content)>250_000 or not path.startswith('.github/workflows/'):
                errors+=1;continue
            a=analyze(repo['full_name'],path,content)
            update_workflow(con,repo['full_name'],path,wf.get('name',path),wf.get('enabled',True),
                            hashlib.sha256(content.encode()).hexdigest(),a['triggers'],a['findings'])
            total+=1
    con.commit()
    return {'source':'fixture','repositories':len(repos),'workflows':total,'errors':errors,'partial':True}


def scan_github(con,owner,max_repos=250,max_workflows_per_repo=60):
    """Read-only authenticated inventory. Partial findings are marked explicitly."""
    api=GithubReader()
    con.execute('''CREATE TABLE IF NOT EXISTS inventory_issues(
      scan_id INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,
      category TEXT NOT NULL,http_status INTEGER,recorded_at TEXT NOT NULL,
      PRIMARY KEY(scan_id,repo,path,category))''')
    scan=con.execute('INSERT INTO scans(source,owner,started_at) VALUES(?,?,?)',('github',owner,now()))
    con.commit()
    count=0; wf_count=0;errors=0;partial=False
    try:
        for repo in api.repositories(owner,max_repos=max_repos):
            upsert_repository(con,repo)
            count+=1
            if repo.get('archived'):
                con.commit();continue
            try:workflows=api.workflows(repo['full_name'])
            except GithubError as exc:
                category='workflow_metadata_http_'+str(exc.status_code or 'unknown')
                con.execute("INSERT INTO inventory_issues(scan_id,repo,path,category,http_status,recorded_at) VALUES(?,?,?,?,?,?)",
                    (scan.lastrowid,repo['full_name'],'(workflow listing)',category,exc.status_code,now()))
                errors+=1;partial=True;con.commit();continue
            if len(workflows)>max_workflows_per_repo:
                partial=True
            for wf in workflows[:max_workflows_per_repo]:
                path=wf.get('path','')
                if not path.startswith('.github/workflows/') or not path.endswith(('.yml','.yaml')):continue
                try:
                    body=api.workflow_text(repo['full_name'],path,repo.get('default_branch','main'))
                    a=analyze(repo['full_name'],path,body)
                    update_workflow(con,repo['full_name'],path,wf.get('name',path),wf.get('state','active')=='active',
                                    hashlib.sha256(body.encode()).hexdigest(),a['triggers'],a['findings'])
                    wf_count+=1
                except (GithubError,UnicodeError,ValueError) as exc:
                    code=exc.status_code if isinstance(exc,GithubError) else None
                    if code==404:
                        category='workflow_yaml_http_404_unavailable_on_default_branch'
                    elif code:
                        category='workflow_yaml_http_'+str(code)
                    else:
                        category='workflow_yaml_parse_or_decode_failure'
                    con.execute("INSERT INTO inventory_issues(scan_id,repo,path,category,http_status,recorded_at) VALUES(?,?,?,?,?,?)",
                        (scan.lastrowid,repo['full_name'],path,category,code,now()))
                    errors+=1;partial=True
            con.commit()
        # max_repos may mean the entire result set was not exhausted; conservatively partial.
        if count>=max_repos:partial=True
    except GithubError:
        partial=True;errors+=1
        raise
    finally:
        con.execute('UPDATE scans SET completed_at=?,repos_scanned=?,workflows_scanned=?,errors=?,partial=? WHERE id=?',
                    (now(),count,wf_count,errors,int(partial or not api.token),scan.lastrowid))
        con.commit()
    categories={r[0]:r[1] for r in con.execute(
        "SELECT category,count(*) FROM inventory_issues WHERE scan_id=? GROUP BY category",(scan.lastrowid,))}
    return {'source':'github','repositories':count,'workflows':wf_count,'errors':errors,
            'error_categories':categories,
            'partial':partial or not bool(api.token),'requests':api.calls,
            'private_coverage':'requires owner-authorized token' if not api.token else 'subject to token scope'}
