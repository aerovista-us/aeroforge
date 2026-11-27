import importlib.util
import pathlib
import unittest
from unittest.mock import patch
P=pathlib.Path(__file__).parents[1]/'scripts/forge_runner_acceptance.py'
spec=importlib.util.spec_from_file_location('acceptance',P)
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class ProbePermissionChecks(unittest.TestCase):
 def test_permission_denied_host_path_is_inaccessible_not_a_crash(self):
  import contextlib,io,json,socket
  buffer=io.StringIO()
  original=pathlib.Path.exists
  def mocked_exists(path):
   if str(path)=="/root/.ssh":
    raise PermissionError("protected")
   return False
  with patch.object(pathlib.Path,'exists',mocked_exists), \
       patch('builtins.open',side_effect=PermissionError('read-only')), \
       patch.object(socket,'create_connection',side_effect=OSError('network off')), \
       contextlib.redirect_stdout(buffer):
   exec(a.PROBE,{})
  result=json.loads(buffer.getvalue())
  self.assertEqual(result['unexpected_host_paths'],[])
  self.assertEqual(result['inaccessible_host_paths'],['/root/.ssh'])
  self.assertTrue(result['external_network_denied'])
  self.assertTrue(result['root_filesystem_readonly'])

class AcceptanceTests(unittest.TestCase):
 def test_nxcore_rejected(self):
  with self.assertRaises(a.AcceptanceDenied):a.check_host('nxcore',{'MemAvailable':8192,'SwapTotal':0,'SwapFree':0})
 def test_high_swap_rejected(self):
  with self.assertRaises(a.AcceptanceDenied):a.check_host('aeroforge-runner',{'MemAvailable':8192,'SwapTotal':100,'SwapFree':10})
 def test_low_ram_rejected(self):
  with self.assertRaises(a.AcceptanceDenied):a.check_host('aeroforge-runner',{'MemAvailable':500,'SwapTotal':0,'SwapFree':0})
 def test_dedicated_allowed_with_ram(self):
  self.assertEqual(a.check_host('aeroforge-runner',{'MemAvailable':4096,'SwapTotal':100,'SwapFree':100})['hostname'],'aeroforge-runner')
 def test_rootless_required(self):
  self.assertFalse(a.rootless_docker({'SecurityOptions':['name=seccomp']}))
  self.assertTrue(a.rootless_docker({'SecurityOptions':['name=rootless','name=seccomp']}))
 def test_pinned_image_required(self):
  with self.assertRaises(a.AcceptanceDenied):a.build_command('python:latest','test')
 def test_docker_args_hardened(self):
  args=a.build_command('sha256:'+'a'*64,'af-test')
  for item in ['--network=none','--read-only','--cap-drop=ALL','--pids-limit=32','--memory=128m','--memory-swap=128m','--cpus=0.25','--pull=never']:
   self.assertIn(item,args)
  self.assertFalse(any('--mount=' in item or '--volume=' in item for item in args[:args.index('python')]))
 def test_configuration_rejects_network(self):
  x={'HostConfig':{'NetworkMode':'bridge'}}
  with self.assertRaises(a.AcceptanceDenied):a.verify_container(x)
 def test_good_runtime_proof(self):
  p={'uid':65534,'external_network_denied':True,'root_filesystem_readonly':True,'unexpected_host_paths':[],
     'cgroup':{'memory.max':str(128*1024*1024),'cpu.max':'25000 100000','pids.max':'32'}}
  self.assertTrue(all(a.verify_probe(p).values()))
 def test_runtime_proof_rejects_network(self):
  p={'uid':65534,'external_network_denied':False,'root_filesystem_readonly':True,'unexpected_host_paths':[],
     'cgroup':{'memory.max':str(128*1024*1024),'cpu.max':'25000 100000','pids.max':'32'}}
  with self.assertRaises(a.AcceptanceDenied):a.verify_probe(p)
 def test_runtime_proof_rejects_excess_cpu(self):
  p={'uid':65534,'external_network_denied':True,'root_filesystem_readonly':True,'unexpected_host_paths':[],
     'cgroup':{'memory.max':str(128*1024*1024),'cpu.max':'100000 100000','pids.max':'32'}}
  with self.assertRaises(a.AcceptanceDenied):a.verify_probe(p)
 def test_no_runtime_without_approval(self):
  with patch.object(a,'check_host',return_value={'hostname':'dedicated'}),patch.object(a,'docker_preflight',return_value={'rootless_docker':True}),patch.object(a,'run_runtime') as run,patch.object(pathlib.Path,'write_text'),patch.object(pathlib.Path,'chmod'):
   self.assertEqual(a.main(['--image-id','sha256:'+'a'*64]),0)
   run.assert_not_called()

if __name__=='__main__':unittest.main()

class AcceptanceExecutionTests(unittest.TestCase):
 def config(self):
  return {'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,
          'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],
          'Memory':128*1024*1024,'MemorySwap':128*1024*1024,
          'NanoCpus':250000000,'PidsLimit':32,'Binds':None,'Mounts':[],
          'PidMode':'','UTSMode':''},
          'Config':{'User':'65534:65534','Env':['HOME=/tmp','PATH=/usr/local/bin']}}
 def proof(self):
  return {'uid':65534,'external_network_denied':True,
          'root_filesystem_readonly':True,'unexpected_host_paths':[],
          'cgroup':{'memory.max':str(128*1024*1024),'cpu.max':'25000 100000','pids.max':'32'}}
 def test_actual_config_shape(self):
  self.assertEqual(len(a.verify_container(self.config())),14)
 def test_successful_mocked_runtime_requires_cleanup(self):
  cid='b'*64
  calls=[]
  class CP:
   def __init__(self,stdout='',returncode=0):self.stdout=stdout;self.returncode=returncode
  def fake_execute(args,timeout=12):
   calls.append(args[:2])
   if args[:2]==['docker','create']:return CP(cid)
   if args[:2]==['docker','start']:return CP(__import__('json').dumps(self.proof())+'\n')
   if args[:2]==['docker','inspect']:
    obj=self.config();obj['State']={'ExitCode':0,'OOMKilled':False}
    return CP(__import__('json').dumps([obj]))
   if args[:2]==['docker','rm']:return CP()
   raise RuntimeError(args)
  with patch.object(a,'execute',side_effect=fake_execute):
   result=a.run_runtime('sha256:'+'a'*64,{'hostname':'dedicated'})
  self.assertTrue(result['accepted'])
  self.assertEqual(result['process_exit'],0)
  self.assertIn(['docker','rm'],calls)
 def test_reject_failed_execution_and_cleanup(self):
  cid='c'*64
  calls=[]
  class CP:
   def __init__(self,stdout='',returncode=0):self.stdout=stdout;self.returncode=returncode
  def fake_execute(args,timeout=12):
   calls.append(args[:2])
   if args[:2]==['docker','create']:return CP(cid)
   if args[:2]==['docker','start']:return CP('',1)
   if args[:2]==['docker','inspect']:
    obj=self.config();obj['State']={'ExitCode':1,'OOMKilled':False}
    return CP(__import__('json').dumps([obj]))
   if args[:2]==['docker','rm']:return CP()
   raise RuntimeError(args)
  with patch.object(a,'execute',side_effect=fake_execute):
   with self.assertRaises(a.AcceptanceDenied):
    a.run_runtime('sha256:'+'a'*64,{'hostname':'dedicated'})
  self.assertIn(['docker','rm'],calls)
