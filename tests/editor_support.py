"""Native Studio analysis helpers shared by editor behavior tests."""
import unittest
from pixellang.workstation import Workstation


class NativeEditorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workstation = Workstation()
        cls.addClassCleanup(cls.workstation.close)

    def index(self, files):
        return self.workstation.symbol_index(files, 'main.pxl')

    def complete(self, files, source, offset):
        return self.workstation.request('completion', dict(files=files, source=source, offset=offset))

    def compile(self, files):
        return self.workstation.compiler.request('compile', files=files)['result']['bytecode']

    def format(self, text, source='main.pxl'):
        return self.workstation.compiler.request('format', files={source: text}, entry=source)['result']['text']
