"""The declarations the tree has already settled on.

    xbx names [ADDRESS...]     the decorated name each address is declared with, and by which unit

Units are compiled apart, so nothing but this rule keeps them linkable with
each other: one address, one declaration. The first unit to call a function
or touch a global fixes its decorated name (its class, calling convention and
parameter types); every later unit must declare it the same way, and the
match gate refuses a unit that does not. `xbx match` checks a unit against
this index and prints the name to use.

The index is built from the matched units' own objects and kept in the build
directory; a unit is read again only when its file changes.
"""
import json
import os
import sys

from . import coff, match, units
from .toolchain import ToolError

INDEX_VERSION = 2


def _path(project):
    return os.path.join(project.out, 'names.json')


def index(project, compiler, skip=()):
    """{(address, alias): (decorated name, unit path relative to the project)} from the
    matched tier. The alias is 0 except for a folded function's other names (units.py).
    skip: unit paths to leave out (the ones being checked against the index)."""
    path = _path(project)
    stored = {}
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                stored = json.load(f)
        except ValueError:
            stored = {}
    if stored.get('version') != INDEX_VERSION or stored.get('flags') != project.cflags:
        stored = {'version': INDEX_VERSION, 'flags': project.cflags, 'units': {}}
    matcher = match.Matcher(project)
    shared = project.shared_symbols
    current, changed = {}, False
    for addr, unit_path in project.units('matched'):
        rel = project.relative(unit_path)
        stat = os.stat(unit_path)
        stamp = [stat.st_mtime_ns, stat.st_size]
        entry = stored['units'].get(rel)
        if entry is None or entry['stamp'] != stamp:
            unit = units.load(unit_path)
            try:
                v = match.check(project, compiler, unit, matcher=matcher)
            except (ToolError, coff.CoffError):
                continue
            entry = {'stamp': stamp, 'defines': v.defines if v.ours else '',
                     'links': {n: a for n, a in v.links.items() if n not in shared and n not in units.ABSOLUTE}}
            changed = True
        current[rel] = entry
    if changed or set(current) != set(stored['units']):
        stored['units'] = current
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(stored, f)
        os.replace(path + '.tmp', path)
    skip = {project.relative(os.path.abspath(p)) for p in skip}
    out = {}
    for rel, entry in sorted(current.items()):
        if rel in skip:
            continue
        addr = units.unit_address(rel)
        if entry['defines']:
            out[(addr, 0)] = (entry['defines'], rel)     # the defining unit's name wins the listing
    for rel, entry in sorted(current.items()):
        if rel in skip:
            continue
        for name, addr in entry['links'].items():
            out.setdefault(units.slot(name, addr), (name, rel))
    return out


def conflicts(known, v, project):
    """Messages for every address a verdict's unit declares differently from the tree."""
    out = []
    shared = project.shared_symbols
    for name, addr in list(v.links.items()) + ([(v.defines, v.addr)] if v.defines else []):
        if name in shared or name in units.ABSOLUTE:
            continue
        seen = known.get(units.slot(name, addr))
        if seen and seen[0] != name:
            out.append(f'{addr:#x} is declared as {name} here and as {seen[0]} in {seen[1]}: '
                       'one address, one declaration (use the same class, calling convention and types)')
    return out


def main(argv=None):
    import argparse
    from . import project as project_module, toolchain
    ap = argparse.ArgumentParser(prog='xbx names', description=__doc__.splitlines()[0])
    ap.add_argument('addresses', nargs='*')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
        known = index(project, toolchain.for_project(project))
    except (project_module.ProjectError, ToolError) as e:
        sys.exit(f'error: {e}')
    wanted = [int(a, 16) for a in args.addresses] or sorted({addr for addr, _ in known})
    for addr in wanted:
        slots = sorted(k for k in known if k[0] == addr)
        for key in slots:
            print(f'{addr:08X}  {known[key][0]}  ({known[key][1]})')
        if not slots:
            print(f'{addr:08X}  not declared by any matched unit yet: yours will be the first')
        if addr in project.folded:
            print(f'{addr:08X}  folded: other source functions here are named func_{addr:08X}_1, _2, ...')


if __name__ == '__main__':
    main()
