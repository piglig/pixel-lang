"""Read explicit failure values emitted by native compiler entry points."""
from .model import PixelError, Span


def check_native_output(events):
    for event in events:
        if not isinstance(event, dict) or event.get('kind') != 'compiler-error':
            continue
        error = event.get('error')
        fields = ('kind', 'code', 'message', 'path', 'operation', 'expected', 'actual')
        if not isinstance(event.get('source'), str) or not isinstance(error, dict) or any(
            not isinstance(error.get(name), str) for name in fields
        ):
            raise PixelError('service', 'Malformed native compiler failure', code='service.invalid-response')
        raise PixelError('compile', error['message'], Span(event['source'], ()), code=error['code'],
                         path=error['path'], operation=error['operation'],
                         expected=error['expected'], actual=error['actual'])
