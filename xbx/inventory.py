"""Writes the project's function table from its retail XBE: every function in
the code sections, with who owns it, what kind of thing it is, how it takes
its arguments, and what it calls.

    xbx inventory [--check] [--out FILE]

    addr,size,owner,kind,section,conv,parent,calls

    owner    game, lib:<name> (code the game was linked with), from xbx.toml:
             a whole section ([owners.sections]) or an address range ([[owners.ranges]])
    kind     func, or eh: a compiler-made exception-handling stub (a frame
             handler stub or an unwind funclet), read from the image's own
             exception tables. It is matched with a function that registers it
    parent   for an eh row, the functions that register it (identical stubs
             of different functions are folded into one by the linker)
    conv     registers the function reads on entry before writing them, of
             eax ebx esi edi. Microsoft's standard conventions never pass
             arguments there; link-time code generation's custom ones do
    calls    the functions it calls or jumps to

The table holds addresses and sizes only, no bytes of the game. It is built
from scratch every time: discovery is seeded only by what xbx.toml lists
under [inventory] (known_starts, not_starts, data_ranges, each with the
evidence in a comment), never by an earlier table, so --check can prove the
committed table is exactly what the XBE gives.

Derived from tools/inventory.py of the halo2-decompiled project (GPL-3.0-or-later).
"""
import argparse
import bisect
import csv
import io
import os
import struct
import sys
from collections import Counter

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

from . import ltcg_probe
from .code import branches
from .functions import discover

COLUMNS = ['addr', 'size', 'owner', 'kind', 'section', 'conv', 'parent', 'calls']
EH_MAX_SIZE = 32
MIN_HANDLER_THUNKS = 8
FUNC_INFO_MAGIC = 0x19930520


def eh_thunk(data):
    """(FuncInfo address, handler address offset from the stub's end) for the bytes of
    `mov eax, imm32; jmp rel32`: the stub a function registers as its frame handler."""
    if len(data) >= 10 and data[0] == 0xB8 and data[5] == 0xE9:
        return struct.unpack_from('<I', data, 1)[0], struct.unpack_from('<i', data, 6)[0]
    return None


def func_info(image, va):
    """(unwind funclet addresses, catch block addresses) of the FuncInfo at va, or None
    when what is there is not one."""
    head = image.read(va, 20)
    magic, max_state, unwind, tries, try_map = struct.unpack('<IIIII', head)
    if magic != FUNC_INFO_MAGIC or max_state > 0x4000 or tries > 0x1000:
        return None
    funclets, catches = [], []
    for i in range(max_state):
        _, action = struct.unpack('<iI', image.read(unwind + 8 * i, 8))
        if action:
            funclets.append(action)
    for i in range(tries):
        _, _, _, count, handlers = struct.unpack('<iiiiI', image.read(try_map + 20 * i, 20))
        for j in range(min(count, 0x100)):
            catches.append(struct.unpack('<IIiI', image.read(handlers + 16 * j, 16))[3])
    return funclets, catches


def exception_handling(image, sections, found):
    """What the image's own exception tables say about its code.

    Returns (handlers, parents, catches): the frame handlers the stubs jump
    to; for every compiler-made stub (a frame-handler stub or an unwind
    funclet) the functions that register it; and the addresses of catch
    blocks, which are part of their function and never a function start."""
    spans = sorted((fn.start, fn.end) for fns in found.values() for fn in fns.values())
    starts = [s for s, _ in spans]

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        return spans[i][0] if i >= 0 and va < spans[i][1] else None

    stubs = {}
    for start, end in spans:
        if end - start <= EH_MAX_SIZE:
            hit = eh_thunk(image.read(start, end - start))
            if hit:
                stubs[start] = (hit[0], start + 10 + hit[1])
    counts = Counter(target for _, target in stubs.values())
    handlers = {t for t, n in counts.items() if n >= MIN_HANDLER_THUNKS}
    code = [(s.va, image.read(s.va, s.vsize)) for s in sections]
    parents, catches = {}, set()
    for stub, (info, target) in stubs.items():
        if target not in handlers:
            continue
        parsed = func_info(image, info)
        if parsed is None:
            continue
        # a function registers its stub with `push offset stub` or `mov eax, offset stub`
        users = set()
        for opcode in (b'\x68', b'\xb8'):
            pattern = opcode + struct.pack('<I', stub)
            for va, data in code:
                k = data.find(pattern)
                while k >= 0:
                    user = owner(va + k)
                    if user is not None and user != stub:
                        users.add(user)
                    k = data.find(pattern, k + 1)
        parents.setdefault(stub, set()).update(users)
        for funclet in parsed[0]:
            parents.setdefault(funclet, set()).update(users)
        catches.update(parsed[1])
    return handlers, parents, catches


class Span:
    """A function as the table lists it: discovery's result, after merging."""

    def __init__(self, fn):
        self.start, self.end = fn.start, fn.end
        self.calls, self.tail_jumps = set(fn.calls), set(fn.tail_jumps)
        self.flow = None      # (call targets, targets of jumps that leave it), decoded

    def absorb(self, other):
        self.end = max(self.end, other.end)
        self.calls |= other.calls
        self.tail_jumps = {t for t in self.tail_jumps | other.tail_jumps if not self.start <= t < self.end}


def merged(functions, not_starts):
    """Discovery's functions of one section with false starts joined to the
    function before them: an address the configuration or the exception tables
    say is not a start (a catch block), and a piece that the function before
    it jumps into the middle of (no function is entered anywhere but its
    first byte, so the two are one)."""
    out = []
    for fn in sorted(functions.values(), key=lambda f: f.start):
        span = Span(fn)
        while True:
            last = out[-1] if out else None
            if last is not None and (span.start in not_starts
                                     or any(span.start < t < span.end for t in last.tail_jumps)):
                last.absorb(span)
                span = out.pop()
                continue
            break
        out.append(span)
    return out


def refine(md, image, spans):
    """Makes one section's spans consistent with the jumps in them. No function
    is entered anywhere but at its first byte, so a jump from one span into
    the middle of another means the table is wrong there, one of two ways:

      - the spans from the one to the other are one function (a false start
        inside a long function; a function the compiler laid out around its
        exception blocks). That is the case when nothing outside them calls or
        jumps to any start between them: they are joined.
      - otherwise the target is the entry of a function that was only ever
        jumped to, and sits behind another with no call to show it: the span
        is cut there.
    Repeats until no jump lands inside a span. Returns the new list."""
    for _ in range(64):
        spans.sort(key=lambda s: s.start)
        starts = [s.start for s in spans]
        users = {}                                   # target -> spans that call or jump to it
        for s in spans:
            if s.flow is None:
                data = image.read(s.start, s.end - s.start)
                s.flow = branches(md, data, s.start)[:2]
            for t in s.flow[0] | s.flow[1]:
                users.setdefault(t, set()).add(s.start)
        fix = None
        for i, s in enumerate(spans):
            for t in sorted(s.flow[1]):
                j = bisect.bisect_right(starts, t) - 1
                if j < 0 or j == i or not spans[j].start < t < spans[j].end:
                    continue
                lo, hi = min(i, j), max(i, j)
                inner = {x.start for x in spans[lo + 1:hi + 1]}
                group = {x.start for x in spans[lo:hi + 1]}
                outside = any(users.get(x, set()) - group for x in inner)
                fix = ('cut', j, t) if outside else ('join', lo, hi)
                break
            if fix:
                break
        if fix is None:
            return spans
        if fix[0] == 'join':
            _, lo, hi = fix
            for other in spans[lo + 1:hi + 1]:
                spans[lo].absorb(other)
            spans[lo].flow = None
            del spans[lo + 1:hi + 1]
        else:
            _, j, t = fix
            whole = spans[j]
            tail = Span(whole)
            tail.start, tail.flow = t, None
            end = t
            data = image.read(whole.start, t - whole.start)
            while end > whole.start and data[end - whole.start - 1] in (0x90, 0xCC):
                end -= 1
            whole.end, whole.flow = end, None
            spans.insert(j + 1, tail)
    raise SystemExit('the function table did not settle: jumps keep landing inside functions')


def build(project):
    """The rows of the function table, in address order."""
    image = project.image
    settings = project.config.get('inventory', {})
    known = {int(a, 16) for a in settings.get('known_starts', [])}
    not_starts = {int(a, 16) for a in settings.get('not_starts', [])}
    data_ranges = [(int(r['start'], 16), int(r['end'], 16)) for r in settings.get('data_ranges', [])]

    def in_data(va):
        return any(start <= va < end for start, end in data_ranges)

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    sections = [image.section(name) for name in project.code_sections]

    def inside(section, va):
        return section.va <= va < section.va + section.vsize

    # Discovery works one section at a time and sees only that section's own
    # calls and tables. What it cannot see is added and the affected sections
    # are done again, until nothing changes: a function called only from
    # another section; an unwind funclet, reached only through an exception
    # table. A catch block, which the same tables show is not a function, is
    # joined to its function afterwards (see merged).
    seeds = {s.name: {va for va in known if inside(s, va) and not in_data(va)} for s in sections}
    found = {}
    pending = {s.name for s in sections}
    handlers, parents = set(), {}
    for _ in range(8):
        for s in sections:
            if s.name in pending:
                found[s.name] = discover(image, seeds=seeds[s.name], text=s, not_starts=frozenset(not_starts))
        pending = set()
        handlers, parents, catches = exception_handling(image, sections, found)
        targets = {t for fns in found.values() for fn in fns.values() for t in fn.calls | fn.tail_jumps}
        targets |= set(parents)
        for s in sections:
            new = {t for t in targets if inside(s, t) and t not in found[s.name] and t not in seeds[s.name]
                   and t not in not_starts and not in_data(t)}
            if new:
                seeds[s.name] |= new
                pending.add(s.name)
        if not pending:
            break
    else:
        raise SystemExit('function discovery did not settle')

    rows = []
    refined = {name: refine(md, image, merged(found[name], not_starts | catches)) for name in project.code_sections}
    all_starts = {fn.start for spans in refined.values() for fn in spans}
    for name in project.code_sections:
        for fn in refined[name]:
            if in_data(fn.start):
                continue
            size = fn.end - fn.start
            targets = {t for t in fn.flow[0] | fn.flow[1] if t in all_starts}
            owner = project.section_owners.get(name)
            conv = ltcg_probe.entry_register_args(md, image.read(fn.start, min(size, 200)), fn.start)
            eh = fn.start in parents
            rows.append(dict(addr=f'{fn.start:08x}', size=str(size), owner=owner or 'game',
                             kind='eh' if eh else 'func', section=name,
                             conv='' if eh else ' '.join(sorted(conv)),
                             parent=' '.join(f'{p:08x}' for p in sorted(parents[fn.start])) if eh else '',
                             calls=' '.join(f'{c:08x}' for c in sorted(targets)),
                             _addr=fn.start))
    for start, end, owner in project.owner_ranges:
        for row in rows:
            if start <= row['_addr'] < end:
                row['owner'] = owner
    rows.sort(key=lambda r: r['_addr'])
    seen = set()
    for row in rows:
        if row['addr'] in seen:
            raise SystemExit(f"two rows for {row['addr']} (a function found in two code sections)")
        seen.add(row['addr'])
    return rows, handlers


def render(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, COLUMNS, lineterminator='\n', extrasaction='ignore')
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def summary(rows, handlers=()):
    code = Counter()
    count = Counter()
    for r in rows:
        key = r['owner'] if r['kind'] == 'func' else f"{r['owner']} ({r['kind']})"
        code[key] += int(r['size'])
        count[key] += 1
    lines = [f'{len(rows)} functions, {sum(code.values())} bytes']
    lines += [f'  {k:<24} {count[k]:6} functions {code[k]:9} bytes' for k in sorted(code)]
    custom = sum(1 for r in rows if r['conv'] and r['kind'] == 'func')
    lines.append(f'  reading eax/ebx/esi/edi at entry: {custom}')
    if handlers:
        lines.append('  frame handlers: ' + ' '.join(f'{h:08x}' for h in sorted(handlers)))
    return '\n'.join(lines)


def main(argv=None):
    from . import project as project_module
    ap = argparse.ArgumentParser(prog='xbx inventory', description=__doc__.splitlines()[0])
    ap.add_argument('--out', help='where to write (default: the project\'s function table)')
    ap.add_argument('--check', action='store_true',
                    help='write nothing; fail unless the committed table is what the XBE gives')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
        rows, handlers = build(project)
    except project_module.ProjectError as e:
        sys.exit(f'error: {e}')
    text = render(rows)
    out = args.out or project.functions_csv
    if args.check:
        current = open(out, encoding='utf-8').read() if os.path.exists(out) else ''
        if current != text:
            sys.exit(f'{project.relative(out)} is not what the XBE gives: run xbx inventory and review the difference')
        print(f'{project.relative(out)} is current')
    else:
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        with open(out, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
    print(summary(rows, handlers))


if __name__ == '__main__':
    main()
