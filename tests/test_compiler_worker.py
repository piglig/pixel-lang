from pathlib import Path
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from pixellang.bootstrap import normalized
from pixellang.native_bundle import load_bundle
from pixellang.compiler_service import CompilerService, Limits
from pixellang.compiler_worker import CompilerWorker, encode_wire, decode_wire, dump_response, load_response
from pixellang.vm import VM


def unresponsive_worker(connection, cancelled, marker, limits):
    connection.send({'ready': True})
    connection.recv()
    Path(marker).write_text('executing')
    time.sleep(30)


def late_success_worker(connection, cancelled, marker, limits):
    connection.send({'ready': True})
    first=True
    while True:
        directory=connection.recv()
        if directory is None:return
        folder=Path(directory)
        request=json.loads((folder/'request.json').read_text())
        if first:
            Path(marker).write_text('executing')
            cancelled.wait(2)
            first=False
        (folder/'result.bin').write_bytes(dump_response(dict(status='ok',id=request['id'],result=request['id'])))
        connection.send({'done':True})


def descendant_worker(connection, cancelled, marker, limits):
    connection.send({'ready':True})
    connection.recv()
    # A child that ignores graceful termination must still be stopped when the
    # supervisor kills its worker. Publish only after the handler is installed.
    child=subprocess.Popen([sys.executable,'-c',
        'import os,signal,time; from pathlib import Path; '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)'])
    child.wait()


class CompilerWorkerTests(unittest.TestCase):
    def test_internal_response_preserves_sharing_and_rejects_executable_objects(self):
        span={'source':'猫.pxl','points':[[1,2]]}
        result=load_response(dump_response({'spans':[span,span],'image':b'\x00\xff'}))
        self.assertIs(result['spans'][0],result['spans'][1])
        self.assertEqual(result['image'],b'\x00\xff')
        for payload in (dump_response({'callable':eval}),dump_response({})+b'trailing',b'broken'):
            with self.subTest(payload=payload),self.assertRaises(ValueError):load_response(payload)

    @classmethod
    def setUpClass(cls):
        cls.folder=TemporaryDirectory()
        cls.path=Path(cls.folder.name)/'compiler.json'
        # Transport tests use the validated installed compiler. Self-compilation
        # belongs to the bootstrap gates and need not precede every worker test.
        code=load_bundle(Path(__file__).resolve().parents[1]/'pixellang/artifacts')[0]
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
            self.assertEqual(warm['metrics']['parseHits'],0)
            self.assertEqual(warm['result']['checkedModules'],['main.pxl'])
            request['operation']='export-png'
            png=worker.request(request)
            self.assertEqual(png['status'],'ok',png)
            restored=worker.request(dict(version=1,id=3,operation='compile-png',image=png['result']['image']))
            self.assertEqual(restored['status'],'ok',restored)
            self.assertEqual(VM(restored['result']['bytecode']).run(),[])

    def test_normal_close_reaps_worker_without_signaling_departed_group(self):
        worker=CompilerWorker()
        self.addCleanup(worker.close)
        response=worker.request(dict(version=1,operation='check',files={'main.pxl':'fn main(){}'}))
        self.assertEqual(response['status'],'ok',response)
        process=worker.process
        with patch('pixellang.compiler_worker.os.killpg',side_effect=AssertionError('graceful close must not signal a departed group')):
            worker.close()
        self.assertIsNone(worker.process)
        self.assertTrue(worker.closed)
        self.assertTrue(process._closed)

    def test_running_cancel_preserves_responsive_child_and_cache(self):
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
            self.assertIsNotNone(worker.process)
            self.assertEqual(worker.process.pid,pid)
            again=worker.request(request)
            self.assertEqual(again['status'],'ok',again)
            self.assertEqual(worker.process.pid,pid)
            self.assertEqual(again['metrics']['parseHits'],0)

    def test_unresponsive_cancel_still_terminates_child(self):
        with TemporaryDirectory() as directory:
            marker=Path(directory)/'started'
            with patch('pixellang.compiler_worker._worker',unresponsive_worker), CompilerWorker(marker) as worker:
                began=time.monotonic()
                result=worker.request(dict(version=1,operation='check'),cancelled=marker.exists)
                self.assertEqual(result['status'],'cancelled',result)
                self.assertLess(time.monotonic()-began,3)
                self.assertIsNone(worker.process)

    @unittest.skipUnless(os.name=='posix','POSIX process groups required')
    def test_forced_cancel_stops_native_descendant_even_after_leader_exit(self):
        with TemporaryDirectory() as directory:
            marker=Path(directory)/'child-pid'
            with patch('pixellang.compiler_worker._worker',descendant_worker),CompilerWorker(marker) as worker:
                result=worker.request(dict(version=1,operation='check'),cancelled=marker.exists)
                self.assertEqual(result['status'],'cancelled',result)
                pid=int(marker.read_text())
                deadline=time.monotonic()+2
                while True:
                    state=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
                    if not state or state.startswith('Z'):break
                    if time.monotonic()>=deadline:self.fail(f'Compiler descendant {pid} is still running: {state}')
                    time.sleep(.02)
                self.assertIsNone(worker.process)

    def test_cancel_discards_late_success_before_reusing_child(self):
        with TemporaryDirectory() as directory:
            marker=Path(directory)/'started'
            with patch('pixellang.compiler_worker._worker',late_success_worker), CompilerWorker(marker) as worker:
                result=worker.request(dict(version=1,id=1),cancelled=marker.exists)
                self.assertEqual(result['status'],'cancelled',result)
                self.assertNotIn('result',result)
                pid=worker.process.pid
                again=worker.request(dict(version=1,id=2))
                self.assertEqual((again['status'],again['id'],again['result']),('ok',2,2))
                self.assertEqual(worker.process.pid,pid)

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
