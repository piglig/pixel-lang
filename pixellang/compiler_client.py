"""Shared application adapter for the supervised self-hosted compiler service."""
from dataclasses import dataclass
from pathlib import Path
import time
from copy import deepcopy

from .compiler_worker import CompilerWorker
from .model import PixelError


@dataclass
class CompiledArtifact:
    bytecode: dict
    source: dict | None = None
    debug: dict | None = None
    source_project: dict | None = None
    ir: dict | None = None
    image: bytes | None = None

    def inspect(self):
        return {'bytecode':self.bytecode,'source':self.source,'debug':self.debug,'ir':self.ir}


class CompilerClient:
    def __init__(self, worker=None):
        self.worker=worker or CompilerWorker()
        self.owned=worker is None
        self.next_id=0

    def sources(self, files):
        library={'std/'+p.name:p.read_text() for p in (Path(__file__).parent/'stdlib').glob('*.pxl')}
        return {**library,**files}

    def request(self, operation, *, files=None, entry='main.pxl', cancelled=lambda:False, revision=None, submitted_at=None, **args):
        self.next_id+=1
        sources=(files if operation=="module-info" else self.sources(files)) if files is not None else None
        request=dict(version=1,id=self.next_id,revision=revision,operation=operation,entry=entry,**args)
        if sources is not None:request['files']=sources
        response=self.worker.request(request,cancelled=cancelled,submitted_at=submitted_at)
        if response['status']=='ok':return response
        diagnostics=response.get('diagnostics',[])
        error=diagnostics[0] if diagnostics else response.get('error',{})
        span=None
        if diagnostics:
            source=error['source'];text=(sources or {}).get(source,'')
            def point(offset):
                prefix=text[:offset]
                return dict(offset=offset,line=prefix.count('\n')+1,column=len(prefix.rsplit('\n',1)[-1])+1)
            span=dict(source=source,kind='text',start=point(error['start']),end=point(error['end']),min=[0,0],max=[0,0],points=[])
        exception=PixelError(error.get('phase','service'),error.get('message','Compilation failed'),span,
                             code=error.get('code','compiler.diagnostic'))
        exception.diagnostics=diagnostics
        exception.status=response['status']
        raise exception

    def debug_project(self, files, entry='main.pxl', *, cancelled=lambda:False, submitted_at=None, include_ir=False, include_png=False, scale=8):
        from .debug_artifact import prepare_debug
        submitted_at=time.monotonic() if submitted_at is None else submitted_at
        snapshot=self.sources(files)
        compiled=self.request('debug-bundle',files=snapshot,entry=entry,cancelled=cancelled,
                              submitted_at=submitted_at,includeIR=include_ir,includePNG=include_png,scale=scale)['result']
        artifact=prepare_debug(compiled['bytecode'],compiled['debug'],compiled['document'])
        artifact.source_project=deepcopy(dict(files=snapshot,entry=entry))
        artifact.ir=compiled.get('ir')
        artifact.image=compiled.get('image')
        return artifact

    def read_project(self, path):
        from .project import safe_name
        path=Path(path).resolve();root=path.parent
        files={};pending=[path.name]
        library=self.sources({})
        while pending:
            name=safe_name(pending.pop())
            if name in files:continue
            if len(files)>=128:raise PixelError('project','Project exceeds 128 files')
            target=(root/name).resolve()
            if not target.is_relative_to(root):raise PixelError('project','Import escapes the project root')
            if not target.exists() and name in library:
                text=library[name]
            else:
                if target.stat().st_size>4_000_000:raise PixelError('project','Text file exceeds 4 MB')
                text=target.read_text(encoding='utf-8')
            files[name]=text
            info=self.request('module-info',files={name:text},entry=name)['result']
            pending.extend(reversed(info['imports']))
        return files,path.name

    def close(self):
        if self.owned:self.worker.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
