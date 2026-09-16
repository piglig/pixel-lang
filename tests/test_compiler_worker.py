from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest

from pixellang.bootstrap import normalized
from pixellang.compiler_service import Limits
from pixellang.compiler_worker import CompilerWorker, encode_wire, decode_wire
from pixellang.project import compile_project
from pixellang.vm import VM


class CompilerWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=TemporaryDirectory()
        cls.path=Path(cls.folder.name)/'compiler.json'
        root=Path(__file__).resolve().parents[1]
        code=compile_project({p.name:p.read_text() for p in (root/'selfhost').glob('*.pxl')})[1].bytecode
        cls.path.write_text(normalized(code))

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def test_persistent_process_cache_and_wire_roundtrip(self):
        value={'image':b'\x00\xff','constants':[{'$bytes':'literal'},{'$escaped':'literal'}]}
        self.assertEqual(decode_wire(encode_wire(value)),value)
        request=dict(version=1,id=1,revision=7,operation='check',files={'main.pxl':'fn main(){}'})
        with CompilerWorker(self.path) as worker:
            cold=worker.request(request)
            self.assertEqual(cold['status'],'ok',cold)
            pid=worker.process.pid
            warm=worker.request(request)
            self.assertEqual(worker.process.pid,pid)
            self.assertEqual(warm['metrics']['parseHits'],1)
            self.assertEqual(warm['result']['checkedModules'],[])
            request['operation']='export-png'
            png=worker.request(request)
            self.assertEqual(png['status'],'ok',png)
            restored=worker.request(dict(version=1,id=3,operation='compile-png',image=png['result']['image']))
            self.assertEqual(restored['status'],'ok',restored)
            self.assertEqual(VM(restored['result']['bytecode']).run(),[])

    def test_running_cancel_kills_child_and_next_request_restarts(self):
        request=dict(version=1,id=1,operation='check',files={'main.pxl':'fn main(){}'})
        with CompilerWorker(self.path) as worker:
            self.assertEqual(worker.request(request)['status'],'ok')
            pid=worker.process.pid
            stop=threading.Event()
            slow=dict(request,operation='compile',files={'main.pxl':'\n'.join(f'fn f{i}()->int={i}' for i in range(2000))+'\nfn main(){}'})
            timer=threading.Timer(.15,stop.set);timer.start()
            began=time.monotonic()
            try:result=worker.request(slow,cancelled=stop.is_set)
            finally:timer.cancel()
            self.assertEqual(result['status'],'cancelled',result)
            self.assertLess(time.monotonic()-began,3)
            self.assertIsNone(worker.process)
            again=worker.request(request)
            self.assertEqual(again['status'],'ok',again)
            self.assertNotEqual(worker.process.pid,pid)

    def test_supervisor_deadline_and_closed_worker(self):
        with CompilerWorker(self.path,limits=Limits(seconds=.001)) as worker:
            result=worker.request(dict(version=1,id=1,operation='check',files={'main.pxl':'fn main(){}'}))
            self.assertEqual(result['status'],'timeout',result)
            self.assertIsNone(worker.process)
        self.assertEqual(worker.request({})['status'],'error')
        with CompilerWorker(self.path) as queued:
            expired=queued.request({},submitted_at=time.monotonic()-121)
            self.assertEqual(expired['status'],'timeout')
            self.assertIsNone(queued.process)

    def test_json_server_correlates_cancel_and_survives_new_requests(self):
        import json
        import queue
        import subprocess
        import sys
        root=Path(__file__).resolve().parents[1]
        process=subprocess.Popen([sys.executable,'-u','-m','pixellang.compiler_rpc','--compiler',str(self.path)],
                                 cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        responses=queue.Queue()
        def read():
            for line in process.stdout:
                responses.put(json.loads(line))
        reader=threading.Thread(target=read,daemon=True);reader.start()
        def send(value):
            process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
        try:
            send(dict(version=1,id=1,revision=4,operation='check',files={'main.pxl':'fn main(){}'}))
            first=responses.get(timeout=20)
            self.assertEqual((first['id'],first['revision'],first['status']),(1,4,'ok'))
            send(dict(version=1,id=2,revision=5,operation='compile',files={'main.pxl':
                 '\n'.join(f'fn f{i}()->int={i}' for i in range(2000))+'\nfn main(){}'}))
            send(dict(version=1,id=3,operation='cancel',target=2))
            pair={r['id']:r for r in [responses.get(timeout=10),responses.get(timeout=10)]}
            self.assertEqual(pair[2]['status'],'cancelled',pair)
            self.assertTrue(pair[3]['result']['cancelled'])
            send(dict(version=1,id=4,revision=6,operation='check',files={'main.pxl':'fn main(){}'}))
            final=responses.get(timeout=20)
            self.assertEqual((final['id'],final['revision'],final['status']),(4,6,'ok'))
        finally:
            process.stdin.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            reader.join(timeout=2)
            stderr=process.stderr.read()
            process.stdout.close();process.stderr.close()
        self.assertEqual(process.returncode,0,stderr)

    def test_json_eof_drains_and_termination_stops_active_job(self):
        import json
        import subprocess
        import sys
        command=[sys.executable,'-u','-m','pixellang.compiler_rpc','--compiler',str(self.path)]
        request=dict(version=1,id=1,operation='check',files={'main.pxl':'fn main(){}'})
        completed=subprocess.run(command,input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=20)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'],'ok')
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            process.stdin.write(json.dumps(request)+'\n');process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())['status'],'ok')
            request.update(id=2,operation='compile',files={'main.pxl':'\n'.join(f'fn f{i}()->int={i}' for i in range(2000))+'\nfn main(){}'})
            process.stdin.write(json.dumps(request)+'\n');process.stdin.flush()
            time.sleep(.15)
            process.terminate()
            process.wait(timeout=5)
            self.assertEqual(process.returncode,0,process.stderr.read())
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdin.close();process.stdout.close();process.stderr.close()
