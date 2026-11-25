import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aeroforge.storage import connect
from aeroforge.usage import scan_usage,latest_usage,job_duration_seconds,runner_kind
from aeroforge.dashboard import build
from aeroforge.github import GithubError

class FakeReader:
    def __init__(self,token='fixture'):
        self.token=token
        self.calls=0
        self.repos=[{'full_name':'aerovista-us/ACOS','owner':{'login':'aerovista-us'},'default_branch':'main','private':True},
                    {'full_name':'aerovista-us/lenora-voss','owner':{'login':'aerovista-us'},'default_branch':'main','private':True}]
        self.results={
          'ACOS': [{'id':7001,'workflow_id':11,'name':'Main Tests','status':'completed','conclusion':'success','event':'push','created_at':'2026-11-22T10:00:00Z'},
                   {'id':7002,'workflow_id':11,'name':'Main Tests','status':'completed','conclusion':'failure','event':'pull_request','created_at':'2026-11-23T10:00:00Z'}],
          'lenora-voss':[{'id':7003,'workflow_id':12,'name':'<img onerror=alert(1)>','status':'completed','conclusion':'success','event':'push','created_at':'2026-11-23T10:00:00Z'}]
        }
        self.jobs={
          7001:[{'id':9001,'status':'completed','conclusion':'success','started_at':'2026-11-22T10:00:00Z','completed_at':'2026-11-22T10:03:00Z','labels':['ubuntu-latest']}],
          7002:[{'id':9002,'status':'completed','conclusion':'failure','started_at':'2026-11-23T10:00:00Z','completed_at':'2026-11-23T10:01:00Z','labels':['self-hosted','linux']}],
          7003:[{'id':9003,'status':'completed','conclusion':'success','started_at':'2026-11-23T10:00:00Z','completed_at':'2026-11-23T10:02:00Z','labels':['ubuntu-latest']}]
        }
        self.fail_repo=None
    def get(self,path):
        self.calls+=1
        if path.startswith('/user/repos') or path.startswith('/users/'):
            return self.repos
        if '/actions/runs?' in path:
            repo=path.split('/')[3]
            if repo==self.fail_repo:raise GithubError('HTTP 403')
            return {'total_count':len(self.results[repo]), 'workflow_runs':self.results[repo]}
        if '/jobs?' in path:
            run=int(path.split('/actions/runs/')[1].split('/')[0])
            return {'total_count':len(self.jobs[run]), 'jobs':self.jobs[run]}
        raise AssertionError('Unexpected URL '+path)

class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.patch=patch.dict(os.environ,{'AEROFORGE_STATE':self.tmp.name})
        self.patch.start();self.addCleanup(self.patch.stop)
        self.db=connect();self.addCleanup(self.db.close)
        self.clock=dt.datetime(2026,11,24,tzinfo=dt.timezone.utc)
    def scan(self,reader=None,**kwargs):
        return scan_usage(self.db,'aerovista-us',api=reader or FakeReader(),clock=self.clock,**kwargs)
    def test_authenticated_scan_and_walltime(self):
        receipt=self.scan(max_repos=10,max_runs_per_repo=10,max_requests=30)
        self.assertEqual(receipt['repositories_seen'],2)
        self.assertEqual(receipt['runs_seen'],3)
        self.assertEqual(receipt['jobs_seen'],3)
        self.assertFalse(receipt['partial'])
        self.assertIsNone(receipt['estimated_savings_minutes'])
        report=latest_usage(self.db)
        self.assertEqual(report['workflows'][0]['workflow_name'],'Main Tests')
        self.assertEqual(report['workflows'][0]['observed_job_wall_minutes'],4.0)
        self.assertEqual(report['workflows'][0]['runs'],2)
        self.assertTrue(pathlib.Path(receipt['report_path']).exists())
        self.assertEqual(len(receipt['report_sha256']),64)
        self.assertIn('NOT GitHub-billed minutes',report['interpretation'])
    def test_repeat_scan_preserves_prior_scan_history(self):
        r1=self.scan(max_repos=10,max_requests=30)
        r2=self.scan(max_repos=10,max_requests=30)
        self.assertEqual(self.db.execute('SELECT count(*) FROM usage_runs').fetchone()[0],6)
        self.assertEqual(self.db.execute('SELECT count(*) FROM usage_jobs').fetchone()[0],6)
        self.assertNotEqual(r1['report_sha256'],r2['report_sha256'])
    def test_budget_marks_partial_not_success(self):
        r=self.scan(max_repos=10,max_requests=2)
        self.assertTrue(r['partial'])
        self.assertEqual(r['read_requests'],2)
        self.assertEqual(r['jobs_seen'],0)
    def test_repo_read_failure_is_partial(self):
        reader=FakeReader();reader.fail_repo='ACOS'
        r=self.scan(reader,max_repos=10,max_requests=30)
        self.assertTrue(r['partial'])
        self.assertEqual(r['errors'],1)
        self.assertEqual(r['runs_seen'],1)
    def test_unauthenticated_scan_never_claims_private_coverage(self):
        r=self.scan(FakeReader(token=None),max_repos=10,max_requests=30)
        self.assertTrue(r['partial'])
        self.assertIn('Public-only',latest_usage(self.db)['scan']['notes'])
    def test_wallclock_no_fake_seconds(self):
        self.assertIsNone(job_duration_seconds({'started_at':None,'completed_at':None}))
        self.assertIsNone(job_duration_seconds({'started_at':'2026-01-02T00:00:00Z','completed_at':'2026-01-01T00:00:00Z'}))
        self.assertEqual(runner_kind({'labels':['self-hosted']}),'self-hosted')
        self.assertEqual(runner_kind({'runner_name':'GitHub Actions','labels':[]}), 'unknown')
    def test_dashboard_escapes_history_data(self):
        self.scan(max_repos=10,max_requests=30)
        page=build(self.db).read_text()
        self.assertIn('&lt;img onerror=alert(1)&gt;',page)
        self.assertNotIn('<img onerror=alert(1)>',page)
        self.assertIn('Observed job wall-min',page)
        self.assertIn('not billed GitHub Actions minutes',page)
    def test_no_real_execution_behind_fixture(self):
        self.scan(max_repos=10,max_requests=30,source='fixture')
        self.assertTrue(latest_usage(self.db)['scan']['partial'])
    def test_strict_bounds(self):
        with self.assertRaises(ValueError): self.scan(days=0)
        with self.assertRaises(ValueError): self.scan(max_repos=1001)
        with self.assertRaises(ValueError): self.scan(max_requests=0)

if __name__=='__main__': unittest.main()

class FixtureAndResumeTests(unittest.TestCase):
    def test_offline_fixture_cli_and_source_label(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            env=os.environ.copy()
            env['AEROFORGE_STATE']=td
            result=subprocess.run([sys.executable,'-m','aeroforge','import-usage-fixture',
                                   str(ROOT/'examples/usage_fixture.json')],
                                  cwd=ROOT,env=env,capture_output=True,text=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)
            receipt=json.loads(result.stdout)
            self.assertEqual(receipt['source'],'fixture')
            self.assertTrue(receipt['partial'])
            self.assertEqual(receipt['runs_seen'],3)
            self.assertTrue((pathlib.Path(td)/'dashboard.html').is_file())
    def test_start_after_only_scans_later_repositories(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,{'AEROFORGE_STATE':td}):
                db=connect()
                self.addCleanup(db.close)
                r=scan_usage(db,'aerovista-us',api=FakeReader(),source='github',
                             start_after='aerovista-us/ACOS',max_repos=20,max_requests=20)
                self.assertTrue(r['partial'])
                self.assertEqual(r['repositories_seen'],1)
                self.assertEqual(r['runs_seen'],1)
                self.assertEqual(latest_usage(db)['workflows'][0]['repo'],'aerovista-us/lenora-voss')
