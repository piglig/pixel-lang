"""Validate an installed, platform-specific native compiler bundle."""
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

from .bootstrap import normalized


def validate_source_map(mapping, code):
    if mapping.get('format')!='pixellang-native-source-map/2':
        raise ValueError('Native compiler requires a compact source map; rebuild the bundle')
    rows=mapping.get('functions')
    functions=code['functions']
    if (not isinstance(rows,list) or len(rows)!=len(functions)
        or any(not isinstance(row,dict) or not isinstance(row.get('name'),str) for row in rows)
        or {row['name'] for row in rows}!=set(functions)
        or any(type(row.get('instructionCount')) is not int or row['instructionCount']!=len(functions[row['name']]['instructions']) for row in rows)):
        raise ValueError('Native source map function identities do not match bytecode')
    session=mapping.get('session')
    if session is not None:
        if (not isinstance(session,dict) or set(session)!={'initialize','advance'}
            or any(not isinstance(name,str) or name not in functions for name in session.values())):
            raise ValueError('Invalid native session function identities')
        initial,advance=(functions[session[key]] for key in ('initialize','advance'))
        if (initial['params'] or initial.get('captures') or advance.get('captures')
            or initial['result']=='unit' or initial['result'].startswith('inout<')
            or advance['params']!=[initial['result']] or advance['result']!=initial['result']):
            raise ValueError('Invalid native session state contract')


def load_bundle(folder):
    folder=Path(folder)
    manifest=json.loads((folder/'compiler-manifest.json').read_text())
    native=manifest.get('native')
    if manifest.get('protocolVersion')!=1 or not isinstance(native,dict):
        raise ValueError('A native compiler bundle is required; rebuild the development distribution')
    if native.get('platform')!=sys.platform or native.get('machine')!=platform.machine():
        raise ValueError('Native compiler bundle does not support this operating system and architecture')
    if native.get('file')!='compiler-native':
        raise ValueError('Invalid native compiler executable name')
    data=(folder/'compiler.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=manifest.get('sha256'):
        raise ValueError('Installed compiler bytecode checksum mismatch')
    code=json.loads(data)
    executable=folder/'compiler-native'
    if not executable.is_file() or not os.access(executable,os.X_OK):
        raise ValueError('Native compiler executable is missing or not executable')
    digest=hashlib.sha256(executable.read_bytes()).hexdigest()
    mapping=json.loads((folder/'compiler-native.map.json').read_text())
    validate_source_map(mapping,code)
    if digest!=native.get('sha256') or mapping.get('executableSha256')!=digest:
        raise ValueError('Native compiler executable checksum mismatch')
    if mapping.get('bytecodeSha256')!=hashlib.sha256(normalized(code).encode('utf-8',errors='surrogatepass')).hexdigest():
        raise ValueError('Native compiler executable belongs to different bytecode')
    return code,executable,manifest
