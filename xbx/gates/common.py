"""What the gates share: how a result is printed and how units are compiled in bulk."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from .. import coff, match, units
from ..toolchain import ToolError


class Report:
    """Collects problems. Steward quotes the PROBLEM lines in its review."""

    def __init__(self, gate, project):
        self.gate, self.project, self.problems = gate, project, 0

    def problem(self, where, why):
        self.problems += 1
        where = self.project.relative(where) if where and os.path.isabs(str(where)) else where
        print(f'PROBLEM {where}: {why}' if where else f'PROBLEM {why}', flush=True)

    def finish(self, summary=''):
        if self.problems:
            print(f'gate {self.gate}: FAILED, {self.problems} problem(s)' + (f' ({summary})' if summary else ''))
            return 1
        print(f'gate {self.gate}: OK' + (f' ({summary})' if summary else ''))
        return 0


def jobs():
    return max(1, int(os.environ.get('XBX_JOBS', '4')))


def tier_units(project, tier):
    """[(address, Unit)] of a tier, in address order."""
    return [(addr, units.load(path)) for addr, path in project.units(tier)]


def verdicts(project, compiler, loaded, strict, progress=True):
    """{address: Verdict} for loaded units: compiled a few at a time, compared one by one."""
    def build(item):
        addr, unit = item
        try:
            return addr, match.compile_unit(project, compiler, unit), None
        except ToolError as e:
            return addr, None, str(e)

    matcher = match.Matcher(project, strict)
    out = {}
    with ThreadPoolExecutor(jobs()) as pool:
        for n, (addr, result, error) in enumerate(pool.map(build, loaded), 1):
            unit = dict(loaded)[addr] if len(loaded) < 64 else None
            unit = unit or next(u for a, u in loaded if a == addr)
            if error is not None:
                v = match.Verdict(addr, unit.path, problems=['does not compile'], output=error)
            else:
                try:
                    v = matcher.compare(coff.Coff(result[0], unit.path), unit)
                    v.output = result[1]
                except coff.CoffError as e:
                    v = match.Verdict(addr, unit.path, problems=[str(e)])
            out[addr] = v
            if progress and n % 200 == 0:
                print(f'  {n} of {len(loaded)} units', file=sys.stderr, flush=True)
    return out
