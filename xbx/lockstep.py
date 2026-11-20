"""Lockstep instruction comparison: both sides are decoded from offset 0 and
compared instruction by instruction, with no alignment. One instruction
inserted early therefore shifts, and counts, everything after it.

    differing_instructions(ours, ours_va, theirs, theirs_va, masked) -> (count, lines, complete)

Derived from tools/check.py of the halo2-decompiled project (GPL-3.0-or-later).
"""
from capstone import CS_ARCH_X86, CS_MODE_32, Cs


def _dword(data, k, signed=False):
    return int.from_bytes(data[k:k + 4], 'little', signed=signed)


def _text(ins):
    return f'{ins.mnemonic} {ins.op_str}' if ins else ''


def _decode(md, data, va, k):
    """The instruction at offset k of data, or None past the end or where nothing decodes."""
    return next(md.disasm(data[k:k + 16], va + k, count=1), None)


def differing_instructions(ours, ours_va, theirs, theirs_va, masked, forced=frozenset(), entries=frozenset(),
                           decoded=None):
    """Returns (count, lines, complete). complete is False when the
    disassembly does not cover every byte of both sides.

    masked: offsets in ours whose bytes are not compared (address fields).
    forced: offsets that count as different whatever their bytes are.
    entries: the offsets of absolute fields that point inside the function on
    both sides. No instruction starts with its own address field, so where
    both sides reach one of these at the same offset the bytes there are a
    jump table entry: that dword is compared as one line, by the same rule as
    the instructions, and decoding resumes after it. Entries that differ only
    by layout are masked, so they do not count, and the instructions after a
    table line up as they do in the image.
    decoded: our bytes as the disassembler reads them, when they differ from
    ours (the comparisons always use ours)."""
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    lines, count = [], 0
    oa = ob = 0
    stuck_a = stuck_b = False

    def same_bytes(k, size, y_bytes):
        return all((k + i) not in forced and ((k + i) in masked or ours[k + i] == y_bytes[i]) for i in range(size))

    while True:
        if not (stuck_a or stuck_b) and oa == ob and oa in entries and oa + 4 <= min(len(ours), len(theirs)):
            same = same_bytes(oa, 4, theirs[ob:ob + 4])
            x_text, y_text = f'dd {_dword(ours, oa):#x}', f'dd {_dword(theirs, ob):#x}'
            oa += 4
            ob += 4
        else:
            x = None if stuck_a else _decode(md, decoded or ours, ours_va, oa)
            y = None if stuck_b else _decode(md, theirs, theirs_va, ob)
            stuck_a, stuck_b = x is None, y is None
            if x is None and y is None:
                break
            same = x is not None and y is not None and x.size == y.size and same_bytes(oa, x.size, y.bytes)
            x_text, y_text = _text(x), _text(y)
            oa += x.size if x else 0
            ob += y.size if y else 0
        count += not same
        lines.append(f'  {"  " if same else "!!"} '
                     f'{x_text:<44} | {y_text}')
    complete = oa == len(ours) and ob == len(theirs)
    return count, lines, complete
