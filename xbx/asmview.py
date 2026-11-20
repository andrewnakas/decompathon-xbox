"""Prints a function's retail disassembly, with what each address refers to.

    xbx asm <address> [...]          one or more functions
    xbx asm --small N [--owner game] the N smallest functions with no unit yet
    xbx asm --bytes <address>        also print the instruction bytes

Call and jump targets that are function starts are shown as func_XXXXXXXX;
addresses in data sections as data_XXXXXXXX with the section's name; kernel
imports by their slot. Switch tables are printed as tables.
"""
import argparse
import struct
import sys

from capstone import CS_ARCH_X86, CS_MODE_32, Cs
from capstone import x86 as cs_x86

from .code import table_start


def annotate(project, value):
    """What an address is, as a short tag ('' when it is nothing known)."""
    image = project.image
    if value in project.rows:
        return f'func_{value:08X}'
    if image.thunk_va and image.thunk_va <= value < image.thunk_va + 4 * len(image.thunks):
        return f'kernel import #{image.thunks[(value - image.thunk_va) // 4]}'
    s = image.section_at(value)
    if s is None:
        return ''
    if s.name in project.code_sections:
        return f'code {value:#x}'
    return f'data_{value:08X} ({s.name})'


def string_at(project, value, limit=48):
    """A printable C string at an address in file-backed data, else ''."""
    image = project.image
    s = image.section_at(value)
    if s is None or s.name in project.code_sections or value >= s.va + s.rsize:
        return ''
    raw = image.read(value, limit + 1)
    end = raw.find(b'\0')
    if end < 4:
        return ''
    text = raw[:end]
    if all(32 <= c < 127 for c in text):
        return '"' + text.decode('ascii') + ('"' if end <= limit else '...')
    return ''


def listing(project, addr, show_bytes=False):
    row = project.rows.get(addr)
    if row is None:
        return [f'{addr:#010x}: not a function start']
    size = row['_size']
    data = project.image.read(addr, size)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    lines = [f'; func_{addr:08X}  {size} bytes  {row["owner"]} {row["kind"]}'
             + (f'  reads {row["conv"]} at entry' if row['conv'] else '')]
    end, _ = table_start(md, data, addr)
    for ins in md.disasm(data[:end], addr):
        notes = []
        relative = cs_x86.X86_GRP_BRANCH_RELATIVE in ins.groups
        for op in ins.operands:
            value = None
            if op.type == cs_x86.X86_OP_IMM:
                value = op.imm & 0xFFFFFFFF
            elif op.type == cs_x86.X86_OP_MEM and op.mem.disp:
                value = op.mem.disp & 0xFFFFFFFF
            if value is None or value < project.image.base:
                continue
            if relative and addr <= value < addr + size:
                continue
            tag = annotate(project, value)
            if tag:
                notes.append(tag + (' ' + string_at(project, value) if tag.startswith('data_') else ''))
        raw = f'{ins.bytes.hex():<22} ' if show_bytes else ''
        text = f'{ins.address:08x}  {raw}{ins.mnemonic:<7} {ins.op_str}'
        lines.append(text + (f'    ; {", ".join(n.strip() for n in notes)}' if notes else ''))
    for off in range(end, size - 3, 4):
        value, = struct.unpack_from('<I', data, off)
        if addr <= value < addr + size:
            lines.append(f'{addr + off:08x}  dd      {value:#x}')
        else:
            lines.append(f'{addr + off:08x}  db      ' + ' '.join(f'{b:02x}' for b in data[off:off + 4]))
    return lines


def main(argv=None):
    from . import project as project_module
    ap = argparse.ArgumentParser(prog='xbx asm', description=__doc__.splitlines()[0])
    ap.add_argument('addresses', nargs='*')
    ap.add_argument('--small', type=int, metavar='N')
    ap.add_argument('--owner', default='game')
    ap.add_argument('--bytes', action='store_true')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
        if args.small:
            done = {a for a, _ in project.units('matched')} | {a for a, _ in project.units('nonmatching')}
            rows = [r for r in project.rows.values()
                    if r['owner'] == args.owner and r['kind'] == 'func' and r['_addr'] not in done]
            addrs = [r['_addr'] for r in sorted(rows, key=lambda r: (r['_size'], r['_addr']))[:args.small]]
        else:
            addrs = [int(a, 16) for a in args.addresses]
        if not addrs:
            ap.error('no addresses given')
        for a in addrs:
            print('\n'.join(listing(project, a, args.bytes)))
            print()
    except project_module.ProjectError as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
