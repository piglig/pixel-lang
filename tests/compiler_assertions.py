"""Shared compiler assertions; this module deliberately defines no test cases.

Ordinary integration tests exercise the installed native bundle. Generated
compiler acceptance replaces setUpClass with its checksum-bound candidate.
"""
from pixellang.compiler_service import CompilerService


class CompilerAssertions:
    @classmethod
    def setUpClass(cls):
        cls.compiler = CompilerService.installed()

    def compile(self, source):
        response = self.compiler.request(dict(version=1, operation='compile',
                                             files={'main.pxl': source}))
        self.assertIn(response['status'], ('ok', 'diagnostics'), response)
        return {**(response['result'] or {}), 'diagnostics': response['diagnostics']}
