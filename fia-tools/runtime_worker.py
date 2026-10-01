"""Internal entry point. Launched with a run-specific environment by the supervisor."""

import os
import sys
from pathlib import Path


def main():
    # Set before importing Excel, StarDist, ImageJ or any numbered FIA script.
    import tempfile
    temp = Path(os.environ['TMPDIR'])
    if tempfile.gettempdir() != str(temp):
        raise RuntimeError('Python did not accept the FIA temporary directory')
    command = sys.argv[1]
    from run_resources import (
        COMMANDS,
        identity,
        observation_supported,
        utc_now,
        write_json,
    )
    if command not in COMMANDS:
        raise ValueError(f'Unknown FIA command: {command}')
    write_json(Path(os.environ['FIA_RUN_DIR']) / 'worker.json',
               {'process': identity() if observation_supported() else {'pid': os.getpid(), 'created': None},
                'started_utc': utc_now()})
    import __init__ as entrypoints
    sys.argv = [command, *sys.argv[2:]]
    return getattr(entrypoints, command).__wrapped__()


def launch_direct(command):
    import __init__ as entrypoints
    return getattr(entrypoints, command)()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print('Analysis canceled.', file=sys.stderr)
        raise SystemExit(130)
