"""Join PixelLang-generated debug frames and source maps without a host frontend."""
from copy import deepcopy
from .compiler_client import CompiledArtifact
from .model import PixelError


def prepare_debug(bytecode, debug, source):
    code=deepcopy(bytecode)
    source=deepcopy(source)
    documents={'main':source,**source.get('bundle',{}).get('modules',{})}
    groups={}
    for module,document in documents.items():
        metadata=document.get('metadata',{})
        spans={}
        for key,span in metadata.get('source_map',{}).items():
            bounds=(span['start']['offset'],span['end']['offset'])
            spans.setdefault(bounds,[]).append([int(v) for v in key.split(',')])
        groups[module]=spans
    if set(code['functions'])!=set(debug['functions']):
        raise PixelError('debug','Debug frame coverage differs from bytecode')
    for key,function in code['functions'].items():
        info=debug['functions'][key]
        module=info['module']
        if module not in documents or documents[module].get('metadata',{}).get('filename')!=info['source']:
            raise PixelError('debug','Debug module does not match source document')
        if len(info['names'])>function['locals']:
            raise PixelError('debug','Debug slot names exceed function frame')
        spans=groups[module]
        for instruction in function['instructions']:
            origin=instruction.get('span')
            if not origin:continue
            start,end=origin['start']['offset'],origin['end']['offset']
            choices=[bounds for bounds in spans if bounds[0]<=start and end<=bounds[1]]
            if not choices:
                raise PixelError('debug','Instruction has no generated pixel source mapping')
            bounds=min(choices,key=lambda pair:(pair[1]-pair[0],pair[0]))
            points=spans[bounds]
            instruction['span']=dict(source=info['source'],points=points,
                min=[min(p[i] for p in points) for i in (0,1)],
                max=[max(p[i] for p in points) for i in (0,1)],text=origin)
    return CompiledArtifact(code,source,deepcopy(debug))
