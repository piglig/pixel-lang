"""Versioned compiler operations executed by a pinned PixelLang bytecode artifact.

The service owns request limits and transport. It never builds its own compiler
or invokes a host language/image frontend. Packaging supplies the compiler artifact.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import posixpath
import re
import pickle
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from .bootstrap import normalized, read_bytecode_stream
from .fileaccess import FileAccess
from .model import PixelError
from .image_workspace import read_parsed, stage_parsed
from .vm import VM

PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class Limits:
    seconds: float = 120
    steps: int = 500_000_000
    heap_items: int = 64_000_000
    heap_objects: int = 2_000_000
    output_chars: int = 64_000_000
    file_bytes: int = 64_000_000

    def __post_init__(self):
        if isinstance(self.seconds, bool) or not isinstance(self.seconds, (int, float)) or not math.isfinite(self.seconds) or not 0 < self.seconds <= 1800:
            raise ValueError('seconds must be finite and in (0, 1800]')
        for name, maximum in [('steps',3_000_000_000), ('heap_items',64_000_000),
                              ('heap_objects',2_000_000), ('output_chars',64_000_000),
                              ('file_bytes',64_000_000)]:
            value=getattr(self,name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f'{name} must be an integer in 1..{maximum}')


class CompilerService:
    """One sequential execution lane; callers supervise concurrency/cancellation."""
    def __init__(self, bytecode, *, limits=None):
        self.limits=limits or Limits()
        self.compiler_sha256=hashlib.sha256(normalized(bytecode).encode()).hexdigest()
        self.vm=VM(bytecode,max_steps=self.limits.steps,max_depth=1024,
                   max_heap_items=self.limits.heap_items,max_heap_objects=self.limits.heap_objects,
                   max_output_chars=self.limits.output_chars)
        self.initial=self.vm.checkpoint()
        self.parses=OrderedDict()
        self.parse_bytes=0
        self.work_steps=0
        self.parse_hits=0
        self.parsed_modules=0
        self.analysis=None
        self.artifacts=OrderedDict()
        self.artifact_bytes=0
        self.ir_cache=None
        self.ir_hits=0
        self.ir_relocated=0

    def _stage_ir_cache(self, files, entry, root):
        if posixpath.normpath(entry)!=entry or any(posixpath.normpath(p)!=p for p in files):
            # Project normalization may merge or rename keys. Until the host has
            # the compiler's canonical graph, do not infer dependency identity.
            return None
        # Cache source identity and transitive interface identity separately:
        # changing an imported body need not invalidate its callers' lowering.
        stamps={}
        for path,text in files.items():
            dependencies=set()
            pending=[path]
            while pending:
                target=pending.pop()
                if target in dependencies:continue
                dependencies.add(target)
                pending.extend(self.source_info.get(target,{}).get('imports',[]))
            identity=[text,{p:self.source_info.get(p) for p in sorted(dependencies)}]
            stamps[path]=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        limit=min(8_000_000,self.limits.file_bytes//8)
        if limit<1_000_000:return None
        environment=hashlib.sha256(json.dumps([self.compiler_sha256,entry,asdict(self.limits)],sort_keys=True).encode()).hexdigest()
        request=dict(outputPath='ir-index-out.json',environment=environment,stamps=stamps,byteLimit=limit)
        if self.ir_cache is not None:
            index=json.loads(self.ir_cache['ir-index-in.json'])
            # Keep prior payloads in a disjoint namespace. A new function may
            # take an old numeric ID while another function retains its bytes.
            for key,name in list(index['functions'].items()):
                staged='prior-ir-function-'+key+'.json'
                (root/staged).write_bytes(self.ir_cache[name])
                index['functions'][key]=staged
            (root/'ir-index-in.json').write_text(json.dumps(index,ensure_ascii=False))
            request['priorPath']='ir-index-in.json'
        return request

    def _retain_ir_cache(self,root):
        path=root/'ir-index-out.json'
        if not path.is_file():return
        try:
            data=path.read_bytes()
            index=json.loads(data)
            if not isinstance(index,dict) or not isinstance(index.get('functions'),dict):return
            retained={'ir-index-in.json':data}
            size=len(data)
            if size>min(8_000_000,self.limits.file_bytes//8):return
            for key,name in index['functions'].items():
                if (not isinstance(key,str) or not key.isdecimal() or not isinstance(name,str)
                    or re.fullmatch(r'(?:prior-)?ir-function-[0-9]+\.json',name) is None):return
                payload=(root/name).read_bytes()
                size+=len(payload)
                if size>min(8_000_000,self.limits.file_bytes//8):return
                retained[name]=payload
            if any(type(index[k]) is not int or index[k]<0 for k in ('hits','relocated')):return
            return retained,index['hits'],index['relocated']
        except (OSError,ValueError,KeyError,TypeError):
            # Optional cache publication cannot turn a successful compilation
            # into a failure. No partial index is ever installed.
            return

    @classmethod
    def installed(cls, *, limits=None):
        folder=Path(__file__).parent/'artifacts'
        manifest=json.loads((folder/'compiler-manifest.json').read_text())
        data=(folder/'compiler.json').read_bytes()
        if manifest.get('protocolVersion') != PROTOCOL_VERSION or hashlib.sha256(data).hexdigest()!=manifest.get('sha256'):
            raise ValueError('Installed compiler artifact does not match its manifest')
        return cls(json.loads(data),limits=limits)

    def _execute(self, request, root, cancelled, deadline):
        text=json.dumps(request,ensure_ascii=False)
        if len(text)>1_000_000:
            raise PixelError('service','Compiler request exceeds VM input limit',code='service.input-limit')
        if self.work_steps>=self.limits.steps:
            raise PixelError("service","Compilation instruction budget exceeded",code="service.steps")
        vm=self.vm
        vm.restore_checkpoint(self.initial)
        vm.max_steps=max(1,self.limits.steps-self.work_steps)
        vm.input_text=text
        vm.file_access=FileAccess(read_root=root,write_root=root,budget=self.limits.file_bytes)
        try:
            while not vm.halted:
                if vm.steps % 1000 == 0:
                    if cancelled():
                        raise PixelError('service','Compilation cancelled',code='service.cancelled')
                    if time.monotonic()>=deadline:
                        raise PixelError('service','Compilation deadline exceeded',code='service.timeout')
                vm.step(snapshot=False)
            if cancelled():
                raise PixelError('service','Compilation cancelled',code='service.cancelled')
            if time.monotonic()>=deadline:
                raise PixelError('service','Compilation deadline exceeded',code='service.timeout')
            return vm.output
        finally:
            self.work_steps += vm.steps

    def _prepare(self, files, root, cancelled, deadline):
        paths={}
        self.source_info={}
        for index, (path, text) in enumerate(files.items()):
            key=hashlib.sha256(json.dumps([self.compiler_sha256,path,text],ensure_ascii=False).encode()).hexdigest()
            encoded=self.parses.pop(key,None)
            if encoded is None:
                events=self._execute(dict(operation="parse-stream",source=path,text=text),root,cancelled,deadline)
                parsed=read_parsed(events[:-1])
                parsed["info"]=events[-1]
                encoded=json.dumps(parsed,ensure_ascii=False).encode()
                self.parsed_modules+=1
                if len(encoded)<=32_000_000:
                    while self.parses and (self.parse_bytes+len(encoded)>32_000_000 or len(self.parses)>=128):
                        _, old=self.parses.popitem(last=False); self.parse_bytes-=len(old)
                    self.parses[key]=encoded; self.parse_bytes+=len(encoded)
            else:
                self.parses[key]=encoded
                parsed=json.loads(encoded)
                self.parse_hits+=1
            self.source_info[path]=parsed["info"]
            paths[path]=stage_parsed(root,str(index),parsed)
        return paths

    def _selected_modules(self, files, entry):
        if any(posixpath.normpath(path)!=path for path in files):
            return None,None
        previous=self.analysis
        hashes={p:hashlib.sha256(t.encode()).hexdigest() for p,t in files.items()}
        current=dict(entry=entry,hashes=hashes,info=self.source_info)
        if previous is None or previous['entry']!=entry:
            return set(files),current
        changed={p for p in hashes.keys()|previous['hashes'].keys() if hashes.get(p)!=previous['hashes'].get(p)}
        old=previous['info']; new=current['info']
        interfaces={p for p in new.keys()|old.keys() if new.get(p,{}).get('interface')!=old.get(p,{}).get('interface')}
        reverse={}
        for graph in (old,new):
            for path,info in graph.items():
                for target in info['imports']: reverse.setdefault(target,set()).add(path)
        pending=list(interfaces); invalid=set(interfaces)
        while pending:
            for path in reverse.get(pending.pop(),()):
                if path not in invalid: invalid.add(path); pending.append(path)
        return (changed|invalid)&files.keys(),current

    def request(self, request, *, cancelled=lambda: False):
        start=time.monotonic()
        self.work_steps=0
        self.parse_hits=0
        self.parsed_modules=0
        self.ir_hits=0
        self.ir_relocated=0
        analysis=None
        selected=None
        artifact_key=None
        response={'version':PROTOCOL_VERSION,'id':None,'revision':None,
                  'compilerSha256':self.compiler_sha256,'diagnostics':[]}
        try:
            if not isinstance(request,dict):
                raise ValueError('Request must be an object')
            response.update(id=request.get('id'),revision=request.get('revision'))
            if type(request.get('version')) is not int or request['version']!=PROTOCOL_VERSION:
                raise ValueError('Unsupported compiler protocol version')
            operation=request.get('operation')
            if type(request.get('reuseIR',True)) is not bool:
                raise ValueError('reuseIR must be a boolean')
            if operation not in ('check','compile','pixels','export-png','compile-png','recover-png','module-info','debug-compile','debug-pixels','export-debug-png','inspect-ir','debug-bundle'):
                raise ValueError('Unknown compiler operation')
            source=request.get('entry','main.pxl')
            if not isinstance(source,str):
                raise ValueError('entry must be a string')
            if operation=='debug-bundle' and request.get('incremental',True) and request.get('reuseArtifacts',True):
                # Validate before canonical JSON encoding: JSON coerces integer map
                # keys to strings, which must never let malformed inputs hit a cache.
                files=request.get('files')
                if not isinstance(files,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in files.items()):
                    raise ValueError('files must map module paths to source text')
                if len(files)>128:raise ValueError('Project exceeds 128 files')
                if type(request.get('includeIR',False)) is not bool or type(request.get('includePNG',False)) is not bool:
                    raise ValueError('includeIR and includePNG must be booleans')
                identity={k:v for k,v in request.items() if k not in ('id','revision')}
                artifact_key=hashlib.sha256(json.dumps([self.compiler_sha256,asdict(self.limits),identity],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                cached=self.artifacts.pop(artifact_key,None)
                if cached is not None:
                    self.artifacts[artifact_key]=cached
                    if cancelled():raise PixelError('service','Compilation cancelled',code='service.cancelled')
                    if time.monotonic()>=start+self.limits.seconds:raise PixelError('service','Compilation deadline exceeded',code='service.timeout')
                    # Only bytes created by this service instance enter this private cache.
                    response.update(status='ok',result=pickle.loads(cached))
                    return self._finish(response,start,artifact_hit=True)
            with TemporaryDirectory(prefix='pixellang-service-') as directory:
                root=Path(directory)
                args=dict(source=source,text='')
                if operation in ('compile-png','recover-png'):
                    image=request.get('image')
                    if not isinstance(image,bytes) or len(image)>self.limits.file_bytes:
                        raise ValueError('image must be PNG bytes within the file budget')
                    (root/'input.png').write_bytes(image)
                    args.update(operation='png-file-source' if operation=='recover-png' else 'png-file-bytecode-stream',file='input.png',
                                byteLimit=self.limits.file_bytes,imageLimit=self.limits.file_bytes)
                else:
                    files=request.get('files')
                    if not isinstance(files,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in files.items()):
                        raise ValueError('files must map module paths to source text')
                    if len(files)>128:
                        raise ValueError('Project exceeds 128 files')
                    args['files']=files
                    if operation!='module-info' and request.get('incremental',True):
                        args['parsedFiles']=self._prepare(files,root,cancelled,start+self.limits.seconds)
                        if operation=='check':
                            selected,analysis=self._selected_modules(files,source)
                            if selected is not None:
                                args['checkModules']=sorted(selected)
                    args['operation']={'check':'check-summary','compile':'bytecode-stream',
                                       'pixels':'pixels','export-png':'png-file','module-info':'parse-stream','debug-compile':'studio-bytecode-stream','debug-pixels':'studio-pixels','export-debug-png':'studio-png-file','inspect-ir':'ir','debug-bundle':'studio-bundle'}[operation]
                    if operation=='debug-bundle':
                        if type(request.get('includeIR',False)) is not bool or type(request.get('includePNG',False)) is not bool:
                            raise ValueError('includeIR and includePNG must be booleans')
                        args['includeIR']=request.get('includeIR',False)
                        if request.get('includePNG',False):
                            args.update(file='output.png',scale=request.get('scale',8))
                    if operation=='module-info':
                        args['text']=files[source]
                    if operation in ('export-png','export-debug-png'):
                        args.update(file='output.png',scale=request.get('scale',4))
                if (operation in ('compile','debug-compile','inspect-ir','debug-bundle')
                    and request.get('incremental',True) and request.get('reuseIR',True)):
                    cache_request=self._stage_ir_cache(files,source,root)
                    if cache_request is not None:args['irCache']=cache_request
                events=self._execute(args,root,cancelled,start+self.limits.seconds)
                if operation in ('debug-pixels','inspect-ir','recover-png'):
                    events=[json.loads(events[0])]
                if operation=='debug-bundle':
                    end=next((i+1 for i,event in enumerate(events) if isinstance(event,str) and json.loads(event).get('kind')=='end'),None)
                    if end is None:raise ValueError('Missing debug bundle bytecode stream end')
                    built=read_bytecode_stream(events[:end])
                    result=None
                    if not built['diagnostics']:
                        expected=end+2+int(request.get('includeIR',False))
                        if len(events)!=expected:raise ValueError('Invalid debug bundle event count')
                        mapped=json.loads(events[end+1])
                        built['diagnostics']=mapped['diagnostics']
                        if not built['diagnostics']:
                            result=dict(bytecode=built['bytecode'],debug=events[end],document=mapped['source'])
                            if request.get('includeIR',False):result['ir']=json.loads(events[end+2])['ir']
                            if request.get('includePNG',False):result['image']=(root/'output.png').read_bytes()
                elif operation=='module-info':
                    parsed=read_parsed(events[:-1])
                    built={'diagnostics':parsed['syntax']['diagnostics']}
                    result={'imports':events[-1]['imports']}
                elif operation in ('compile','compile-png','debug-compile'):
                    built=read_bytecode_stream(events[:-1] if operation=='debug-compile' else events)
                    result={'bytecode':built['bytecode']} if not built['diagnostics'] else None
                    if operation=='debug-compile' and result is not None:
                        result['debug']=events[-1]
                else:
                    if len(events)!=1 or not isinstance(events[0],dict):
                        raise ValueError('Invalid compiler response')
                    built=events[0]
                    result=({'document':built['source']} if operation in ('pixels','debug-pixels') and not built['diagnostics']
                            else {'image':(root/'output.png').read_bytes()} if operation in ('export-png','export-debug-png') and not built['diagnostics']
                            else {'ir':built['ir']} if operation=='inspect-ir' and not built['diagnostics']
                            else {'files':built['files'],'entry':built['entry']} if operation=='recover-png' and not built['diagnostics'] else {})
                if operation=='check':
                    if analysis is not None and not built['globalFailure']:
                        retained=[d for d in (self.analysis or {}).get('diagnostics',[]) if d['source'] not in selected and d['source'] in files]
                        built['diagnostics']=retained+built['diagnostics']
                        built['diagnostics'].sort(key=lambda d:(d['source'],d['start'],d['phase'],d['message']))
                        analysis['diagnostics']=built['diagnostics']
                        self.analysis=analysis
                    else:
                        self.analysis=None
                        built['diagnostics'].sort(key=lambda d:(d['source'],d['start'],d['phase'],d['message']))
                    result={'checkedModules':built['checkedModules'],'globalFailure':built['globalFailure']}
                response['diagnostics']=built['diagnostics']
                response['status']='diagnostics' if built['diagnostics'] else 'ok'
                response['result']=result
                if response['status']=='ok' and 'irCache' in args:
                    retained=self._retain_ir_cache(root)
                    if cancelled():raise PixelError('service','Compilation cancelled',code='service.cancelled')
                    if time.monotonic()>=start+self.limits.seconds:raise PixelError('service','Compilation deadline exceeded',code='service.timeout')
                    if retained is not None:
                        self.ir_cache,self.ir_hits,self.ir_relocated=retained
        except PixelError as error:
            response.pop('result',None)
            response.update(status={'service.cancelled':'cancelled','service.timeout':'timeout'}.get(error.code,'error'),error=error.to_dict())
        except (ValueError,TypeError,KeyError,OSError) as error:
            response.update(status='error',error={'code':'service.request','phase':'service','message':str(error)})
        if artifact_key is not None and response.get('status')=='ok':
            encoded=pickle.dumps(response['result'],protocol=5)
            if len(encoded)<=32_000_000:
                while self.artifacts and (self.artifact_bytes+len(encoded)>32_000_000 or len(self.artifacts)>=16):
                    _,old=self.artifacts.popitem(last=False);self.artifact_bytes-=len(old)
                self.artifacts[artifact_key]=encoded;self.artifact_bytes+=len(encoded)
        return self._finish(response,start)

    def _finish(self,response,start,artifact_hit=False):
        response['metrics']={'seconds':time.monotonic()-start,'steps':self.work_steps,
                             'parseHits':self.parse_hits,'parsedModules':self.parsed_modules,'parseCacheBytes':self.parse_bytes,
                             'artifactCacheHit':artifact_hit,'artifactCacheBytes':self.artifact_bytes}
        response['metrics'].update(irCacheHits=self.ir_hits,irCacheRelocated=self.ir_relocated,
            irCacheBytes=sum(map(len,self.ir_cache.values())) if self.ir_cache else 0)
        return response
