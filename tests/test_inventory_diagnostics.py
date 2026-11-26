"""Inventory diagnostic classification regression checks (no live GitHub calls)."""
import os
import tempfile
import unittest
from unittest.mock import patch

from aeroforge.github import GithubReader,GithubError
from aeroforge.inventory import scan_github
from aeroforge.dashboard import build
from aeroforge.storage import connect

class InventoryDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx=patch.dict(os.environ,{'AEROFORGE_STATE':self.tmp.name,'GH_TOKEN':'fixture-token'})
        self.ctx.start()
        self.addCleanup(self.ctx.stop)
        self.con=connect()
        self.addCleanup(self.con.close)

    def test_error_status_carries_http_code(self):
        ex=GithubError('GitHub read failed: HTTP 404',status_code=404)
        self.assertEqual(ex.status_code,404)
        self.assertNotIn('fixture-token',str(ex))

    def test_source_404_is_distinct_from_auth_failures(self):
        repos=[
            {'full_name':'aerovista-us/removed','owner':{'login':'aerovista-us'},
              'default_branch':'main','visibility':'private'},
            {'full_name':'aerovista-us/restricted','owner':{'login':'aerovista-us'},
              'default_branch':'main','visibility':'private'}]
        def workflow_list(name):
            if name.endswith('restricted'):
                raise GithubError('GitHub read failed: HTTP 403',status_code=403)
            return [{'path':'.github/workflows/old.yml','name':'Old workflow','state':'active'}]
        with patch.object(GithubReader,'repositories',return_value=iter(repos)), \
             patch.object(GithubReader,'workflows',side_effect=workflow_list), \
             patch.object(GithubReader,'workflow_text',side_effect=GithubError('GitHub read failed: HTTP 404',status_code=404)):
            result=scan_github(self.con,'aerovista-us',max_repos=50)
        self.assertTrue(result['partial'])
        self.assertEqual(result['errors'],2)
        self.assertEqual(result['error_categories'],{
            'workflow_metadata_http_403':1,
            'workflow_yaml_http_404_unavailable_on_default_branch':1})
        records=[tuple(r) for r in self.con.execute(
            'SELECT repo,path,category,http_status FROM inventory_issues ORDER BY repo')]
        self.assertEqual(records,[
            ('aerovista-us/removed','.github/workflows/old.yml',
             'workflow_yaml_http_404_unavailable_on_default_branch',404),
            ('aerovista-us/restricted','(workflow listing)','workflow_metadata_http_403',403)])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM workflows').fetchone()[0],0)

    def test_forgedeck_displays_http_categories_without_exposing_source(self):
        repos=[{'full_name':'aerovista-us/retired','owner':{'login':'aerovista-us'},
                'default_branch':'main','visibility':'private'}]
        with patch.object(GithubReader,'repositories',return_value=iter(repos)), \
             patch.object(GithubReader,'workflows',return_value=[
                 {'path':'.github/workflows/retired.yml','name':'Retired','state':'active'}]), \
             patch.object(GithubReader,'workflow_text',
                 side_effect=GithubError('GitHub read failed: HTTP 404',status_code=404)):
            scan_github(self.con,'aerovista-us',max_repos=100)
        page=build(self.con).read_text()
        self.assertIn('Workflow inventory diagnostics',page)
        self.assertIn('workflow_yaml_http_404_unavailable_on_default_branch',page)
        self.assertNotIn('fixture-token',page)

    def test_valid_workflow_preserves_clean_result(self):
        repos=[{'full_name':'aerovista-us/clean','owner':{'login':'aerovista-us'},
                 'default_branch':'main','visibility':'public'}]
        with patch.object(GithubReader,'repositories',return_value=iter(repos)), \
             patch.object(GithubReader,'workflows',return_value=[
                 {'path':'.github/workflows/ci.yml','name':'CI','state':'active'}]), \
             patch.object(GithubReader,'workflow_text',return_value='on: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n'):
            result=scan_github(self.con,'aerovista-us',max_repos=50)
        self.assertEqual(result['errors'],0)
        self.assertEqual(result['error_categories'],{})
        self.assertFalse(result['partial'])

if __name__=='__main__':
    unittest.main()
