"""Reference VM execution for bounded image transport differential tests."""
from contextlib import ExitStack
import json
import time
from unittest.mock import patch
from pixellang.bootstrap_image import HOST_STAGES, PROFILE
from pixellang.fileaccess import FileAccess
from pixellang.vm import VM
from pixellang.compiler_protocol import check_native_output


class Execution:
    """Reuse validated code, restoring an empty trusted VM checkpoint per job."""
    def __init__(self, code):
        self.vm = VM(code, **PROFILE)
        self.initial = self.vm.checkpoint()

    def reset(self, request, root):
        text = json.dumps(request, ensure_ascii=False)
        if len(text)>1000000:
            raise ValueError('Request exceeds bounded VM input; use chunk files')
        self.vm.restore_checkpoint(self.initial)
        self.vm.input_text = text
        self.vm.file_access = FileAccess(read_root=root,budget=64000000)
        return self.vm


def execute(code, request, root, deadline):
    if time.monotonic() > deadline:
        raise TimeoutError("Image workspace deadline exceeded")
    vm = code.reset(request,root) if isinstance(code,Execution) else VM(
        code, **PROFILE, input_text=json.dumps(request, ensure_ascii=False),
        file_access=FileAccess(read_root=root, budget=64000000))
    start = time.monotonic()
    with ExitStack() as stack:
        for target in HOST_STAGES:
            stack.enter_context(patch(target, side_effect=AssertionError('Host frontend/codec used')))
        while not vm.halted:
            vm.step(snapshot=False)
            if vm.steps % 100000 == 0 and time.monotonic() > deadline:
                raise TimeoutError('Image workspace VM deadline exceeded')
    if time.monotonic() > deadline:
        raise TimeoutError("Image workspace deadline exceeded")
    check_native_output(vm.output)
    return vm.output, dict(seconds=time.monotonic()-start, steps=vm.steps)
