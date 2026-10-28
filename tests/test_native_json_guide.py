"""The JSON reference handles success and failure through native Result values."""
from tests.native_compiler import native_service
import re
from pathlib import Path
import unittest
from unittest.mock import patch
from pixellang.vm import VM

ROOT=Path(__file__).resolve().parents[1]
class NativeJsonGuideTests(unittest.TestCase):
    def test_bilingual_json_reference_handles_success_and_failure(self):
        programs=[re.search(r'```pixellang\n(.*?)\n```',
                           (ROOT/'docs'/lang/'reference/language/json.md').read_text(),re.S)[1]
                  for lang in ('zh-CN','en')]
        self.assertEqual(programs[0],programs[1])
        compiler=native_service()
        with patch('pixellang.text.parse_text',side_effect=AssertionError('host frontend forbidden')):
            response=compiler.request(dict(version=1,operation='compile',incremental=False,files={'main.pxl':programs[0]}))
        self.assertEqual(response['status'],'ok',response)
        code=response['result']['bytecode']
        self.assertEqual(VM(code,input_text='{"title":"book","counts":[1,2]}').run(),['book'])
        self.assertEqual(VM(code,input_text='{"title":"book"}').run(),['json.missing_field','$.counts'])
if __name__=='__main__':unittest.main()
