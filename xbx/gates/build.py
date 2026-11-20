"""build: the matched units, linked, are the game's bytes.

A second proof of the same claim as the match gate, made the other way round.
The match gate reads retail's bytes and works back to what each relocation
must mean. This gate goes forward, as a linker does: every block of every
matched unit's object is given its address, every relocation is applied from
the symbols' addresses, and the bytes that come out must equal the retail
executable over every range the units cover. A mistake in how the match gate
reads a relocation cannot hide here, because this gate never reads retail
before its own bytes are final.
"""
import struct

from .. import coff, toolchain, units
from .common import Report, tier_units, verdicts


class LinkError(Exception):
    pass


def link(project, obj, unit, placement):
    """[(address, bytes)] for every placed block of an object, relocations applied."""
    shared = project.shared_symbols

    def external(name):
        hit = units.placeholder(name)
        if hit and name in unit.syms and units.is_override(name, unit.syms[name]):
            return unit.syms[name]
        if hit:
            return hit[1]
        if name in units.ABSOLUTE:
            return units.ABSOLUTE[name]
        if name in shared:
            return shared[name]
        return unit.syms.get(name)

    def local(sym, addend):
        """(address of what a local symbol plus addend refers to)."""
        if obj._is_section_symbol(sym):
            offset = sym.value + addend
            block = obj.block_at(sym.section, offset)
            extra = 0
        else:
            offset, extra = sym.value, addend
            block = obj.block_at(sym.section, offset)
        if block is None or block.key not in placement:
            raise LinkError(f'{sym.name} has no address')
        return placement[block.key] + offset - block.start + extra

    out = []
    for block in obj.blocks():
        if block.key not in placement:
            if block.size and not block.section.comdat:     # an unreferenced COMDAT is dropped by the linker
                raise LinkError(f'{block.name} was never given an address')
            continue
        base = placement[block.key]
        data = bytearray(block.data)
        for r in block.relocations:
            k = r.offset - block.start
            addend = struct.unpack_from('<i', data, k)[0]
            sym = obj.symbol(r.symbol)
            if sym.section == 0 and sym.storage == coff.CLASS_WEAK and sym.default >= 0 and external(sym.name) is None:
                sym = obj.symbol(sym.default)
            if sym.section == 0 and obj.definition(sym.name) is not None:
                sym = obj.definition(sym.name)      # defined under another entry of the same name
            if sym.section == -1:
                value = sym.value + addend
            elif sym.section > 0:
                value = local(sym, addend)
            else:
                address = external(sym.name)
                if address is None:
                    raise LinkError(f'{sym.name} has no address')
                value = address + addend
            if r.type == coff.REL_REL32:
                value -= base + k + 4
            elif r.type != coff.REL_DIR32:
                raise LinkError(f'relocation type {r.type:#x} is not supported')
            struct.pack_into('<I', data, k, value & 0xFFFFFFFF)
        out.append((base, bytes(data)))
    return out


def run(project):
    report = Report('build', project)
    compiler = toolchain.for_project(project)
    image = project.image
    loaded = tier_units(project, 'matched')
    results = verdicts(project, compiler, loaded, strict=True)
    code = data = 0
    owners = {}
    for addr, unit in loaded:
        v = results[addr]
        if not v.ok:
            report.problem(unit.path, 'is not a proven match (see the match gate)')
            continue
        try:
            pieces = link(project, v.obj, unit, v.placement)
        except LinkError as e:
            report.problem(unit.path, f'does not link: {e}')
            continue
        bad = [(base, len(blob)) for base, blob in pieces if image.read(base, len(blob)) != blob]
        if bad:
            report.problem(unit.path, f'linked bytes differ from retail at {bad[0][0]:#x} ({bad[0][1]} bytes)')
            continue
        row = project.rows[addr]
        if not any(base == addr and len(blob) >= row['_size'] for base, blob in pieces):
            report.problem(unit.path, 'its function does not cover its row of the function table')
            continue
        for base, blob in pieces:
            home = image.section_at(base)
            if home is not None and home.name in project.code_sections:
                code += len(blob)
            else:
                data += len(blob)
        owners[row['owner']] = owners.get(row['owner'], 0) + row['_size']
    split = ', '.join(f'{k} {v}' for k, v in sorted(owners.items()))
    return report.finish(f'{len(loaded)} units linked: {code} bytes of code and {data} bytes of data equal retail'
                         + (f'; function bytes by owner: {split}' if split else ''))
