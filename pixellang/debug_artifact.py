"""Text debug provenance and optional PixelLang-generated spatial projections."""
from copy import deepcopy
from .compiler_client import CompiledArtifact
from .model import PixelError


def prepare_debug(bytecode, debug, source=None, *, take_ownership=False):
    # CompilerClient owns its freshly decoded response. Other callers retain
    # isolation by default; only the explicit ownership path mutates its input.
    code=bytecode if take_ownership else deepcopy(bytecode)
    if set(code['functions'])!=set(debug['functions']):
        raise PixelError('debug','Debug frame coverage differs from bytecode')
    spans={}
    for key,function in code['functions'].items():
        info=debug['functions'][key]
        if len(info['names'])>function['locals']:
            raise PixelError('debug','Debug slot names exceed function frame')
        for instruction in function['instructions']:
            origin=instruction.get('span')
            if not origin:continue
            if origin['source']!=info['source']:
                raise PixelError('debug','Debug frame does not match instruction source')
            identity=id(origin)
            if identity not in spans:
                spans[identity]=dict(source=info['source'],points=[],min=[0,0],max=[0,0],text=origin)
            instruction['span']=spans[identity]
    artifact=CompiledArtifact(code,deepcopy(source),debug if take_ownership else deepcopy(debug))
    if source is not None:projection_points(artifact,source)
    return artifact


def projection_points(artifact, source):
    """Validate and join a projection without modifying executable provenance."""
    documents={'main':source,**source.get('bundle',{}).get('modules',{})}
    groups={}
    for module,document in documents.items():
        spans={}
        for key,span in document.get('metadata',{}).get('source_map',{}).items():
            bounds=(span['start']['offset'],span['end']['offset'])
            spans.setdefault(bounds,[]).append([int(v) for v in key.split(',')])
        groups[module]=spans
    result={}
    for key,function in artifact.bytecode['functions'].items():
        info=artifact.debug['functions'][key]
        module=info['module']
        if module not in documents or documents[module].get('metadata',{}).get('filename')!=info['source']:
            raise PixelError('debug','Debug module does not match source document')
        spans=groups[module]
        for pc,instruction in enumerate(function['instructions']):
            origin=instruction.get('span',{}).get('text')
            if not origin:continue
            start,end=origin['start']['offset'],origin['end']['offset']
            choices=[bounds for bounds in spans if bounds[0]<=start and end<=bounds[1]]
            if not choices:
                raise PixelError('debug','Instruction has no generated pixel source mapping')
            bounds=min(choices,key=lambda pair:(pair[1]-pair[0],pair[0]))
            result[key,pc]=spans[bounds]
    return result
