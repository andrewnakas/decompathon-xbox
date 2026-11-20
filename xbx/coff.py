"""Reads a COFF object file as the Microsoft compilers write it: sections,
symbols, relocations, and the blocks that the linker places as one piece.

A block is what a relocation can point into and what ends up contiguous in the
linked image: a whole COMDAT section, or, in an ordinary section, the bytes
from one defined symbol to the next. The function a unit is about is a block;
so is a string literal, a floating-point constant, an exception-handling
table, a vtable.
"""
import struct
from dataclasses import dataclass, field

MACHINE_I386 = 0x14C

# section characteristics
CODE, INITIALIZED, UNINITIALIZED = 0x20, 0x40, 0x80
LNK_INFO, LNK_REMOVE, LNK_COMDAT = 0x200, 0x800, 0x1000
DISCARDABLE = 0x02000000

# i386 relocation types
REL_ABSOLUTE, REL_DIR32, REL_DIR32NB, REL_SECTION, REL_SECREL, REL_REL32 = 0x00, 0x06, 0x07, 0x0A, 0x0B, 0x14
RELOCATION_NAMES = {REL_ABSOLUTE: 'ABSOLUTE', REL_DIR32: 'DIR32', REL_DIR32NB: 'DIR32NB', REL_SECTION: 'SECTION',
                    REL_SECREL: 'SECREL', REL_REL32: 'REL32'}

# symbol storage classes
CLASS_EXTERNAL, CLASS_STATIC, CLASS_LABEL, CLASS_WEAK = 2, 3, 6, 105


class CoffError(Exception):
    pass


@dataclass
class Relocation:
    offset: int        # in the section
    symbol: int        # index into Coff.symbols
    type: int


@dataclass
class Section:
    index: int         # 1-based, as symbols refer to it
    name: str
    data: bytes        # b'' for uninitialised data
    size: int
    flags: int
    relocations: list
    comdat: int = 0    # the COMDAT selection (1..6) when LNK_COMDAT, else 0
    associated: int = 0

    @property
    def is_code(self):
        return bool(self.flags & CODE)

    @property
    def is_info(self):
        """Linker directives and debug information: never part of the image."""
        return bool(self.flags & (LNK_INFO | LNK_REMOVE | DISCARDABLE)) or self.name.startswith('.debug')

    @property
    def is_bss(self):
        return bool(self.flags & UNINITIALIZED) and not self.data


@dataclass
class Symbol:
    index: int
    name: str
    value: int
    section: int       # 1-based section, 0 undefined, -1 absolute, -2 debug
    type: int
    storage: int
    default: int = -1  # a weak external's fallback: the symbol index used when nothing defines it

    @property
    def is_function(self):
        return (self.type >> 4) == 2

    @property
    def defined(self):
        return self.section > 0


@dataclass
class Block:
    section: Section
    start: int
    end: int
    symbols: list = field(default_factory=list)   # the symbols at start (the block's names)

    @property
    def name(self):
        for s in self.symbols:
            if s.storage == CLASS_EXTERNAL:
                return s.name
        return self.symbols[0].name if self.symbols else f'{self.section.name}+{self.start:#x}'

    @property
    def data(self):
        return self.section.data[self.start:self.end]

    @property
    def size(self):
        return self.end - self.start

    @property
    def relocations(self):
        return [r for r in self.section.relocations if self.start <= r.offset < self.end]

    @property
    def key(self):
        return (self.section.index, self.start)


class Coff:
    def __init__(self, data, path='<object>'):
        self.path = path
        if len(data) < 20:
            raise CoffError(f'{path}: too short for a COFF object')
        machine, nsec, _, symptr, nsym, opt, _ = struct.unpack_from('<HHIIIHH', data, 0)
        if machine != MACHINE_I386:
            if machine == 0 and nsec == 0xFFFF:
                raise CoffError(f'{path}: an anonymous object (compiled with /GL): there is no machine code in it')
            raise CoffError(f'{path}: not an i386 COFF object (machine {machine:#x})')
        strtab = symptr + 18 * nsym

        def string(offset):
            start = strtab + offset
            return data[start:data.index(b'\0', start)].decode('latin-1')

        self.symbols, slots, i = [], {}, 0   # slots: raw symbol index -> Symbol
        aux_of = {}
        while i < nsym:
            o = symptr + 18 * i
            raw = data[o:o + 8]
            name = string(struct.unpack_from('<I', raw, 4)[0]) if raw[:4] == b'\0\0\0\0' \
                else raw.rstrip(b'\0').decode('latin-1')
            value, secnum, typ, cls, naux = struct.unpack_from('<IhHBB', data, o + 8)
            sym = Symbol(i, name, value, secnum, typ, cls)
            slots[i] = sym
            self.symbols.append(sym)
            if naux:
                aux_of[i] = data[o + 18:o + 36]
                if cls == CLASS_WEAK:
                    sym.default = struct.unpack_from('<I', aux_of[i], 0)[0]
            i += 1 + naux
        self._slots = slots

        self.sections = []
        for n in range(nsec):
            o = 20 + opt + 40 * n
            raw = data[o:o + 8].rstrip(b'\0').decode('latin-1')
            name = string(int(raw[1:])) if raw.startswith('/') else raw
            vsize, _, rsize, rptr, relptr, _, nrel, _, flags = struct.unpack_from('<IIIIIIHHI', data, o + 8)
            body = data[rptr:rptr + rsize] if rptr and not flags & UNINITIALIZED else b''
            relocations = []
            for r in range(nrel):
                off, symidx, typ = struct.unpack_from('<IIH', data, relptr + 10 * r)
                relocations.append(Relocation(off, symidx, typ))
            relocations.sort(key=lambda r: r.offset)
            self.sections.append(Section(n + 1, name, body, rsize, flags, relocations))
        # a section symbol's auxiliary record carries the COMDAT selection
        for sym in self.symbols:
            if sym.storage == CLASS_STATIC and sym.value == 0 and sym.section > 0 and sym.index in aux_of:
                sec = self.sections[sym.section - 1]
                if sym.name == sec.name or sec.flags & LNK_COMDAT:
                    _, _, _, _, number, selection = struct.unpack_from('<IHHIHB', aux_of[sym.index], 0)
                    if sec.flags & LNK_COMDAT and not sec.comdat:
                        sec.comdat, sec.associated = selection, number
        self._blocks = None

    def symbol(self, index):
        try:
            return self._slots[index]
        except KeyError:
            raise CoffError(f'{self.path}: relocation names symbol {index}, which is an auxiliary record') from None

    def section(self, index):
        return self.sections[index - 1]

    def _is_section_symbol(self, sym):
        return (sym.storage == CLASS_STATIC and sym.value == 0 and sym.section > 0
                and sym.name == self.sections[sym.section - 1].name)

    def blocks(self):
        """Every block of every section that is part of the image, in section and offset order."""
        if self._blocks is not None:
            return self._blocks
        out = []
        for sec in self.sections:
            if sec.is_info:
                continue
            marks = {}
            for sym in self.symbols:
                # a label (a jump target, a switch table inside a function) does not start a block
                if (sym.section == sec.index and not self._is_section_symbol(sym)
                        and sym.storage in (CLASS_EXTERNAL, CLASS_STATIC, CLASS_WEAK)
                        and not (sec.is_code and not sym.is_function)):
                    marks.setdefault(sym.value, []).append(sym)
            if sec.comdat or not marks:
                names = [s for syms in marks.values() for s in syms] if sec.comdat else []
                out.append(Block(sec, 0, sec.size, sorted(names, key=lambda s: s.value)))
                continue
            offsets = sorted(marks)
            if offsets[0] != 0:
                out.append(Block(sec, 0, offsets[0], []))
            for k, start in enumerate(offsets):
                end = offsets[k + 1] if k + 1 < len(offsets) else sec.size
                out.append(Block(sec, start, end, marks[start]))
        self._blocks = out
        return out

    def block_at(self, section_index, offset):
        """The block holding an offset of a section, or None (an info section, or past the end)."""
        for b in self.blocks():
            if b.section.index == section_index and (b.start <= offset < b.end or (b.size == 0 and offset == b.start)):
                return b
        return None

    def functions(self):
        """The code blocks that start at a function symbol."""
        return [b for b in self.blocks() if b.section.is_code and any(s.is_function for s in b.symbols)]


def load(path):
    with open(path, 'rb') as f:
        return Coff(f.read(), path)
