"""Offline historical usage fixture adapter. Entirely synthetic, never live GitHub data."""
from __future__ import annotations
import json
import pathlib
import re


class FixtureUsageReader:
    token='fixture'
    def __init__(self, payload):
        if not isinstance(payload,dict) or payload.get('schema')!='aeroforge.usage-fixture.v1':
            raise ValueError('Unexpected usage fixture schema')
        repos=payload.get('repositories')
        if not isinstance(repos,list) or not 1<=len(repos)<=50:raise ValueError('Invalid fixture repository count')
        self.calls=0
        self.repositories_data=[]
        self.runs={};self.jobs={}
        for repo in repos:
            if not isinstance(repo,dict):raise ValueError('Invalid fixture repository')
            name=repo.get('full_name')
            if not isinstance(name,str) or not re.fullmatch(r'[\w.-]+/[\w.-]+',name):raise ValueError('Invalid fixture name')
            if not isinstance(repo.get('runs'),list) or len(repo['runs'])>100:raise ValueError('Invalid fixture runs')
            owner=name.split('/')[0]
            self.repositories_data.append({'full_name':name,'owner':{'login':owner},'private':True,'archived':False})
            self.runs[name]=[]
            for run in repo['runs']:
                if not isinstance(run,dict) or not isinstance(run.get('id'),int) or not isinstance(run.get('jobs'),list) or len(run['jobs'])>100:raise ValueError('Invalid fixture run')
                self.runs[name].append({k:v for k,v in run.items() if k!='jobs'})
                self.jobs[run['id']]=run['jobs']
    def get(self,path):
        self.calls+=1
        if path.startswith(('/user/repos?','/users/')):
            return self.repositories_data
        m=re.fullmatch(r'/repos/([\w.-]+/[\w.-]+)/actions/runs\?.+',path)
        if m:
            return {'total_count':len(self.runs.get(m.group(1),[])),'workflow_runs':self.runs.get(m.group(1),[])}
        m=re.fullmatch(r'/repos/([\w.-]+/[\w.-]+)/actions/runs/(\d+)/jobs\?.+',path)
        if m:
            jobs=self.jobs.get(int(m.group(2)),[])
            return {'total_count':len(jobs),'jobs':jobs}
        raise ValueError('Unsupported fixture endpoint')


def read_usage_fixture(path):
    p=pathlib.Path(path).expanduser().resolve(strict=True)
    if p.stat().st_size>1_000_000:raise ValueError('Fixture too large')
    return FixtureUsageReader(json.loads(p.read_text('utf-8')))
