"""match: every unit in the matched tier is a proven match, and the units agree
with each other.

Each unit is compiled with the pinned compiler and flags and compared with the
retail executable under the strict rules of `xbx match --strict`. Then, across
all of them:
  - one name means one address
  - one address is declared one way (except a folded function, which is
    several source functions with names of their own, units.py): a function or variable that two units
    refer to must have the same decorated name in both (same class, calling
    convention, parameter types), and that is the name its own unit defines.
    The units can then be linked against each other; the judge's message
    names the declaration already in the tree
Units in the non-matching tier must compile, define their function, not match
(a unit that matches belongs in the matched tier), and carry the similarity
score the tools compute for them.
"""
from .. import fuzzy, toolchain, units
from .common import Report, tier_units, verdicts


def nonmatching_score(project, v):
    low = project.image.base
    return min(fuzzy.score(v.ours, v.mask, v.retail[:project.rows[v.addr]['_size']], v.addr, low), 99.9)


def run(project):
    report = Report('match', project)
    compiler = toolchain.for_project(project)
    loaded = tier_units(project, 'matched')
    results = verdicts(project, compiler, loaded, strict=True)
    shared = project.shared_symbols
    by_name, by_addr = {}, {}
    matched = 0
    for addr, unit in loaded:
        v = results[addr]
        if not v.ok:
            for why in v.problems:
                report.problem(unit.path, why)
            for line in (v.output.strip().splitlines()[:12] if not v.ours else []):
                print('    ' + line)
            continue
        matched += 1
        for name, target in list(v.links.items()) + [(v.defines, v.addr)]:
            if name in units.ABSOLUTE or name in shared:
                continue
            seen = by_name.setdefault(name, (target, unit.path))
            if seen[0] != target:
                report.problem(unit.path, f'{name} is {target:#x} here and {seen[0]:#x} in '
                                          f'{project.relative(seen[1])}')
            if target in project.folded:
                continue
            seen = by_addr.setdefault(units.slot(name, target), (name, unit.path))
            if seen[0] != name:
                report.problem(unit.path, f'{target:#x} is declared as {name} here and as {seen[0]} in '
                                          f'{project.relative(seen[1])}: one address, one declaration '
                                          '(use the same class, calling convention and types)')
    # the covered file may only claim what a proven unit carries
    from .. import covers
    proven = covers.compute(project, compiler, {a: results[a] for a, _ in loaded})
    for addr, unit in sorted(project.covered.items()):
        if addr not in proven:
            report.problem(project.relative(project.covered_csv),
                           f'{addr:#x} is listed as carried by {unit}, but no proven unit carries it '
                           '(a maintainer refreshes the file with `xbx covers --write`)')
    loose = tier_units(project, 'nonmatching')
    scored = verdicts(project, compiler, loose, strict=False)
    for addr, unit in loose:
        v = scored[addr]
        if not v.ours:
            for why in v.problems:
                report.problem(unit.path, why)
            for line in v.output.strip().splitlines()[:12]:
                print('    ' + line)
            continue
        if v.ok:
            report.problem(unit.path, 'this unit matches: move it to the matched tier')
            continue
        score = nonmatching_score(project, v)
        if unit.fuzzy is None or abs(unit.fuzzy - score) > 0.05:
            report.problem(unit.path, f'/* fuzzy: {unit.fuzzy} */ but the tools compute {score}: '
                                      'run `xbx match --write-fuzzy` on it')
    return report.finish(f'{matched} of {len(loaded)} matched units proven, {len(loose)} non-matching units scored')
