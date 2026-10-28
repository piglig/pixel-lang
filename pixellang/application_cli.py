"""Run and replay native flow applications using an explicit logical clock."""
import argparse
from contextlib import closing
import sqlite3
import json
from pathlib import Path

from .application_session import ApplicationSession
from .compiler_client import CompilerClient
from .model import PixelError


def main(argv=None):
    parser = argparse.ArgumentParser(prog='pixel application')
    parser.add_argument('source', type=Path, help='Native .pxl entry or executable PNG')
    parser.add_argument('--config', type=Path, help='Host JSON configuration: instance, tick, intervalNanoseconds')
    parser.add_argument('--steps', type=int, default=1, help='Number of event transitions (including queued inputs)')
    parser.add_argument('--events', type=Path, help='JSON array of {tick, input: {variant, fields}}')
    parser.add_argument('--replay', type=Path, help='Restore a recording by executing its inputs')
    parser.add_argument('--record', type=Path, help='Save a replayable recording')
    parser.add_argument('--seek', type=int, help='Inspect a recorded event boundary after running')
    parser.add_argument('--history', action='store_true', help='Retain bounded event history for inspection')
    parser.add_argument('--journal', type=Path, help='Create or resume a durable application journal')
    args = parser.parse_args(argv)
    try:
        history = bool(args.history or args.record or args.replay or (args.seek is not None and not args.journal))
        if args.journal and history:
            raise PixelError('event', '--journal cannot be combined with in-memory history, record or replay')
        if args.steps < 0:
            raise PixelError('event', 'Steps must be nonnegative')
        if history and args.steps > 4096:
            raise PixelError('event', 'Recorded sessions support at most 4096 steps per command')
        configuration = json.loads(args.config.read_text()) if args.config else None
        if args.replay and configuration is None:
            configuration = json.loads(args.replay.read_text())['configuration']
        with CompilerClient() as compiler:
            with closing(ApplicationSession(compiler, args.source, history=history, journal_path=args.journal,
                                            configuration=configuration)) as session:
                if args.replay:
                    session.request('application-restore', dict(recordingText=args.replay.read_text()))
                    session.runtime.seek(len(session.runtime.records))
                if args.events:
                    for event in json.loads(args.events.read_text()):
                        session.runtime.enqueue(event['input'], tick=event.get('tick'))
                for _ in range(args.steps): session.runtime.step()
                if args.seek is not None: session.request('application-seek', dict(index=args.seek))
                if args.record:
                    args.record.write_text(json.dumps(session.runtime.recording(), ensure_ascii=False, indent=2) + '\n')
                print(json.dumps(session.status(), ensure_ascii=False, indent=2))
        return 0
    except (PixelError, ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps(dict(error=str(error)), ensure_ascii=False))
        return 1
