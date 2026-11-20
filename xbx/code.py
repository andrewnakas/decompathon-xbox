"""Reading a function's bytes as instructions: where its switch tables begin,
and where its calls and jumps go."""
from capstone import x86 as cs_x86


def table_start(md, data, addr):
    """(end, reached): the offset where a function's instructions end and its
    switch tables begin, and the offset decoding got to. They are equal when
    every byte before the tables decoded. md needs detail on."""
    end, off = len(data), 0
    while off < end:
        ins = next(md.disasm(data[off:off + 16], addr + off, count=1), None)
        if ins is None:
            return end, off
        for op in ins.operands:
            # `jmp [table + reg*4]`, and the byte table `movzx reg, byte [table + reg]` before it
            if op.type == cs_x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index and op.mem.disp:
                t = (op.mem.disp & 0xFFFFFFFF) - addr
                if off < t < end:
                    end = t
        off += ins.size
    return end, end


def branches(md, data, addr):
    """(calls, jumps, complete): the targets of the direct calls, and of the
    relative jumps that leave [addr, addr + len(data)); complete is False when
    the bytes did not all decode."""
    end, reached = table_start(md, data, addr)
    calls, jumps = set(), set()
    for ins in md.disasm(data[:end], addr):
        if cs_x86.X86_GRP_BRANCH_RELATIVE not in ins.groups and ins.mnemonic != 'call':
            continue
        op = ins.operands[0] if ins.operands else None
        if op is None or op.type != cs_x86.X86_OP_IMM:
            continue
        target = op.imm & 0xFFFFFFFF
        if ins.mnemonic == 'call':
            calls.add(target)
        elif not addr <= target < addr + len(data):
            jumps.add(target)
    return calls, jumps, reached == end
