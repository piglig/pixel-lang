"""JSON-lines compiler service for CLI/editor clients, with cancellable request IDs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import signal
import sys
import threading
import time

from .compiler_service import Limits, PROTOCOL_VERSION
from .compiler_worker import CompilerWorker, MAX_WIRE_BYTES, decode_wire, encode_wire, failure


def serve(worker, incoming, outgoing, shutdown=None):
    shutdown=shutdown or threading.Event()
    output_lock=threading.Lock()
    state_lock=threading.Lock()
    pending={}

    def emit(response):
        with output_lock:
            outgoing.write(json.dumps(encode_wire(response),ensure_ascii=False)+'\n')
            outgoing.flush()

    def run(request, event, submitted):
        try:
            result=worker.request(request,cancelled=lambda:event.is_set() or shutdown.is_set(),submitted_at=submitted)
            emit(result)
        except Exception as error:
            emit(failure(request,'internal',str(error)))
        finally:
            with state_lock:pending.pop(request['id'],None)

    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='compiler-rpc') as executor:
        drain=False
        try:
            while True:
                line=incoming.readline(MAX_WIRE_BYTES+1)
                if not line:
                    drain=True
                    return
                request={}
                submitted=time.monotonic()
                try:
                    if len(line)>MAX_WIRE_BYTES:
                        emit(failure({},'input-limit','JSON request exceeds transport budget'))
                        return  # Do not interpret a suffix of the oversized line as a request.
                    request=decode_wire(json.loads(line))
                    if not isinstance(request,dict):raise ValueError('Request must be an object')
                    if type(request.get('version')) is not int or request['version']!=PROTOCOL_VERSION:
                        raise ValueError('Unsupported protocol version')
                    if type(request.get('id')) not in (str,int):raise ValueError('id must be a string or integer')
                    if request.get('operation')=='cancel':
                        if type(request.get('target')) not in (str,int):raise ValueError('target must be a request ID')
                        with state_lock:
                            event=pending.get(request['target'])
                            if event:event.set()
                        emit(dict(version=1,id=request['id'],revision=request.get('revision'),status='ok',
                                  diagnostics=[],result={'cancelled':event is not None}))
                        continue
                    with state_lock:
                        if request['id'] in pending:raise ValueError('Duplicate active request ID')
                        if len(pending)>=8:raise ValueError('Compiler queue is full')
                        event=threading.Event();pending[request['id']]=event
                    executor.submit(run,request,event,submitted)
                except (ValueError,TypeError,KeyError,RecursionError) as error:
                    emit(failure(request if isinstance(request,dict) else {},'request',str(error)))
        finally:
            if not drain:
                with state_lock:
                    for event in pending.values():event.set()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',help='Explicit development compiler artifact; default uses the installed manifest')
    parser.add_argument('--timeout',type=float,default=120)
    args=parser.parse_args()
    try:limits=Limits(seconds=args.timeout)
    except ValueError as error:parser.error(str(error))
    shutdown=threading.Event()
    def interrupted(signum, frame):
        shutdown.set()
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        with CompilerWorker(args.compiler,limits=limits) as worker:
            serve(worker,sys.stdin.buffer,sys.stdout,shutdown)
    except KeyboardInterrupt:
        pass


if __name__=='__main__':main()
