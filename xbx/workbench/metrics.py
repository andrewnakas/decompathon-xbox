"""Every variant gets a vector, not one number.

match  1 only when the unit is a proven match: match.check's Verdict is ok
       (every byte equal and every relocation resolved against retail), not a
       byte compare with the address fields masked
lock   lockstep count (xbx.lockstep) plus the length difference in bytes
dist   aligned instruction distance (difflib), registers kept
sem    multiset distance with registers/stack/labels masked (order ignored)
frame  ours - retail prologue `sub esp` bytes
first  retail offset of the first aligned difference; when the listings align
       everywhere, the Verdict's first differing byte (-1 if none)

vector() takes a Verdict or anything with its fields: ok, addr, ours (our
function's bytes), mask (offsets of relocation bytes in ours), retail, first.
"""
import difflib

from .. import lockstep
from . import asm


def aligned(x, y):
    """Opcodes aligning retail instructions x with ours y (texts at level reloc)."""
    sm = difflib.SequenceMatcher(None, [i.text for i in x], [i.text for i in y], autojunk=False)
    return sm.get_opcodes()


def retail_bytes(verdict, size=None):
    """Retail's function: the Verdict holds max(table size, our size) bytes, the
    function is the first `size` (its row in the function table)."""
    return bytes(verdict.retail if size is None else verdict.retail[:size])


def vector(verdict, floor, size=None):
    """floor: the image's base address (asm.decode). size: the function's size in
    the function table, when the Verdict's retail bytes run past it."""
    if verdict.ok:
        return dict(match=1, lock=0, dist=0, sem=0, frame=0, first=-1)
    data, masked, addr = verdict.ours, verdict.mask, verdict.addr
    theirs = retail_bytes(verdict, size)
    b = asm.strip_pad(data)
    count, _, _ = lockstep.differing_instructions(b, 0, theirs, addr, {o for o in masked if o < len(b)})
    x, y = asm.decode(theirs, addr, floor=floor), asm.ours(data, masked, floor)
    ops = aligned(x, y)
    dist = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in ops if op != 'equal')
    first = next((x[i1].off if i1 < len(x) else len(theirs) for op, i1, i2, j1, j2 in ops if op != 'equal'), -1)
    if first < 0:
        first = getattr(verdict, 'first', -1)          # same listing: a wrong target, encoding or constant block
    a, c = asm.bag(x), asm.bag(y)
    sem = sum((a - c).values()) + sum((c - a).values())
    return dict(match=0, lock=count + abs(len(b) - len(theirs)), dist=dist, sem=sem,
                frame=asm.frame(y) - asm.frame(x), first=first)


def key(v):
    """Search order: a match first, then aligned distance, then semantic, then lockstep."""
    return (0 if v.get('match') else 1, v.get('dist', 10 ** 6), v.get('sem', 10 ** 6), v.get('lock', 10 ** 6))
