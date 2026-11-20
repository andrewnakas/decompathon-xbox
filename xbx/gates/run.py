"""Runs one of the project's gates: the checks a change must pass to be merged.

    xbx gate toolcheck [--record]   the judge's compiler and game file are the pinned ones
    xbx gate scan                   unit files hold nothing a unit may not hold (needs no game file)
    xbx gate split                  the function table is what the game's executable gives
    xbx gate match                  every matched unit is a proven match, and the units agree
    xbx gate build                  the matched units, linked, are the game's bytes
    xbx gate all                    all five, in that order

A gate prints `PROBLEM <file>: <why>` for everything it refuses and ends with
`gate <name>: OK` or `gate <name>: FAILED`; the exit status says the same.
"""
import argparse
import sys

from .. import project as project_module
from ..toolchain import ToolError

GATES = ('toolcheck', 'scan', 'split', 'match', 'build')


def run_gate(name, project, args):
    if name == 'toolcheck':
        from . import toolcheck
        return toolcheck.run(project, record=args.record)
    if name == 'scan':
        from . import scan
        return scan.run(project)
    if name == 'split':
        from . import split
        return split.run(project, rebuild=not args.no_rebuild)
    if name == 'match':
        from . import matchgate
        return matchgate.run(project)
    from . import build
    return build.run(project)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='xbx gate', description=__doc__.splitlines()[0])
    ap.add_argument('gate', choices=GATES + ('all',))
    ap.add_argument('--record', action='store_true', help='toolcheck: record the compiler checksums and the canary')
    ap.add_argument('--no-rebuild', action='store_true',
                    help='split: check the committed table without building it again (quick, not the judge\'s check)')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
        status = 0
        for name in (GATES if args.gate == 'all' else (args.gate,)):
            status |= run_gate(name, project, args)
        return status
    except (project_module.ProjectError, ToolError) as e:
        print(f'PROBLEM {e}')
        print(f'gate {args.gate}: FAILED')
        return 1


if __name__ == '__main__':
    sys.exit(main())
