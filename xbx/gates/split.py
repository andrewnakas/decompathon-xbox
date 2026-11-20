"""split: the function table is exactly what the game's executable gives, and it
accounts for every byte of the game's code.

  - building the table again from the XBE gives the committed file, byte for byte
  - in every section that holds the game's own code: rows are in order and do
    not overlap; between two rows there is nothing but padding (at most 15
    bytes of it); every row's instructions decode from its first byte to its
    switch tables; no jump leaves a row for the middle of another; every
    direct call lands on the first byte of a row
  - every compiler-made exception-handling row names the functions it belongs to
Sections that hold a library (xbx.toml [owners.sections]) are listed in the
table but are not held to the layout rules: their code is not the project's
subject and hand-written assembly does not follow them.
"""
import bisect
import os

from capstone import CS_ARCH_X86, CS_MODE_32, Cs
from capstone import x86 as cs_x86

from .. import inventory
from ..code import table_start
from ..pad import MAX_GAP, is_padding
from .common import Report


def run(project, rebuild=True):
    report = Report('split', project)
    image = project.image
    table = project.functions_csv
    if rebuild:
        rows, _ = inventory.build(project)
        current = open(table, encoding='utf-8').read() if os.path.exists(table) else ''
        if inventory.render(rows) != current:
            report.problem(table, 'is not what the XBE gives: run `xbx inventory` and review the difference')
    rows = project.rows
    starts = sorted(rows)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    strict = [name for name in project.code_sections if name not in project.section_owners]

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        if i < 0:
            return None
        r = rows[starts[i]]
        return r if va < r['_addr'] + r['_size'] else None

    for name in project.code_sections:
        section = image.section(name)
        mine = [rows[a] for a in starts if rows[a]['section'] == name]
        for r in mine:
            if not section.va <= r['_addr'] and r['_addr'] + r['_size'] <= section.va + section.vsize:
                report.problem(table, f"row {r['addr']} is not inside section {name}")
        if name not in strict:
            continue
        pos = section.va
        for r in mine + [None]:
            start = r['_addr'] if r else section.va + section.vsize
            if start < pos:
                report.problem(table, f"row {r['addr']} overlaps the row before it")
            elif start > pos:
                gap = image.read(pos, start - pos)
                if len(gap) > MAX_GAP or not is_padding(gap):
                    report.problem(table, f'{len(gap)} bytes at {pos:08x} belong to no row and are not padding '
                                          f'({gap[:8].hex()})')
            if r:
                pos = max(pos, r['_addr'] + r['_size'])
        for r in mine:
            addr, size = r['_addr'], r['_size']
            data = image.read(addr, size)
            end, reached = table_start(md, data, addr)
            if reached != end:
                report.problem(table, f"row {r['addr']}: its bytes stop decoding as instructions at +{reached:#x}")
                continue
            for ins in md.disasm(data[:end], addr):
                if cs_x86.X86_GRP_BRANCH_RELATIVE not in ins.groups and ins.mnemonic != 'call':
                    continue
                op = ins.operands[0] if ins.operands else None
                if op is None or op.type != cs_x86.X86_OP_IMM:
                    continue
                target = op.imm & 0xFFFFFFFF
                if addr <= target < addr + size:
                    continue
                hit = image.section_at(target)
                if hit is None or hit.name not in project.code_sections:
                    report.problem(table, f"row {r['addr']}: `{ins.mnemonic} {ins.op_str}` at {ins.address:08x} "
                                          'leaves the code sections')
                elif target not in rows:
                    inside = owner(target)
                    where = f"the middle of row {inside['addr']}" if inside else 'no row'
                    report.problem(table, f"row {r['addr']}: `{ins.mnemonic} {ins.op_str}` at {ins.address:08x} "
                                          f'lands in {where}')
    for r in rows.values():
        if r.get('kind') == 'eh':
            parents = r.get('parent', '').split()
            if not parents:
                report.problem(table, f"exception-handling row {r['addr']} names no function it belongs to")
            for p in parents:
                parent = rows.get(int(p, 16))
                if parent is None or parent.get('kind') != 'func':
                    report.problem(table, f"exception-handling row {r['addr']}: parent {p} is not a function row")
    return report.finish(f'{len(rows)} rows, {len(strict)} section(s) held to the layout rules')
