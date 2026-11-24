import json, os, pathlib, sqlite3, tempfile, unittest
from unittest.mock import patch
from aeroforge.detector import analyze,triggers
from aeroforge.github import GithubReader,GithubError
from aeroforge.inventory import scan_fixture,scan_github
from aeroforge.storage import connect,stats
from aeroforge.worker import enqueue,run_next,validate_target
from aeroforge.dashboard import build

ROOT=pathlib.Path(__file__).resolve().parents[1]

class TestDetection(unittest.TestCase):
    def test_multiline_events(self):
        self.assertEqual(triggers('name: test\non:\n  push:\n    branches: [main]\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n'),['pull_request','push'])
    def test_inline_events(self):
        self.assertEqual(triggers('on: [push, pull_request]\n'),['pull_request','push'])
    def test_no_trigger_not_fabricated(self):
        self.assertEqual(triggers('name: demo\njobs:\n  task:\n    runs-on: ubuntu-latest'),[])
    def test_overlap_and_timeout(self):
        y='on:\n  push:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n'
        rules={x['rule'] for x in analyze('av/a','.github/workflows/test.yml',y)['findings']}
        self.assertTrue({'PUSH_PR_OVERLAP','NO_CONCURRENCY','TIMEOUT_test'}.issubset(rules))
    def test_release_manual_not_false_duplicate(self):
        y='on:\n  workflow_dispatch:\njobs:\n  release:\n    runs-on: ubuntu-latest\n    timeout-minutes: 45\n'
        self.assertEqual(analyze('av/r','.github/workflows/release.yml',y)['findings'],[])
    def test_evidence_ids_are_deterministic(self):
        y='on: [push, pull_request]\n'
        self.assertEqual(analyze('r/x','a.yml',y)['findings'][0]['id'],analyze('r/x','a.yml',y)['findings'][0]['id'])

class TestStoreWorker(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=pathlib.Path(self.tmp.name)
        self.repo=root/'source';self.repo.mkdir()
        self.state=root/'state'
        self.env=patch.dict(os.environ,{'AEROFORGE_STATE':str(self.state),'AEROFORGE_ALLOWED_ROOTS':str(self.repo)})
        self.env.start();self.addCleanup(self.env.stop)
        self.con=connect();self.addCleanup(self.con.close)
    def test_fixture_inventory_and_dedup(self):
        fixture=json.loads((ROOT/'examples/fixture.json').read_text())
        r=scan_fixture(self.con,fixture)
        self.assertEqual(r['repositories'],2)
        self.assertEqual(stats(self.con)['workflows'],2)
        self.assertGreaterEqual(stats(self.con)['findings'],3)
        first=stats(self.con)['findings']
        scan_fixture(self.con,fixture)
        self.assertEqual(stats(self.con)['findings'],first)
        self.assertEqual(stats(self.con)['workflows'],2)
    def test_successful_ast_validation_and_receipt(self):
        (self.repo/'ok.py').write_text('x=2\n')
        tid=enqueue(self.con,'python_syntax',str(self.repo))
        receipt=run_next(self.con)
        self.assertEqual(receipt['status'],'passed')
        self.assertEqual(receipt['job_id'],tid)
        self.assertTrue((self.state/'runs'/f'{tid:06d}'/'evidence.json').exists())
        self.assertEqual(self.con.execute('SELECT status FROM jobs WHERE id=?',(tid,)).fetchone()[0],'passed')
        self.assertEqual(run_next(self.con),None)
    def test_bad_python_fails(self):
        (self.repo/'bad.py').write_text('def f(:\n')
        enqueue(self.con,'python_syntax',str(self.repo))
        self.assertEqual(run_next(self.con)['status'],'failed')
    def test_invalid_json_fails(self):
        (self.repo/'bad.json').write_text('invalid {')
        enqueue(self.con,'json_validate',str(self.repo))
        self.assertEqual(run_next(self.con)['status'],'failed')
    def test_outside_root_is_denied(self):
        other=pathlib.Path(self.tmp.name)/'outside';other.mkdir()
        with self.assertRaises(PermissionError):validate_target(str(other))
        with self.assertRaises(PermissionError):enqueue(self.con,'repo_inventory',str(other))
    def test_symlinks_and_env_skipped(self):
        outside=pathlib.Path(self.tmp.name)/'secret.txt';outside.write_text('sensitive')
        (self.repo/'link.json').symlink_to(outside)
        (self.repo/'.env').write_text('SECRET=xyz')
        enqueue(self.con,'repo_inventory',str(self.repo))
        run_next(self.con)
        report=json.loads((self.state/'runs'/'000001'/'evidence.json').read_text())
        paths=[x['file'] for x in report['files']]
        self.assertNotIn('link.json',paths)
        self.assertNotIn('.env',paths)
    def test_dashboard_escapes_untrusted_data(self):
        scan_fixture(self.con,{'schema':'aeroforge.fixture.v1','repositories':[
            {'full_name':'a/<img src=x onerror=alert(1)>','visibility':'public','workflows':[{'path':'.github/workflows/a.yml','content':'on: [push, pull_request]\n'}]}]})
        h=build(self.con).read_text()
        self.assertIn('&lt;img',h)
        self.assertNotIn('<img src=x onerror',h)
    def test_no_allowed_roots_denied(self):
        with patch.dict(os.environ,{'AEROFORGE_ALLOWED_ROOTS':''}):
            with self.assertRaises(PermissionError):validate_target(str(self.repo))
    def test_reject_arbitrary_command_profile(self):
        with self.assertRaises(ValueError):enqueue(self.con,'bash',str(self.repo))

class TestGithubAPI(unittest.TestCase):
    def test_url_allowlist(self):
        x=GithubReader(token='fake')
        with self.assertRaises(ValueError):x.get('https://evil.example/hello')
        with self.assertRaises(ValueError):x.workflow_text('a/b','../../etc/passwd','main')
    def test_scan_mocked_read_only(self):
        r={'full_name':'aerovista-us/alpha','visibility':'public','default_branch':'main','owner':{'login':'aerovista-us'}}
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ,{'AEROFORGE_STATE':d,'GH_TOKEN':'test'}):
                c=connect()
                with patch.object(GithubReader,'repositories',return_value=iter([r])),\
                     patch.object(GithubReader,'workflows',return_value=[{'path':'.github/workflows/t.yml','name':'Test','state':'active'}]),\
                     patch.object(GithubReader,'workflow_text',return_value='on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n'):
                    output=scan_github(c,'aerovista-us',max_repos=50)
                self.assertEqual(output['repositories'],1)
                self.assertEqual(output['workflows'],1)
                self.assertFalse(output['partial'])
                self.assertEqual(stats(c)['findings'],1)
                c.close()

if __name__=='__main__':unittest.main()
