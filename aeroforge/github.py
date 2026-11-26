"""Read-only GitHub REST inventory. Never invokes Actions, starts runs, or posts checks."""
from __future__ import annotations
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

class GithubError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code=status_code

class GithubReader:
    def __init__(self,token=None,timeout=12):
        self.token=token if token is not None else os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        self.timeout=timeout
        self.calls=0

    def get(self,path):
        if not path.startswith('/') or not re.fullmatch(r'/[A-Za-z0-9_./%?=&+,:-]+',path):
            raise ValueError('Invalid read-only GitHub API path')
        url='https://api.github.com'+path
        headers={'Accept':'application/vnd.github+json','User-Agent':'AeroForge-read-only/0.1',
                 'X-GitHub-Api-Version':'2022-11-28'}
        if self.token:headers['Authorization']='Bearer '+self.token
        self.calls+=1
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers,method='GET'),timeout=self.timeout) as r:
                return json.loads(r.read(4_000_000))
        except urllib.error.HTTPError as exc:
            # Never leak token, body, or credentials in public status reports.
            raise GithubError('GitHub read failed: HTTP '+str(exc.code),status_code=exc.code) from None
        except (TimeoutError,urllib.error.URLError) as exc:
            raise GithubError('GitHub read unavailable: '+type(exc).__name__) from None

    def repositories(self,owner,max_repos=250):
        if not re.fullmatch('[A-Za-z0-9-]{1,39}',owner):raise ValueError('Invalid owner')
        count=0
        for page in range(1,(max_repos+99)//100+1):
            if self.token:
                # Authenticated account owner gets public and private; filter by actual owner.
                path=f'/user/repos?affiliation=owner&per_page=100&page={page}&sort=full_name&direction=asc'
            else:
                path=f'/users/{owner}/repos?per_page=100&page={page}&type=owner&sort=full_name&direction=asc'
            results=self.get(path)
            if not isinstance(results,list):raise GithubError('Unexpected GitHub repository list')
            for item in results:
                if item.get('owner',{}).get('login','').lower()!=owner.lower():continue
                yield item;count+=1
                if count>=max_repos:return
            if len(results)<100:return

    def workflows(self,repo):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo):raise ValueError('Invalid repository')
        result=self.get('/repos/'+repo+'/actions/workflows?per_page=100&page=1')
        return result.get('workflows',[]) if isinstance(result,dict) else []

    def workflow_text(self,repo,path,branch):
        if not path.startswith('.github/workflows/') or not path.endswith(('.yaml','.yml')):
            raise ValueError('Non-workflow file requested')
        if '..' in path.split('/') or not re.fullmatch(r'[\w./-]+',path):raise ValueError('Unsafe workflow path')
        endpoint='/repos/'+repo+'/contents/'+urllib.parse.quote(path,safe='/')+'?ref='+urllib.parse.quote(branch,safe='')
        result=self.get(endpoint)
        if result.get('type')!='file' or result.get('size',0)>250_000:
            raise GithubError('Workflow not a supported small file')
        data=base64.b64decode(result.get('content','').replace('\n',''),validate=True)
        if len(data)>250_000:raise GithubError('Workflow exceeds limit')
        return data.decode('utf-8')
