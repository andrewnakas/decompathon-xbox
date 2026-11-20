"""Writes small COFF objects and a stand-in image for the tests, so the matcher
and the linker can be tested without a compiler or a game."""
import struct
from dataclasses import dataclass

from xbx import coff

TEXT = coff.CODE | 0x60000000 | 0x00500000           # code, execute, read, align 16
RDATA = coff.INITIALIZED | 0x40000000 | 0x00300000   # initialised data, read, align 4
FUNCTION = 0x20


def _name(name, strings):
    raw = name.encode('latin-1')
    if len(raw) <= 8:
        return raw.ljust(8, b'\0')
    offset = 4 + len(strings)
    strings += raw + b'\0'
    return struct.pack('<II', 0, offset)


def build(sections, symbols):
    """sections: dicts with name, data, flags, relocs [(offset, symbol name, type)] and
    comdat (a selection number, or absent). symbols: dicts with name, value, section
    (1-based, 0 undefined, -1 absolute), type, storage and, for a weak external,
    default (a symbol name)."""
    strings = bytearray()
    records, index = [], {}
    for n, sec in enumerate(sections, 1):
        flags = sec['flags'] | (coff.LNK_COMDAT if sec.get('comdat') else 0)
        sec['_flags'] = flags
        aux = struct.pack('<IHHIHB3x', len(sec['data']), len(sec.get('relocs', [])), 0, 0, 0, sec.get('comdat') or 0)
        records.append(_name(sec['name'], strings) + struct.pack('<IhHBB', 0, n, 0, coff.CLASS_STATIC, 1))
        records.append(aux)
    pending = []
    for sym in symbols:
        index[sym['name']] = len(records)
        weak = sym.get('default') is not None
        records.append(_name(sym['name'], strings) + struct.pack(
            '<IhHBB', sym.get('value', 0), sym.get('section', 0), sym.get('type', 0),
            sym.get('storage', coff.CLASS_EXTERNAL), 1 if weak else 0))
        if weak:
            pending.append((len(records), sym['default']))
            records.append(b'\0' * 18)
    for at, name in pending:
        records[at] = struct.pack('<II10x', index[name], 1)
    header_size = 20 + 40 * len(sections)
    body = bytearray()
    headers = bytearray()
    for sec in sections:
        raw = header_size + len(body)
        body += sec['data']
        rel = header_size + len(body)
        relocs = sec.get('relocs', [])
        for offset, name, kind in relocs:
            body += struct.pack('<IIH', offset, index[name], kind)
        headers += sec['name'].encode().ljust(8, b'\0')[:8] + struct.pack(
            '<IIIIIIHHI', 0, 0, len(sec['data']), raw, rel if relocs else 0, 0, len(relocs), 0, sec['_flags'])
    symptr = header_size + len(body)
    out = struct.pack('<HHIIIHH', coff.MACHINE_I386, len(sections), 0, symptr, len(records), 0, 0)
    out += bytes(headers) + bytes(body) + b''.join(records)
    out += struct.pack('<I', 4 + len(strings)) + bytes(strings)
    return bytes(out)


@dataclass
class Section:
    name: str
    va: int
    vsize: int
    rsize: int


class Image:
    """A stand-in for xbx.xbe.Xbe: sections of bytes at addresses."""

    def __init__(self, sections):
        self.sections = [Section(name, va, len(data) + bss, len(data)) for name, va, data, bss in sections]
        self._data = {name: bytearray(data) for name, va, data, bss in sections}
        self.base = 0x10000
        self.end = max(s.va + s.vsize for s in self.sections)
        self.thunk_va, self.thunks = 0, []

    def section_at(self, va):
        return next((s for s in self.sections if s.va <= va < s.va + s.vsize), None)

    def section(self, name):
        return next(s for s in self.sections if s.name == name)

    def read(self, va, size):
        s = self.section_at(va)
        if s is None:
            return bytes(size)
        data = self._data[s.name]
        return bytes(data[va - s.va:va - s.va + size]).ljust(size, b'\0')

    def write(self, va, data):
        s = self.section_at(va)
        self._data[s.name][va - s.va:va - s.va + len(data)] = data


class Project:
    """A stand-in for xbx.project.Project."""
    root = '/'
    code_sections = ['.text']

    def __init__(self, image, functions):
        self.image = image
        self.rows = {a: {'_addr': a, '_size': n, 'addr': f'{a:08x}', 'size': str(n), 'kind': 'func',
                         'owner': 'game', 'section': '.text', 'parent': '', 'conv': ''}
                     for a, n in functions.items()}
        self.starts = set(self.rows)
        self.shared_symbols = {}

    def relative(self, path):
        return path
