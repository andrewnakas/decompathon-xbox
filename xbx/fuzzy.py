"""The similarity score of a function that does not match yet: 0 to 100.

Both sides are read as instructions; any operand that is an address in the
image, and any relocated field of ours, is spelled ADDR, and a branch inside
the function is spelled by its distance. The score is the share of
instructions that line up (difflib's ratio over the two instruction lists).
It only ranks unfinished work. A score never makes a function count as matched:
that takes the match gate, which compares bytes.
"""
import difflib
import re

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

_HEX = re.compile(r'0x[0-9a-f]+')
_MD = Cs(CS_ARCH_X86, CS_MODE_32)


def instructions(data, addr, low, mask=()):
    """Normalised instruction texts of data loaded at addr. mask: offsets of relocation bytes."""
    data = bytearray(data)
    for o in mask:
        if o < len(data):
            data[o] = 0x7F                 # 0x7f7f7f7f: outside any image, so it reads as ADDR
    while data and data[-1] in (0xCC, 0x90):
        data.pop()
    size = len(data)
    out = []
    for i in _MD.disasm(bytes(data), addr):
        ops = i.op_str
        if i.mnemonic.startswith(('j', 'call', 'loop')) and '[' not in ops and ops[:1].isdigit():
            v = int(ops, 0)
            ops = f'{v - i.address:+d}' if addr <= v < addr + size else 'ADDR'
        else:
            ops = _HEX.sub(lambda m: 'ADDR' if int(m.group(0), 16) >= low else m.group(0), ops)
        out.append(f'{i.mnemonic} {ops}')
    return out


def score(ours, mask, retail, addr, low=0x10000):
    """0.0 to 100.0; 100.0 only when every instruction lines up."""
    if not ours or not retail:
        return 0.0
    x = instructions(retail, addr, low)
    y = instructions(ours, addr, low, mask)
    if not x or not y:
        return 0.0
    return round(100.0 * difflib.SequenceMatcher(None, x, y, autojunk=False).ratio(), 1)
