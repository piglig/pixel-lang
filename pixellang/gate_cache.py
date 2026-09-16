"""Exact-input reuse of completed verification stages, with immutable source evidence."""
import hashlib
import json
from pathlib import Path
import shutil


def hashes(directory):
    directory=Path(directory)
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file() and not p.is_symlink()}


def command_key(argv, output):
    # Only normalize paths within the evidence directory; compiler/source paths outside
    # it remain part of command identity. The entire source manifest is also required.
    prefix=str(Path(output).resolve())
    normalized=[str(Path(arg).resolve()) if Path(arg).is_absolute() else arg for arg in argv]
    return [arg.replace(prefix+'/', '{output}/',1) if arg.startswith(prefix+'/') else arg for arg in normalized]


class StageReuse:
    def __init__(self, source, manifest):
        self.source=Path(source).resolve()
        previous=json.loads((self.source/'manifest.json').read_text())
        if previous!=manifest:
            raise ValueError('Resume inputs, gate selection or execution environment differ')
        self.summary=json.loads((self.source/'summary.json').read_text())
        if self.summary.get('inputsUnchanged') is False:
            raise ValueError('Cannot resume evidence with changed inputs')

    def restore(self, name, argv, output):
        output=Path(output).resolve()
        key=command_key(argv,output)
        entries=[e for e in self.summary.get('commands',[]) if e.get('name')==name]
        if not entries:return None
        entry=entries[-1]
        if entry.get('exitCode')!=0 or not entry.get('completed') or entry.get('commandKey')!=key:
            return None
        stage_name=name
        artifacts=entry.get('artifacts')
        if not isinstance(artifacts,dict) or not artifacts:
            return None
        # Validate every artifact before copying anything. Fail closed on corruption;
        # preserve the prior directory so its successful receipt is never overwritten.
        for name,digest in artifacts.items():
            path=self.source/name
            if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(self.source):
                raise ValueError('Unsafe resume artifact path')
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('Resume artifact missing or corrupt: '+name)
            if (output/name).exists():raise ValueError('Resume artifact would overwrite current evidence: '+name)
        for name in artifacts:
            target=output/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(self.source/name,target)
            if hashlib.sha256(target.read_bytes()).hexdigest()!=artifacts[name]:
                raise ValueError('Resume artifact changed while copying: '+name)
        return dict(entry,reusedFrom=str(self.source),originalSeconds=entry.get('seconds'),seconds=0,
                    argv=argv,log=str(output/(stage_name+'.log')))
