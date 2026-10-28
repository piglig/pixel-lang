"""Shared workload identity and power-state capture for performance probes."""
import hashlib
import json
import subprocess
import sys


def digest(value):
    def encode(v):
        if isinstance(v,bytes):return {'binarySha256':hashlib.sha256(v).hexdigest()}
        raise TypeError(type(v).__name__)
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=encode).encode()).hexdigest()


def power():
    if sys.platform!='darwin':return None
    return {name:subprocess.run(['pmset','-g',name],capture_output=True,text=True,check=True).stdout
            for name in ('batt','custom')}
