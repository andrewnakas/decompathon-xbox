"""Compiles a unit and proves it against the retail executable.

    xbx match <unit.cpp> [...]        compile and compare each unit
    xbx match --all [--tier nonmatching]
    xbx match --strict ...            the gate's rules (the default explains instead of failing)

Nothing is masked. The unit's function is placed at the address its file name
carries and compared byte for byte. Every relocation is resolved against the
retail bytes: the address retail holds in that field must be the address of
the symbol our object names there.

  - an undefined external must resolve (a func_/data_ placeholder, the
    project's shared symbol table, or a /* sym: */ line) to exactly that address;
  - a symbol our own object defines (a string or floating-point constant, an
    exception-handling funclet or table, a vtable, a jump table) gets the
    address retail implies, every reference to it must imply the same one,
    and its contents are then compared there, by the same rules.

So a wrong constant, string, table entry or call target is a difference, and
everything the object would put in the image is either verified or refused.
"""
import bisect
import hashlib
import json
import os
import struct
import sys
from dataclasses import dataclass, field

from capstone import CS_ARCH_X86, CS_MODE_32, Cs
from capstone import x86 as cs_x86

from . import coff, units
from .pad import is_padding
from .toolchain import ToolError

SELECT_NODUPLICATES, SELECT_ANY = 1, 2
# compiler-generated code that legitimately rides along in a unit's object:
# scalar and vector deleting destructors, virtual-call thunks
GENERATED_CODE = ('??_G', '??_E', '??_9')
EH_CODE = '.text$x'
BANNED_SECTIONS = ('.CRT$', '.tls', '.bss')
CACHE_VERSION = '1'


@dataclass
class Verdict:
    addr: int
    path: str = ''
    ok: bool = False
    problems: list = field(default_factory=list)   # why it is not a proven match
    notes: list = field(default_factory=list)      # things worth knowing that do not fail it
    primary: str = ''
    ours: bytes = b''
    mask: frozenset = frozenset()                  # offsets of relocation bytes in ours
    retail: bytes = b''
    first: int = -1                                # first differing offset in the function, -1 if none
    differing: int = 0                             # differing bytes in the function
    placed: list = field(default_factory=list)     # (name, address, size, kind) of every verified block
    links: dict = field(default_factory=dict)      # external name -> address it resolved to
    assumed: dict = field(default_factory=dict)    # external name -> address retail implies (no mapping yet)
    defines: str = ''                              # the decorated name the unit gives its function
    covers: list = field(default_factory=list)     # other function-table rows this object's code covers
    used_syms: set = field(default_factory=set)    # /* sym: */ names that resolved something
    used_consts: set = field(default_factory=set)  # /* const: */ values that excused something
    output: str = ''                               # compiler messages
    obj: object = field(default=None, repr=False)  # the parsed object
    placement: dict = field(default_factory=dict, repr=False)   # block key -> retail address

    @property
    def status(self):
        return 'MATCH' if self.ok else 'DIFF'


def _u32(data, k):
    return struct.unpack_from('<I', data, k)[0]


def _s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def code_end(obj, block):
    """Where a function block's instructions end and its switch tables begin: the
    lowest target of a reference from the code into the block's own later bytes."""
    end = block.size
    own = block.section.index
    for r in block.relocations:
        k = r.offset - block.start
        if k >= end or r.type != coff.REL_DIR32:
            continue
        sym = obj.symbol(r.symbol)
        if sym.section == own:
            target = sym.value + (_u32(block.data, k) if obj._is_section_symbol(sym) else 0) - block.start
            if k < target < end:
                end = target
    return end


class Matcher:
    def __init__(self, project, strict=False):
        self.project = project
        self.image = project.image
        self.strict = strict
        self._md = Cs(CS_ARCH_X86, CS_MODE_32)
        self._md.detail = True
        self._starts = sorted(project.rows)

    # ---- names ---------------------------------------------------------

    def resolve(self, name, unit, v=None):
        """The retail address of an external name, or None when nothing maps it."""
        hit = units.placeholder(name)
        if hit:
            return hit[1]
        if name in units.ABSOLUTE:
            return units.ABSOLUTE[name]
        shared = self.project.shared_symbols
        if name in shared:
            return shared[name]
        if name in unit.syms:
            if v is not None:
                v.used_syms.add(name)
            return unit.syms[name]
        return None

    def next_start(self, addr):
        i = bisect.bisect_right(self._starts, addr)
        return self._starts[i] if i < len(self._starts) else None

    def tile(self, addr, data):
        """The function-table rows that our code block at addr covers exactly
        (row after row, then nothing but padding), or None."""
        rows, pos, end = [], addr, addr + len(data)
        while pos < end:
            row = self.project.rows.get(pos)
            if row is None:
                return rows if rows and is_padding(data[pos - addr:]) else None
            rows.append(row)
            pos += row['_size']
        return rows if pos == end else None

    # ---- the comparison ------------------------------------------------

    def compare(self, obj, unit):
        """The Verdict for a parsed object (coff.Coff) of a unit."""
        v = Verdict(unit.addr, unit.path)
        v.obj = obj
        image, rows = self.image, self.project.rows
        row = rows.get(unit.addr)
        if row is None:
            v.problems.append(f'{unit.addr:#010x} is not a function start in the function table')
            return v
        if row.get('kind', 'func') != 'func':
            v.problems.append(f'{unit.addr:#010x} is a compiler-made {row["kind"]} stub: it is matched with the '
                              f'function it belongs to' + (f' ({row["parent"]})' if row.get('parent') else ''))
            return v

        primary = self._primary(obj, unit, v)
        if primary is None:
            return v
        v.primary = v.defines = primary.name
        v.ours = primary.data
        span = max(row['_size'], primary.size)
        v.retail = image.read(unit.addr, span)
        v.mask = frozenset(o for r in primary.relocations for o in range(r.offset - primary.start,
                                                                         r.offset - primary.start + 4))

        placed = v.placement                      # block key -> retail address
        placed[primary.key] = unit.addr
        work = [primary]
        while work:
            block = work.pop()
            self._verify(obj, unit, v, block, placed[block.key], placed, work, block is primary)

        # the function's own extent: the table's row, then at most padding
        size = row['_size']
        if primary.size < size:
            v.problems.append(f'size: ours is {primary.size} bytes, retail\'s function is {size}')
        elif primary.size > size:
            room = self.next_start(unit.addr)
            if room is not None and unit.addr + primary.size > room:
                v.problems.append(f'size: ours is {primary.size} bytes and runs into the function at {room:#x} '
                                  f'(retail\'s is {size})')
            elif not is_padding(primary.data[size:]):
                message = (f'size: ours is {primary.size} bytes, the function table says {size}; if every byte '
                           'equals retail the table row is short (a maintainer fixes the table)')
                (v.problems if self.strict else v.notes).append(message)

        # nothing unverified may ride along
        for b in obj.blocks():
            if b.key in placed or b.size == 0:
                continue
            what = 'code' if b.section.is_code else 'data'
            v.problems.append(f'unverified {what} in the object: {b.name} ({b.section.name}, {b.size} bytes) '
                              'is referenced by nothing that was compared')
        self._strays(obj, unit, v, primary, unit.addr)
        if self.strict:
            for name in sorted(set(unit.syms) - v.used_syms):
                v.problems.append(f'/* sym: {name} */ is not used by the object: remove it')
            for value in sorted(unit.consts - v.used_consts):
                v.problems.append(f'/* const: {value:#x} */ excuses nothing in the object: remove it')
            for name in unit.duplicate_syms:
                v.problems.append(f'/* sym: {name} */ is given two different addresses')
        v.ok = not v.problems
        return v

    def _primary(self, obj, unit, v):
        want = f'func_{unit.addr:08x}'
        functions = obj.functions()

        def carries(block):
            for s in block.symbols:
                hit = units.placeholder(s.name)
                if hit and hit[0] == 'func' and hit[1] == unit.addr and want in s.name.lower():
                    return True
            return False

        named = [b for b in functions if carries(b)]
        block = None
        if len(named) == 1:
            block = named[0]
        elif not named:
            wanted = {n for n, a in unit.syms.items() if a == unit.addr}
            hits = [b for b in functions if any(s.name in wanted for s in b.symbols)]
            if len(hits) == 1:
                block = hits[0]
                v.used_syms.update(s.name for s in block.symbols if s.name in wanted)
                if self.strict and not any(s.name.startswith('??') for s in block.symbols if s.name in wanted):
                    v.problems.append(f'{block.name}: only a special member (a ?? name: constructor, destructor, '
                                      f'operator) may take its address from /* sym: */; name it func_{unit.addr:08X}')
        if block is None:
            names = ', '.join(b.name for b in functions) or 'none'
            v.problems.append(f'the object defines no function for this unit: expected one named func_{unit.addr:08X} '
                              f'(or a /* sym: */ line giving a special member this address). Functions: {names}')
            return None
        if block.section.comdat == SELECT_ANY:
            v.problems.append(f'{block.name} is emitted as an inline copy (defined inline or in a class body); '
                              'define the unit\'s function out of line')
        return block

    def _verify(self, obj, unit, v, block, addr, placed, work, is_primary):
        image = self.image
        sec = block.section
        kind = 'code' if sec.is_code else 'data'
        home = image.section_at(addr)
        name = block.name
        if home is None or addr + block.size > home.va + home.vsize:
            v.problems.append(f'{name}: retail refers to {addr:#x}, which is not {block.size} bytes inside a section')
            return
        if sec.is_bss:
            v.problems.append(f'{name}: the unit defines uninitialised data; declare it extern (data_XXXXXXXX)')
            return
        if any(sec.name.startswith(p) for p in BANNED_SECTIONS):
            v.problems.append(f'{name}: the unit emits {sec.name} (a dynamic initialiser or thread-local data)')
            return
        if addr + block.size > home.va + home.rsize:
            v.problems.append(f'{name}: retail refers to {addr:#x}, in the zero-filled part of {home.name}; '
                              'a constant cannot live there')
            return
        if sec.is_code != (home.name in self.project.code_sections):
            v.problems.append(f'{name}: our {kind} would sit at {addr:#x}, which is in {home.name}')
            return
        if not is_primary:
            if sec.is_code:
                rows = self.tile(addr, block.data)
                if rows is None:
                    v.problems.append(f'{name}: retail refers to {addr:#x}; our {block.size} bytes of code there do '
                                      'not line up with the function table')
                    return
                eh = sec.name.startswith(EH_CODE)
                generated = any(s.name.startswith(GENERATED_CODE) for s in block.symbols)
                if eh:
                    wrong = [r for r in rows if r.get('kind') != 'eh']
                    if wrong:
                        v.problems.append(f'{name}: exception-handling code would sit on the function at '
                                          f'{wrong[0]["_addr"]:#x}')
                    strangers = [r for r in rows if r.get('parent') and int(r['parent'], 16) != unit.addr]
                    if strangers:
                        v.problems.append(f'{name}: exception-handling stub {strangers[0]["_addr"]:#x} belongs to '
                                          f'the function at {strangers[0]["parent"]}')
                elif not generated:
                    v.problems.append(f'{name}: a second function rides in this unit (an out-of-line copy of an '
                                      'inline function, or a helper that was not inlined); declare it extern')
                v.covers += [r['_addr'] for r in rows]
            elif any(s.storage == coff.CLASS_EXTERNAL for s in block.symbols) and not sec.comdat:
                v.problems.append(f'{name}: the unit defines a global; declare it extern (data_XXXXXXXX)')
            elif any(units.placeholder(s.name) for s in block.symbols):
                v.problems.append(f'{name}: the unit defines data it should only declare (extern)')
        v.placed.append((name, addr, block.size, kind))

        ours, retail = block.data, image.read(addr, block.size)
        skip = set()
        for r in block.relocations:
            k = r.offset - block.start
            if k + 4 > block.size:
                v.problems.append(f'{name}+{k:#x}: a relocation runs past the end of its block')
                continue
            skip.update(range(k, k + 4))
            if r.type not in (coff.REL_DIR32, coff.REL_REL32):
                v.problems.append(f'{name}+{k:#x}: relocation type '
                                  f'{coff.RELOCATION_NAMES.get(r.type, hex(r.type))} is not allowed')
                continue
            addend, value = _u32(ours, k), _u32(retail, k)
            target = value if r.type == coff.REL_DIR32 else (addr + k + 4 + _s32(value)) & 0xFFFFFFFF
            self._reference(obj, unit, v, block, k, obj.symbol(r.symbol), _s32(addend), target, placed, work,
                            is_primary)

        diffs = [k for k in range(block.size) if k not in skip and ours[k] != retail[k]]
        if diffs:
            if is_primary:
                v.first = diffs[0] if v.first < 0 else min(v.first, diffs[0])
                v.differing += len(diffs)
                v.problems.append(f'code differs: {len(diffs)} bytes, first at +{diffs[0]:#x}')
            else:
                v.problems.append(f'{name} at {addr:#x} differs from retail: {len(diffs)} of {block.size} bytes, '
                                  f'first at +{diffs[0]:#x}')

    def _reference(self, obj, unit, v, block, k, sym, addend, target, placed, work, is_primary):
        where = f'{block.name}+{k:#x}'

        def differs(message):
            v.problems.append(message)
            if is_primary:
                v.first = k if v.first < 0 else min(v.first, k)
                v.differing += 4

        if sym.section == 0 and sym.storage == coff.CLASS_WEAK and sym.default >= 0 \
                and self.resolve(sym.name, unit, v) is None:
            sym = obj.symbol(sym.default)           # nothing else defines it: the linker uses the fallback
        if sym.section == -1:                       # an absolute symbol
            if (sym.value + addend) & 0xFFFFFFFF != target:
                differs(f'{where}: {sym.name} is {sym.value:#x}, retail has {target:#x}')
            return
        if sym.section > 0:                         # something our own object defines
            section_symbol = obj._is_section_symbol(sym)
            offset = sym.value + (addend if section_symbol else 0)
            extra = 0 if section_symbol else addend
            b = obj.block_at(sym.section, offset)
            if b is None:
                v.problems.append(f'{where}: refers to {sym.name}, which is not part of the image')
                return
            base = (target - (offset - b.start) - extra) & 0xFFFFFFFF
            if sym.is_function and extra:
                v.problems.append(f'{where}: refers into the middle of function {sym.name} (+{extra:#x})')
            if b.key in placed:
                if placed[b.key] != base:
                    if b.key == block.key:
                        differs(f'{where}: retail refers to {target:#x}, ours to its own '
                                f'+{offset - b.start + extra:#x} ({placed[b.key] + offset - b.start + extra:#x})')
                    else:
                        differs(f'{where}: {b.name} is at {placed[b.key]:#x} by an earlier reference, '
                                f'retail refers to {base:#x} here')
                return
            placed[b.key] = base
            work.append(b)
            return
        # an undefined external
        known = self.resolve(sym.name, unit, v)
        implied = (target - addend) & 0xFFFFFFFF
        if known is None:
            if self.strict:
                v.problems.append(f'{where}: {sym.name} has no address (retail implies {implied:#x}); '
                                  'name it func_/data_XXXXXXXX or map it')
                return
            if v.assumed.setdefault(sym.name, implied) != implied:
                differs(f'{where}: {sym.name} would be {implied:#x} here and '
                        f'{v.assumed[sym.name]:#x} elsewhere')
            known = v.assumed[sym.name]
        else:
            v.links[sym.name] = known
            if (known + addend) & 0xFFFFFFFF != target:
                differs(f'{where}: {sym.name} is {known:#x}, retail refers to {target:#x}'
                        + (f' ({sym.name}{addend:+#x})' if addend else ''))
                return
        if sym.name in units.ABSOLUTE:
            return
        hit = units.placeholder(sym.name)
        row = self.project.rows.get(known)
        home = self.image.section_at(known)
        in_code = home is not None and home.name in self.project.code_sections
        if sym.is_function or (hit and hit[0] == 'func'):
            if addend:
                v.problems.append(f'{where}: refers into the middle of function {sym.name} ({addend:+#x})')
            elif row is None:
                message = f'{where}: {sym.name} at {known:#x} is not a function start in the function table'
                (v.problems if self.strict else v.notes).append(message)
        elif hit and hit[0] == 'data' and in_code and self.strict:
            v.problems.append(f'{where}: {sym.name} is in a code section; declare it as the function it is')

    def _strays(self, obj, unit, v, primary, addr):
        """Numbers inside the image's address range that no relocation covers."""
        image = self.image
        low, high = image.base, image.end
        data = primary.data[:code_end(obj, primary)]
        mask = v.mask
        for ins in self._md.disasm(data, addr):
            off = ins.address - addr
            if cs_x86.X86_GRP_BRANCH_RELATIVE in ins.groups:
                continue
            for kind, at, size in (('displacement', ins.disp_offset, ins.disp_size),
                                   ('immediate', ins.imm_offset, ins.imm_size)):
                if size != 4 or not at:
                    continue
                k = off + at
                if any(o in mask for o in range(k, k + 4)) or k + 4 > len(data):
                    continue
                value = _u32(data, k)
                if not low <= value < high:
                    continue
                absolute = kind == 'displacement' and any(
                    op.type == cs_x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0 for op in ins.operands)
                if absolute:
                    v.problems.append(f'+{off:#x}: `{ins.mnemonic} {ins.op_str}` reads or writes the address '
                                      f'{value:#x} written as a number; declare data_{value:08X} instead')
                elif value in unit.consts:
                    v.used_consts.add(value)
                else:
                    message = (f'+{off:#x}: `{ins.mnemonic} {ins.op_str}` holds {value:#x}, a number in the '
                               f'image\'s address range; if it is a number add /* const: {value:#x} */, '
                               'if it is an address use a symbol')
                    (v.problems if self.strict else v.notes).append(message)


# ---- compiling ------------------------------------------------------------

def unit_flags(project, unit):
    return list(project.cflags) + list(unit.cflags)


_DIGESTS = {}


def _tree_digest(project):
    """One hash of everything a unit can include."""
    key = ('tree', project.root)
    if key not in _DIGESTS:
        h = hashlib.sha256()
        for base in project.includes:
            for folder, dirs, files in os.walk(base):
                dirs.sort()
                for name in sorted(files):
                    path = os.path.join(folder, name)
                    h.update(os.path.relpath(path, project.root).encode() + b'\0')
                    with open(path, 'rb') as f:
                        h.update(hashlib.sha256(f.read()).digest())
        _DIGESTS[key] = h.hexdigest()
    return _DIGESTS[key]


def _compiler_digest(compiler):
    key = ('cc', compiler.msvc)
    if key not in _DIGESTS:
        _DIGESTS[key] = hashlib.sha256(json.dumps(compiler.digests(), sort_keys=True).encode()).hexdigest()
    return _DIGESTS[key]


def forget_digests():
    _DIGESTS.clear()


def compile_unit(project, compiler, unit, source_path=None, cache=True):
    """The object of a unit (its file, or source_path when its text lives elsewhere)
    as (bytes, compiler messages). An object is reused only when the source text,
    the flags, every header a unit can include and every compiler file are the same."""
    source = source_path or unit.path
    flags = unit_flags(project, unit)
    with open(source, 'rb') as f:
        text = f.read()
    key = hashlib.sha256(json.dumps([CACHE_VERSION, _compiler_digest(compiler), _tree_digest(project), flags,
                                     hashlib.sha256(text).hexdigest()]).encode()).hexdigest()
    folder = os.path.join(project.out, 'cache', key[:2])
    cached = os.path.join(folder, key + '.obj')
    if cache and os.path.exists(cached):
        with open(cached, 'rb') as f:
            return f.read(), ''
    os.makedirs(folder, exist_ok=True)
    scratch = os.path.join(folder, f'{key}.{os.getpid()}.tmp.obj')
    try:
        output = compiler.compile(source, scratch, flags, project.includes)
        with open(scratch, 'rb') as f:
            data = f.read()
        os.replace(scratch, cached)
    finally:
        if os.path.exists(scratch):
            os.remove(scratch)
    return data, output


def check(project, compiler, unit, strict=False, obj_dir=None, source_path=None, matcher=None, cache=True):
    """Compile a unit and compare it. A compile error is a Verdict with the error as its problem."""
    v = Verdict(unit.addr, unit.path)
    try:
        data, output = compile_unit(project, compiler, unit, source_path, cache)
    except ToolError as e:
        v.problems.append('does not compile')
        v.output = str(e)
        return v
    try:
        obj = coff.Coff(data, source_path or unit.path)
    except coff.CoffError as e:
        v.problems.append(str(e))
        return v
    v = (matcher or Matcher(project, strict)).compare(obj, unit)
    v.output = output
    return v


def describe(v, verbose=True):
    lines = [f'{v.addr:08X} {v.status:5} {len(v.ours):5} bytes  {v.primary}'
             + (f'  first@+{v.first:#x} differing={v.differing}' if v.first >= 0 else '')]
    if verbose:
        lines += [f'    ! {p}' for p in v.problems]
        lines += [f'    . {n}' for n in v.notes]
        lines += [f'    ? {name} = {addr:#x}  (no mapping; retail implies this address)'
                  for name, addr in sorted(v.assumed.items())]
        if v.output and not v.ours:
            lines += ['      ' + line for line in v.output.strip().splitlines()[:40]]
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    from . import project as project_module, toolchain
    ap = argparse.ArgumentParser(prog='xbx match', description=__doc__.splitlines()[0])
    ap.add_argument('units', nargs='*')
    ap.add_argument('--all', action='store_true', help='every unit of the tier')
    ap.add_argument('--tier', default='matched', choices=('matched', 'nonmatching'))
    ap.add_argument('--strict', action='store_true', help="the gate's rules")
    ap.add_argument('--no-cache', action='store_true', help='compile even when the object is cached')
    ap.add_argument('-q', '--quiet', action='store_true', help='one line per unit')
    ap.add_argument('--write-fuzzy', action='store_true',
                    help='non-matching units: write the similarity score into the /* fuzzy: */ line')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
        compiler = toolchain.for_project(project)
        paths = [p for _, p in project.units(args.tier)] if args.all else args.units
        if not paths:
            ap.error('no units given')
        matcher = Matcher(project, args.strict)
        from . import names
        known = names.index(project, compiler, skip=paths) if args.tier == 'matched' else {}
        bad = 0
        for path in paths:
            unit = units.load(path)
            if unit.addr is None:
                print(f'{path}: not a unit file name (func_XXXXXXXX.cpp)')
                bad += 1
                continue
            v = check(project, compiler, unit, args.strict, matcher=matcher, cache=not args.no_cache)
            clashes = names.conflicts(known, v, project) if known else []
            if clashes:
                v.problems += clashes
                v.ok = False
            bad += not v.ok
            print(describe(v, not args.quiet))
            if v.ours and not v.ok:
                from . import fuzzy
                score = min(fuzzy.score(v.ours, v.mask, v.retail[:project.rows[v.addr]['_size']], v.addr,
                                        project.image.base), 99.9)
                print(f'    fuzzy {score}')
                if args.write_fuzzy:
                    units.write_fuzzy(path, score)
        print(f'{len(paths) - bad} of {len(paths)} match')
        return 1 if bad else 0
    except (project_module.ProjectError, ToolError) as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    sys.exit(main())
