"""Omitted next retains native state without synthetic updates or wiring."""
from tests.native_compiler import native_service
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from pixellang.model import PixelError
from pixellang.vm import VM

SOURCE = '''flow Child(initial:int) step(delta:int)->int {
 state retained=[initial,initial+1]
 state changing=0
 next changing=changing+delta
 output retained[0]+changing
}
flow Parent() step(delta:int)->int {
 state label=100
 instance child=Child(7)
 output label+child.step(delta)
}
effect fn main(){
 let first=Parent()
 let second=Parent()
 print(first.step(2))
 print(first.step(3))
 print(second.step(10))
 print(first.step(0))
}'''


class NativeRetainedStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=native_service()
        cls.addClassCleanup(cls.compiler.close)

    def request(self,operation,**args):
        with patch('pixellang.text.parse_text',side_effect=AssertionError('host frontend forbidden')):
            result=self.compiler.request(dict(version=1,operation=operation,incremental=False,**args))
        self.assertEqual(result['status'],'ok',result)
        return result['result']

    def test_partial_updates_retain_state_across_instances_png_and_recovery(self):
        files={'main.pxl':SOURCE}
        code=self.request('compile',files=files)['bytecode']
        self.assertEqual(VM(code).run(),[107,109,107,112])
        graphs={g['name']:g for g in code['flows'].values()}
        self.assertEqual(graphs['Parent']['updates'],[])
        self.assertEqual(len(graphs['Child']['updates']),1)
        self.assertEqual(len(graphs['Child']['states']),2)
        image=self.request('export-png',files=files)['image']
        self.assertEqual(VM(self.request('compile-png',image=image)['bytecode']).run(),[107,109,107,112])
        recovered=self.request('recover-png',image=image)
        self.assertEqual(VM(self.request('compile',**recovered)['bytecode']).run(),[107,109,107,112])
        projection=self.request('native-graphs',**recovered)['projection']
        # PNG execution does not require original identifier metadata. Recovery
        # may assign new names; verify the retained/update structure instead.
        parent=next(g for g in projection['graphs'] if g['children'])
        child=next(g for g in projection['graphs'] if not g['children'])
        self.assertEqual(parent['updates'],[])
        self.assertEqual(len(child['states']),2)
        changing=next(state['name'] for state in child['states'] if state['type']=='int')
        self.assertEqual([u['state'] for u in child['updates']],[changing])

    def test_remove_next_through_native_graph_edit_preserves_state_and_comments(self):
        text='// 星 🛰️\r\n'+SOURCE.replace('next changing=changing+delta', 'next changing=changing+delta // keep').replace('\n','\r\n')
        before=self.request('native-graphs',files={'main.pxl':text})['projection']
        graph=next(g for g in before['graphs'] if g['name']=='Child')
        action=dict(kind='remove-next',node=graph['updates'][0]['node'])
        script="""const fs=require('node:fs');
const {planEdit,validateGraphEdit}=require('./vscode/src/native-graph-edits');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
let candidate=p.text;
for(const edit of planEdit(p.text,p.graph,p.action)) candidate=candidate.slice(0,edit.start)+edit.text+candidate.slice(edit.end);
if(p.after)validateGraphEdit(p.graph,p.after,p.action,p.text,candidate);
process.stdout.write(JSON.stringify(candidate));"""
        payload=dict(text=text,graph=graph,action=action)
        def edit():
            result=subprocess.run(['node','-e',script],input=json.dumps(payload),text=True,capture_output=True,cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        candidate=edit()
        self.assertIn('// 星 🛰️\r\n',candidate)
        self.assertIn('// keep\r\n',candidate)
        after=self.request('native-graphs',files={'main.pxl':candidate})['projection']
        self.assertEqual(after['diagnostics'],[])
        payload['after']=next(g for g in after['graphs'] if g['name']=='Child')
        self.assertEqual(payload['after']['updates'],[])
        self.assertEqual(edit(),candidate)
        self.assertEqual(VM(self.request('compile',files={'main.pxl':candidate})['bytecode']).run(),[107,107,107,107])

    def test_retained_values_and_staged_changes_replay_identically(self):
        vm=VM(self.request('compile',files={'main.pxl':SOURCE})['bytecode'])
        while not (vm.flow_runtime.transaction and vm.flow_runtime.transaction.staged):
            vm.step(snapshot=False)
        checkpoint=vm.checkpoint()
        self.assertEqual(vm.run(),[107,109,107,112])
        final=vm.checkpoint()
        vm.restore_checkpoint(checkpoint)
        self.assertEqual(vm.run(),[107,109,107,112])
        self.assertEqual(vm.checkpoint(),final)

    def test_parent_failure_preserves_retained_and_updated_child_state(self):
        source=SOURCE[:SOURCE.index('effect fn main')].replace('output label+child.step(delta)', 'let prior=child.step(delta)\n output (label+prior)/0')
        source+='effect fn main(){let instance=Parent();print(instance.step(2))}'
        vm=VM(self.request('compile',files={'main.pxl':source})['bytecode'])
        while not (vm.flow_runtime.instances and vm.flow_runtime.transaction is None):
            vm.step(snapshot=False)
        before=dict(vm.flow_runtime.instances[0].cells)
        with self.assertRaises(PixelError):vm.run()
        self.assertEqual(vm.flow_runtime.instances[0].cells,before)
        self.assertEqual(vm.flow_runtime.instances[0].steps,0)
        self.assertIsNone(vm.flow_runtime.transaction)


if __name__=='__main__':unittest.main()
