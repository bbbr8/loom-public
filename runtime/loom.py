#!/usr/bin/env python3
"""Loom language entry point. Surd module names remain compatible."""
from surd import *
from surd import main as runtime_main, SurdError
LoomError = SurdError


def main(argv):
    """Shared command, with explicit dialects and legacy timeline invocation."""
    from pathlib import Path
    if argv and argv[0] in ("-e", "--execute"):
        return runtime_main(["timeline"] + list(argv))
    # Old `loom FILE` calls remain valid. `run FILE` chooses by its first statement,
    # never by .loom extension (both dialects legitimately use that extension).
    index = 1 if argv and argv[0] == "run" else 0
    if len(argv) > index and Path(argv[index]).is_file():
        path = Path(argv[index])
        if path.stat().st_size <= 1000000:
            first = next((line.strip().split()[0] for line in path.read_text().splitlines()
                          if line.strip() and not line.lstrip().startswith("#")), "")
            from surd_timeline import COMMANDS
            if first in COMMANDS:
                return runtime_main(["timeline"] + list(argv[index:]))
        if index == 0:
            return runtime_main(["run"] + list(argv))
    return runtime_main(argv)


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv[1:]))
