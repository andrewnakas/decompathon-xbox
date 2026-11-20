"""The functions that matched units carry along.

    xbx covers           list them: the function-table rows a matched unit's object places and proves
                         besides its own function (deleting and implicit destructors, adjustor and vcall
                         thunks, special members the compiler emitted as COMDATs)
    xbx covers --write   write them to the project's covered file (config/covered.csv)

They have no unit file of their own, so the progress report, which runs without the compiler, cannot see
them. The covered file lets it count them. The match gate refuses a covered file that lists a row no
proven unit covers, so it can only claim what the gates prove; it may lag behind (a maintainer refreshes
it with --write), never run ahead.
"""
import argparse
import csv
import os
import sys

from . import project as project_module, toolchain
from .gates.common import tier_units, verdicts
from .toolchain import ToolError

HEADER = ['addr', 'unit']


def compute(project, compiler, results=None):
    """{address: unit path relative to the project} for every func row a proven unit carries."""
    if results is None:
        loaded = tier_units(project, 'matched')
        results = verdicts(project, compiler, loaded, strict=True)
        paths = dict(loaded)
    else:
        paths = {addr: v.path for addr, v in results.items()}
    out = {}
    for addr, v in sorted(results.items()):
        if not v.ok:
            continue
        for a in v.covers:
            row = project.rows.get(a)
            if a != addr and row is not None and row.get('kind', 'func') == 'func':
                unit = paths[addr] if isinstance(paths[addr], str) else paths[addr].path
                out.setdefault(a, project.relative(unit))
    return out


def write(project, covered):
    path = project.covered_csv
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(HEADER)
        for addr in sorted(covered):
            w.writerow([f'{addr:08x}', covered[addr]])


def main(argv=None):
    ap = argparse.ArgumentParser(prog='xbx covers', description=__doc__.splitlines()[0])
    ap.add_argument('--write', action='store_true', help='write the covered file')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
        covered = compute(project, toolchain.for_project(project))
    except (project_module.ProjectError, ToolError) as e:
        sys.exit(f'error: {e}')
    if args.write:
        write(project, covered)
        print(f'{project.relative(project.covered_csv)}: {len(covered)} functions carried by matched units')
    else:
        for addr, unit in sorted(covered.items()):
            print(f'{addr:08X}  {unit}')
        print(f'{len(covered)} functions carried by matched units')


if __name__ == '__main__':
    main()
