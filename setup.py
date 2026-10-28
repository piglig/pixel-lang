"""Platform wheel metadata for the bundled native compiler executable."""
import json
from pathlib import Path
from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel

manifest=json.loads((Path(__file__).parent/'pixellang/artifacts/compiler-manifest.json').read_text())
native=manifest.get('native')
if not native or not (Path(__file__).parent/'pixellang/artifacts/compiler-native').is_file():
    raise RuntimeError('Stage a verified native compiler bundle before building a distribution')


class NativeDistribution(Distribution):
    def has_ext_modules(self):return True


class NativeWheel(bdist_wheel):
    def get_tag(self):
        return 'py3','none',native['wheelTag']


setup(distclass=NativeDistribution,cmdclass={'bdist_wheel':NativeWheel})
