"""Small bytecode programs with explicit function effect contracts."""
from tests.test_native_flow_runtime import delay_program


def op(name, arg=None):
    return dict(op=name, arg=arg)


def program(body, typ='int[]', result='int[]'):
    code=delay_program()
    code['flows']={}
    code['effectFunctions']=['main']
    code['functions']={
        'main':dict(params=[],locals=1,result='unit',instructions=[op('PUSH',0),op('RETURN')]),
        'subject':dict(params=[typ],locals=2,result=result,instructions=body),
    }
    return code
