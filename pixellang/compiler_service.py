"""Versioned compiler operations executed by a pinned PixelLang bytecode artifact.

The service owns request limits and transport. It never builds its own compiler
or invokes a host language/image frontend. Packaging supplies the compiler artifact.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import pickle
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from .bootstrap import normalized, read_bytecode_stream
from .fileaccess import FileAccess
from .model import PixelError
from .image_transport import read_parsed
from .vm import VM
from .code_image import CodeImage
from .compiler_protocol import check_native_output

PROTOCOL_VERSION = 1


def read_debug_document(event):
    """Decode JSON-valued metadata from a typed compiler output document.

    Pixel/link records travel under the compiler output budget, independently of
    the language's limit on any single JSON value. VM printing serializes each
    metadata value to JSON text; decode only these explicitly typed fields.
    """
    source = event['source']
    for document in (source, *source.get('bundle', {}).get('modules', {}).values()):
        document['metadata'] = {name: json.loads(value)
                                for name, value in document['metadata'].items()}
    return event


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
    def __init__(self, bytecode, *, limits=None, native_executable=None):
        self.limits=limits or Limits()
        self.compiler_sha256=hashlib.sha256(normalized(bytecode.data if isinstance(bytecode, CodeImage) else bytecode).encode('utf-8',errors='surrogatepass')).hexdigest()
        self.native=None
        self.resident=False
        self.session_revision=0
        self.session_nodes=0
        if native_executable is not None:
            from .native_execution import NativeExecution
            executable=Path(native_executable).resolve(strict=True)
            mapping=json.loads(executable.with_name(executable.name+'.map.json').read_text())
            from .native_bundle import validate_source_map
            validate_source_map(mapping,bytecode.data if isinstance(bytecode,CodeImage) else bytecode)
            if (mapping.get('bytecodeSha256')!=self.compiler_sha256 or
                mapping.get('executableSha256')!=hashlib.sha256(executable.read_bytes()).hexdigest()):
                raise ValueError('Native compiler does not match its bytecode and executable manifest')
            if (self.limits.heap_items,self.limits.heap_objects)!=(64_000_000,2_000_000):
                raise ValueError('Native compiler uses fixed byte allocation budgets; custom VM heap limits are unsupported')
            if mapping.get('session') is not None:
                from .native_session import NativeSessionExecution
                self.native=NativeSessionExecution(executable)
                self.resident=True
            else:
                self.native=NativeExecution(executable)
            self.vm=None
            self.initial=None
        else:
            self.vm=VM(bytecode,max_steps=self.limits.steps,max_depth=1024,
                       max_heap_items=self.limits.heap_items,max_heap_objects=self.limits.heap_objects,
                       max_output_chars=self.limits.output_chars)
            self.initial=self.vm.checkpoint()
        self.work_steps=0
        self.parse_hits=0
        self.parsed_modules=0
        self.artifacts=OrderedDict()
        self.artifact_bytes=0
        self.binding_hits=0
        self.bound_declarations=0
        self.type_hits=0
        self.typed_declarations=0
        self.semantic_hits=0
        self.semantic_checked=0
        self.ir_nodes=0
        self.ir_hits=0
        self.ir_relocated=0

    def close(self):
        close = getattr(self.native, 'close', None)
        if close is not None:
            close()

    @classmethod
    def from_bundle(cls, folder, *, limits=None):
        from .native_bundle import load_bundle
        code,executable,_=load_bundle(folder)
        return cls(code,limits=limits,native_executable=executable)

    @classmethod
    def installed(cls, *, limits=None):
        return cls.from_bundle(Path(__file__).parent/'artifacts',limits=limits)

    def _execute(self, request, root, cancelled, deadline):
        if self.resident:
            request={**request,'resident':True}
        text=json.dumps(request,ensure_ascii=False)
        if len(text)>1_000_000:
            raise PixelError('service','Compiler request exceeds VM input limit',code='service.input-limit')
        if self.work_steps>=self.limits.steps:
            raise PixelError("service","Compilation instruction budget exceeded",code="service.steps")
        if self.native is not None:
            execution = self.native
            isolated = self.resident and request.get('incremental', True) is False
            if isolated:
                from .native_session import NativeSessionExecution
                execution = NativeSessionExecution(self.native.executable)
                execution.profile_memory = self.native.profile_memory
            try:
                events=execution.execute(text,root=root,steps=self.limits.steps-self.work_steps,
                    output_chars=self.limits.output_chars,file_bytes=self.limits.file_bytes,
                    cancelled=cancelled,deadline=deadline)
                if self.resident:
                    if cancelled() or time.monotonic()>=deadline:
                        execution.close()
                        code='service.cancelled' if cancelled() else 'service.timeout'
                        raise PixelError('service','Compilation interrupted before response acceptance',code=code)
                    if (not events or not isinstance(events[-1],dict)
                        or events[-1].get('kind')!='compiler-session'):
                        execution.close()
                        raise PixelError('service','Missing compiler session statistics',code='service.invalid-response')
                    stats=events.pop()
                    names=('bindingHits','boundDeclarations','typeHits','typedDeclarations','parsed','parseHits','checkedFunctions','semanticHits','revision','retainedNodes','irHits','irRelocated','irNodes')
                    if any(type(stats.get(name)) is not int or stats[name]<0 for name in names):
                        execution.close()
                        raise PixelError('service','Invalid compiler session statistics',code='service.invalid-response')
                    self.parsed_modules+=stats['parsed']; self.parse_hits+=stats['parseHits']
                    self.binding_hits+=stats['bindingHits']; self.bound_declarations+=stats['boundDeclarations']
                    self.type_hits+=stats['typeHits']; self.typed_declarations+=stats['typedDeclarations']
                    self.semantic_checked+=stats['checkedFunctions']; self.semantic_hits+=stats['semanticHits']
                    if not isolated:
                        self.session_revision=stats['revision']; self.session_nodes=stats['retainedNodes']
                        self.ir_nodes=stats['irNodes']
                    self.ir_hits+=stats['irHits']; self.ir_relocated+=stats['irRelocated']
                check_native_output(events)
                return events
            finally:
                self.work_steps+=execution.steps
                if isolated:
                    execution.close()
                if not isolated and self.resident and execution.process is None:
                    self.session_revision=0; self.session_nodes=0; self.ir_nodes=0
        vm=self.vm
        vm.restore_checkpoint(self.initial)
        vm.max_steps=max(1,self.limits.steps-self.work_steps)
        vm.input_text=text
        vm.file_access=FileAccess(read_root=root,write_root=root,budget=self.limits.file_bytes,
                                  record=False,limit_total=True)
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
            check_native_output(vm.output)
            return vm.output
        finally:
            self.work_steps += vm.steps
            # Printed events are materialized values, independent of the heap.
            # Release request state before decoding those events into another
            # compiler-sized object graph, including on cancellation/failure.
            vm.restore_checkpoint(self.initial)
            vm.input_text = ""
            vm.file_access = None

    def request(self, request, *, cancelled=lambda: False):
        start=time.monotonic()
        self.work_steps=0
        self.parse_hits=0
        self.parsed_modules=0
        self.ir_hits=0
        self.ir_relocated=0
        self.binding_hits=0
        self.bound_declarations=0
        self.type_hits=0
        self.typed_declarations=0
        self.semantic_hits=0
        self.semantic_checked=0
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
            if 'reuseSemantic' in request or 'reuseIR' in request:
                raise ValueError('Per-stage cache switches were removed; use incremental to control session reuse')
            if type(request.get('incremental',True)) is not bool:
                raise ValueError('incremental must be a boolean')
            if operation not in ('check','compile','compile-pixels','recover-pixels','export-pixel-png','normalize-pixels','pixels','export-png','compile-png','recover-png','module-info','debug-compile','debug-pixels','export-debug-png','inspect-ir','debug-bundle','native-graphs','editor-index','format'):
                raise ValueError('Unknown compiler operation')
            source=request.get('entry','main.pxl')
            if not isinstance(source,str):
                raise ValueError('entry must be a string')
            if 'entryFunction' in request:
                if operation != 'compile' or not isinstance(request['entryFunction'], str) or not request['entryFunction']:
                    raise ValueError('entryFunction requires a nonempty function name and the compile operation')
            if operation in ('debug-compile','debug-bundle') and request.get('incremental',True) and request.get('reuseArtifacts',True):
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
                if self.resident:args['incremental']=request.get('incremental',True)
                if 'entryFunction' in request: args['entryFunction']=request['entryFunction']
                if operation in ('compile-pixels','recover-pixels','export-pixel-png','normalize-pixels'):
                    document=request.get('document')
                    if not isinstance(document,dict):
                        raise ValueError('document must be a pixel document object')
                    encoded=json.dumps(document,ensure_ascii=False,allow_nan=False).encode('utf-8')
                    if len(encoded)>self.limits.file_bytes:
                        raise ValueError('Pixel document exceeds the file budget')
                    args.update(operation={'compile-pixels':'pixel-document-bytecode-stream',
                                           'recover-pixels':'pixel-document-source',
                                           'export-pixel-png':'pixel-document-png-file',
                                           'normalize-pixels':'pixel-document-normalize'}[operation],native=document)
                    if operation=='export-pixel-png': args.update(file='output.png',scale=request.get('scale',8))
                elif operation in ('compile-png','recover-png'):
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
                    if operation=='editor-index':
                        editor_roots=request.get('editorRoots', sorted(files))
                        if (not isinstance(editor_roots,list) or len(editor_roots)>128
                            or not all(isinstance(path,str) and path in files for path in editor_roots)):
                            raise ValueError('editorRoots must list supplied source paths')
                        args['editorRoots']=sorted(set(editor_roots))
                    args['operation']={'check':'check-summary','compile':'bytecode-stream',
                                       'pixels':'pixels','export-png':'png-file','module-info':'parse-stream','debug-compile':'studio-bytecode-stream','debug-pixels':'studio-pixels','export-debug-png':'studio-png-file','inspect-ir':'ir','debug-bundle':'studio-bundle','native-graphs':'native-graphs','editor-index':'editor-index','format':'format'}[operation]
                    if operation=='debug-bundle':
                        if type(request.get('includeIR',False)) is not bool or type(request.get('includePNG',False)) is not bool:
                            raise ValueError('includeIR and includePNG must be booleans')
                        args['includeIR']=request.get('includeIR',False)
                        if request.get('includePNG',False):
                            args.update(file='output.png',scale=request.get('scale',8))
                    if operation in ('module-info','format'):
                        if source not in files: raise ValueError('Entry must name a supplied source file')
                        args['text']=files[source]
                        if operation=='format': del args['files']
                    if operation in ('export-png','export-debug-png'):
                        args.update(file='output.png',scale=request.get('scale',4))
                events=self._execute(args,root,cancelled,start+self.limits.seconds)
                if operation in ('inspect-ir','recover-png','recover-pixels','editor-index','format'):
                    events=[json.loads(events[0])]
                if operation=='debug-pixels':
                    events=[read_debug_document(events[0])]
                if operation=='debug-bundle':
                    end=next((i+1 for i,event in enumerate(events) if isinstance(event,str) and json.loads(event).get('kind')=='end'),None)
                    if end is None:raise ValueError('Missing debug bundle bytecode stream end')
                    built=read_bytecode_stream(events[:end])
                    result=None
                    if not built['diagnostics']:
                        expected=end+2+int(request.get('includeIR',False))
                        if len(events)!=expected:raise ValueError('Invalid debug bundle event count')
                        mapped=read_debug_document(events[end+1])
                        built['diagnostics']=mapped['diagnostics']
                        if not built['diagnostics']:
                            result=dict(bytecode=built['bytecode'],debug=events[end],document=mapped['source'])
                            if request.get('includeIR',False):result['ir']=json.loads(events[end+2])['ir']
                            if request.get('includePNG',False):result['image']=(root/'output.png').read_bytes()
                elif operation=='module-info':
                    parsed=read_parsed(events[:-1])
                    built={'diagnostics':parsed['syntax']['diagnostics']}
                    result={key:events[-1][key] for key in ('imports','tests','hasMain')}
                elif operation in ('compile','compile-png','compile-pixels','debug-compile'):
                    built=read_bytecode_stream(events[:-1] if operation=='debug-compile' else events)
                    result={'bytecode':built['bytecode']} if not built['diagnostics'] else None
                    if operation=='debug-compile' and result is not None:
                        result['debug']=events[-1]
                else:
                    if len(events)!=1 or not isinstance(events[0],dict):
                        raise ValueError('Invalid compiler response')
                    built=events[0]
                    result=({'index':built} if operation=='editor-index' else {'projection':built['projection']} if operation=='native-graphs' else {'document':built['source']} if operation in ('pixels','debug-pixels','normalize-pixels') and not built['diagnostics']
                            else {'image':(root/'output.png').read_bytes()} if operation in ('export-png','export-debug-png','export-pixel-png') and not built['diagnostics']
                            else {'text':built['text']} if operation=='format' and not built['diagnostics']
                            else {'ir':built['ir']} if operation=='inspect-ir' and not built['diagnostics']
                            else {'files':built['files'],'entry':built['entry']} if operation in ('recover-png','recover-pixels') and not built['diagnostics'] else {})
                if operation=='check':
                    built['diagnostics'].sort(key=lambda d:(d['source'],d['start'],d['phase'],d['message']))
                    result={'checkedModules':built['checkedModules'],'globalFailure':built['globalFailure']}
                response['diagnostics']=built['diagnostics']
                response['status']='diagnostics' if built['diagnostics'] and operation!='editor-index' else 'ok'
                response['result']=result
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
                             'parseHits':self.parse_hits,'parsedModules':self.parsed_modules,
                             'artifactCacheHit':artifact_hit,'artifactCacheBytes':self.artifact_bytes}
        response['metrics'].update(bindingCacheHits=self.binding_hits,bindingCheckedDeclarations=self.bound_declarations,
            typeCacheHits=self.type_hits,typeCheckedDeclarations=self.typed_declarations)
        response['metrics'].update(semanticCacheHits=self.semantic_hits,semanticCheckedFunctions=self.semantic_checked,
            sessionRevision=self.session_revision,residentCacheNodes=self.session_nodes)
        response['metrics'].update(irCacheHits=self.ir_hits,irCacheRelocated=self.ir_relocated,
            residentIRNodes=self.ir_nodes)
        return response
