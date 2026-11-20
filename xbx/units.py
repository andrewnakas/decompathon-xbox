"""A unit file: one function's source, named for its retail address, with the
directives a unit may carry.

    /* cflags: /O1 */                    first line only; flags from the project's allow-list
    /* sym: ??0Foo@@QAE@XZ = 0x12A340 */  the retail address of a decorated name that cannot carry one
    /* const: 0x10000 */                 a number in the image's address range that is a number, not an address
    /* fuzzy: 87.5 */                    (non-matching tier) the verified similarity score

Most names need no directive: a function or variable called func_XXXXXXXX or
data_XXXXXXXX carries its address in every decoration the compiler gives it
(?func_0012A340@Foo@@QAEXH@Z, _func_0012A340@8, @func_0012A340@8,
?data_001EFC88@@3HA), so nothing is declared and nothing can be misdeclared.
"""
import os
import re
from dataclasses import dataclass, field

CFLAGS = re.compile(r'\A\s*/\*\s*cflags:\s*(.*?)\s*\*/')
SYM = re.compile(r'^[ \t]*/\*\s*sym:\s*(\S+)\s*=\s*(0[xX][0-9A-Fa-f]+)\s*\*/[ \t]*$', re.M)
CONST = re.compile(r'^[ \t]*/\*\s*const:\s*(0[xX][0-9A-Fa-f]+)\s*\*/[ \t]*$', re.M)
FUZZY = re.compile(r'^[ \t]*/\*\s*fuzzy:\s*(\d+(?:\.\d+)?)\s*\*/[ \t]*$', re.M)
UNIT_ADDRESS = re.compile(r'func_([0-9A-Fa-f]{8})\.(?:cpp|c)$')
# the identifier comes first in every decoration: optional __imp_, then one of ? _ @
PLACEHOLDER = re.compile(r'^(?:__imp_)?[?_@]?(func|data)_([0-9A-Fa-f]{8})(?![0-9A-Za-z_])')

# names the C runtime defines as absolute values rather than addresses in the image
ABSOLUTE = {'__except_list': 0}


@dataclass
class Unit:
    path: str
    addr: int
    text: str
    cflags: list = field(default_factory=list)
    syms: dict = field(default_factory=dict)       # decorated name -> address
    consts: set = field(default_factory=set)
    fuzzy: float | None = None
    duplicate_syms: list = field(default_factory=list)


def placeholder(name):
    """(kind, address) when a decorated name is an address-carrying placeholder, else None."""
    m = PLACEHOLDER.match(name)
    return (m.group(1), int(m.group(2), 16)) if m else None


def unit_address(path):
    m = UNIT_ADDRESS.search(os.path.basename(path))
    return int(m.group(1), 16) if m else None


def parse(text, path='<text>', addr=None):
    unit = Unit(path, unit_address(path) if addr is None else addr, text)
    m = CFLAGS.match(text)
    if m:
        unit.cflags = m.group(1).split()
    for name, value in SYM.findall(text):
        value = int(value, 16)
        if name in unit.syms and unit.syms[name] != value:
            unit.duplicate_syms.append(name)
        unit.syms[name] = value
    unit.consts = {int(v, 16) for v in CONST.findall(text)}
    m = FUZZY.search(text)
    if m:
        unit.fuzzy = float(m.group(1))
    return unit


def load(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return parse(f.read(), path)


def write_fuzzy(path, score):
    """Sets (or adds, after a first-line cflags comment) the unit's /* fuzzy: */ line."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    line = f'/* fuzzy: {score:.1f} */'
    if FUZZY.search(text):
        text = FUZZY.sub(line, text, count=1)
    elif CFLAGS.match(text):
        head, _, rest = text.partition('\n')
        text = f'{head}\n{line}\n{rest}'
    else:
        text = f'{line}\n{text}'
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
