"""Conservative static workflow heuristics; proposals only, never YAML auto-edits."""
from __future__ import annotations
import hashlib
import re


def triggers(source:str)->list[str]:
    lines=source.splitlines()
    for i,line in enumerate(lines):
        m=re.match(r'^(?:on|["\']on["\'])\s*:\s*(.*)$',line)
        if not m:continue
        tail=m.group(1).split('#',1)[0].strip()
        if tail.startswith('['):
            return sorted(set(re.findall(r'[A-Za-z_][\w-]*',tail)))
        if tail and not tail.startswith('{'):
            return [tail.strip(' "\'')]
        items=[]
        for s in lines[i+1:]:
            if s.strip() and not s[0].isspace():break
            x=re.match(r'^  ([a-zA-Z_][\w-]*)\s*:',s)
            if x:items.append(x.group(1))
        return sorted(set(items))
    return []


def _job_sections(source:str):
    lines=source.splitlines();in_jobs=False;current=None;buf=[];result=[]
    for line in lines:
        if line.rstrip() == 'jobs:':in_jobs=True;continue
        if not in_jobs:continue
        if line.strip() and not line[0].isspace():
            if current:result.append((current,'\n'.join(buf)))
            break
        m=re.match(r'^  ([\w-]+):\s*$',line)
        if m:
            if current:result.append((current,'\n'.join(buf)))
            current=m.group(1);buf=[]
        elif current:buf.append(line)
    else:
        if current:result.append((current,'\n'.join(buf)))
    return result


def analyze(repo:str,path:str,source:str)->dict:
    """Return evidence-grounded risk candidates. Does not estimate savings."""
    ev=triggers(source)
    findings=[]
    def add(rule,severity,confidence,summary,recommendation):
        key=hashlib.sha256(f'{repo}\0{path}\0{rule}'.encode()).hexdigest()[:20]
        findings.append(dict(id=key,rule=rule,severity=severity,confidence=confidence,
                             summary=summary,recommendation=recommendation))
    both='push' in ev and 'pull_request' in ev
    if both:
        add('PUSH_PR_OVERLAP','review','medium',
            'Both push and pull_request trigger this workflow; duplicate runs may occur on feature branches.',
            'Compare push branch filters to PR coverage. Prefer PR tests on feature branches and push tests on protected main; preserve release gates.')
    if both and not re.search(r'^concurrency\s*:',source,re.M):
        add('NO_CONCURRENCY','opportunity','high',
            'No top-level concurrency rule for a workflow triggered by pushes and PRs.',
            'Review a concurrency group keyed by workflow and ref with cancel-in-progress for non-release runs; do not cancel deployments.')
    if 'schedule' in ev:
        add('SCHEDULE_REVIEW','review','high',
            'Scheduled execution is configured.',
            'Check cadence and whether the scheduled task is still necessary. Keep security/backup schedules where justified.')
    jobs=_job_sections(source)
    for name,body in jobs:
        if re.search(r'^\s+runs-on\s*:',body,re.M) and not re.search(r'^\s+timeout-minutes\s*:',body,re.M):
            add('TIMEOUT_'+name,'opportunity','high',f'Job {name} has no explicit timeout-minutes.',
                'Set a tested, job-specific timeout with enough headroom for legitimate builds and release checks.')
    if both and not re.search(r'^\s{4}(paths|paths-ignore)\s*:',source,re.M):
        add('PATH_FILTER_REVIEW','review','low',
            'No path filter detected under push/pull_request trigger declarations.',
            'Review whether unrelated documentation or asset changes should skip tests; retain checks required for protected branches.')
    return {'triggers':ev,'findings':findings,'jobs':len(jobs)}
