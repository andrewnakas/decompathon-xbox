"""A unit file: one function's source, named for its retail address, with the
directives a unit may carry.

    /* cflags: /O1 */                    first line only; flags from the project's allow-list
    /* sym: ??0Foo@@QAE@XZ = 0x12A340 */  the retail address of a decorated name that cannot carry one:
                                         a special member, or a virtual override (below)
    /* const: 0x10000 */                 a number in the image's address range that is a number, not an address
    /* fuzzy: 87.5 */                    (non-matching tier) the verified similarity score

Most names need no directive: a function or variable called func_XXXXXXXX or
data_XXXXXXXX carries its address in every decoration the compiler gives it
(?func_0012A340@Foo@@QAEXH@Z, _func_0012A340@8, @func_0012A340@8,
?data_001EFC88@@3HA), so nothing is declared and nothing can be misdeclared.

Overrides. A virtual function that overrides another must keep the name of the
one it overrides, so Derived's override of func_00011C90 is
?func_00011C90@Derived@@UAEXXZ while it sits at an address of its own. A sym
line gives such a name its address (`/* sym: ?func_00011C90@Derived@@UAEXXZ
= 0x728D0 */`): only a virtual member function's name, only another address
than the one it carries, and the line then wins over the carried address.

Folded functions. When the linker folded identical functions into one (a bare
`ret` that is a dozen different empty virtual functions, two slots of the same
vtable), that one address stands for several source functions, which C++
cannot give one name. For an address listed under [names] folded in xbx.toml
a unit may also write func_XXXXXXXX_1 ... func_XXXXXXXX_99: the same address
under another name. The one-address-one-declaration rule does not apply to a
folded address (it is several functions); one name still means one address.
Overrides that the linker folded (two classes' identical overrides of the same
slot) are listed the same way and keep their override names.
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
PLACEHOLDER = re.compile(r'^(?:__imp_)?[?_@]?(func|data)_([0-9A-Fa-f]{8})(?:_([1-9][0-9]?))?(?![0-9A-Za-z_])')

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


def alias(name):
    """N for func_XXXXXXXX_N (a folded function's alias), else 0."""
    m = PLACEHOLDER.match(name)
    return int(m.group(3)) if m and m.group(3) else 0


def slot(name, addr):
    """What the one-address-one-declaration rule keys on: the address, and the
    alias for a folded function's other names."""
    return (addr, alias(name))


# after the class qualifiers, a member function's access code; these are the virtual ones
VIRTUAL_CODES = frozenset('EFMNUV')


def is_override(name, addr):
    """Whether a sym line may give `name` the address `addr`: the name of a
    virtual member function that carries another function's address."""
    hit = placeholder(name)
    if not hit or hit[0] != 'func' or hit[1] == addr or not name.startswith('?'):
        return False
    at = name.find('@@')
    return at > 0 and name.count('@', 0, at) >= 1 and name[at + 2:at + 3] in VIRTUAL_CODES


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
