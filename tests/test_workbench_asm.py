"""The workbench's disassembler/normaliser and the lockstep comparison, on
hand-assembled byte strings. No game file, no compiler."""
import struct

from xbx import lockstep
from xbx.workbench import asm

FLOOR = 0x10000          # an image base: numbers at or above it read as addresses
BASE = 0x11070           # where the "retail" function is loaded


def texts(data, base=BASE, **kw):
    return [i.text for i in asm.decode(data, base, floor=FLOOR, **kw)]


def call(at, target):
    """e8 rel32 for a call placed at address `at`."""
    return b'\xe8' + struct.pack('<i', target - (at + 5))


# push esi / mov esi, ecx / test esi, esi / je +5 / call 0x1ba8a0 / pop esi / ret
RETAIL = bytes.fromhex('568bf185f67405') + call(BASE + 7, 0x1BA8A0) + bytes.fromhex('5ec3')


# ------------------------------------------------------------------ padding and masks

def test_strip_pad_drops_trailing_int3_and_nop_only():
    assert asm.strip_pad(b'\x90\xc3\xcc\xcc\x90') == b'\x90\xc3'
    assert asm.strip_pad(b'\xcc\xcc') == b''
    assert asm.strip_pad(b'') == b''


def test_apply_mask_overwrites_fields_and_ignores_offsets_past_the_end():
    assert asm.apply_mask(b'\xe8\x00\x00\x00\x00\xc3', {1, 2, 3, 4, 99}) == b'\xe8\x7f\x7f\x7f\x7f\xc3'


# ------------------------------------------------------------------ decode: level 'reloc'

def test_decode_offsets_sizes_and_branch_targets():
    ins = asm.decode(RETAIL, BASE, floor=FLOOR)
    assert [(i.off, i.size) for i in ins] == [(0, 1), (1, 2), (3, 2), (5, 2), (7, 5), (12, 1), (13, 1)]
    assert [i.text for i in ins] == ['push esi', 'mov esi, ecx', 'test esi, esi', 'je Lc', 'call ADDR',
                                     'pop esi', 'ret']
    assert ins[3].mnem == 'je' and ins[3].ops == 'Lc'


def test_branch_out_of_the_function_is_an_address():
    # jmp to 0x20000 from a 5-byte function: a tail call, not a label
    data = b'\xe9' + struct.pack('<i', 0x20000 - (BASE + 5))
    assert texts(data) == ['jmp ADDR']


def test_a_recursive_call_reads_the_same_on_both_sides():
    # retail: call to the function's own start; ours: the same call, its field an unresolved relocation
    retail = bytes.fromhex('85c97405') + call(BASE + 4, BASE) + b'\xc3'       # test ecx, ecx; je +5; call self; ret
    ours = bytes.fromhex('85c97405e800000000c3')
    assert texts(retail) == ['test ecx, ecx', 'je L9', 'call ADDR', 'ret']
    assert [i.text for i in asm.ours(ours, frozenset(range(5, 9)), FLOOR)] == texts(retail)
    # a jump back to the start is a loop, and stays a label
    assert texts(bytes.fromhex('85c975fcc3')) == ['test ecx, ecx', 'jne L0', 'ret']


def test_numbers_are_addresses_from_the_image_base_up():
    mov_abs = bytes.fromhex('a100f01e00')              # mov eax, dword ptr [0x1ef000]
    mov_imm = bytes.fromhex('b834120000')              # mov eax, 0x1234
    assert texts(mov_abs) == ['mov eax, dword ptr [ADDR]']
    assert texts(mov_imm) == ['mov eax, 0x1234']
    # the cutoff is the caller's, not a constant: with a lower base the immediate is an address too
    assert [i.text for i in asm.decode(mov_imm, BASE, floor=0x1000)] == ['mov eax, ADDR']
    assert [i.text for i in asm.decode(mov_abs, BASE, floor=0x400000)] == ['mov eax, dword ptr [0x1ef000]']


def test_decode_stops_at_size():
    assert texts(RETAIL, size=5) == ['push esi', 'mov esi, ecx', 'test esi, esi']


def test_ours_masks_relocations_and_strips_padding():
    # our object: the call's displacement is an unresolved relocation (zeros), then int3 padding
    ours = bytes.fromhex('568bf185f67405e8000000005ec3cccc')
    got = asm.ours(ours, frozenset(range(8, 12)), FLOOR)
    assert [i.text for i in got] == ['push esi', 'mov esi, ecx', 'test esi, esi', 'je Lc', 'call ADDR',
                                     'pop esi', 'ret']
    # an absolute field of ours reads as an address once masked, whatever the object holds there
    got = asm.ours(bytes.fromhex('a100000000c3'), frozenset(range(1, 5)), FLOOR)
    assert [i.text for i in got] == ['mov eax, dword ptr [ADDR]', 'ret']


# ------------------------------------------------------------------ mask levels

def test_mask_levels():
    t = 'mov eax, dword ptr [esp + 8]'
    assert asm.mask(t, 'reloc') == t
    assert asm.mask(t, 'regs') == 'mov R, dword ptr [esp + 8]'
    assert asm.mask(t, 'stack') == 'mov R, dword ptr [S]'
    assert asm.mask(t, 'loose') == 'mov R, dword ptr [S]'

    t = 'movss xmm1, dword ptr [ebp - 8]'
    assert asm.mask(t, 'regs') == 'movss X, dword ptr [R - 8]'
    assert asm.mask(t, 'stack') == 'movss X, dword ptr [S]'

    t = 'mov al, byte ptr [ecx + 0x14]'
    assert asm.mask(t, 'regs') == 'mov Rb, byte ptr [R + 0x14]'
    assert asm.mask(t, 'stack') == 'mov Rb, byte ptr [R + 0x14]'
    assert asm.mask(t, 'loose') == 'mov Rb, byte ptr [R + N]'

    assert asm.mask('mov ax, cx', 'regs') == 'mov Rw, Rw'
    assert asm.mask('je L1c', 'regs') == 'je L'
    assert asm.mask('add esi, 4', 'loose') == 'add R, N'
    assert asm.mask('call ADDR', 'loose') == 'call ADDR'


def test_registers_of_different_width_stay_distinct():
    assert len({asm.mask(t, 'loose') for t in ('mov eax, 1', 'mov ax, 1', 'mov al, 1')}) == 3


# ------------------------------------------------------------------ bag and frame

def test_bag_ignores_register_moves_and_frame_adjustment():
    # sub esp, 0x10 / mov esi, ecx / mov eax, [esi+4] / mov edx, [edi+4] / add esp, 0x10 / ret
    data = bytes.fromhex('83ec10' '8bf1' '8b4604' '8b5704' '83c410' 'c3')
    ins = asm.decode(data, BASE, floor=FLOOR)
    assert asm.bag(ins) == {'mov R, dword ptr [R + 4]': 2, 'ret': 1}
    assert asm.bag(ins, 'loose') == {'mov R, dword ptr [R + N]': 2, 'ret': 1}


def test_bag_is_the_same_for_a_register_renaming():
    a = asm.decode(bytes.fromhex('8b4604' '03c3' 'c3'), BASE, floor=FLOOR)     # mov eax,[esi+4]; add eax,ebx
    b = asm.decode(bytes.fromhex('8b4f04' '03ca' 'c3'), BASE, floor=FLOOR)     # mov ecx,[edi+4]; add ecx,edx
    assert [i.text for i in a] != [i.text for i in b]
    assert asm.bag(a) == asm.bag(b)


def test_frame():
    assert asm.frame(asm.decode(bytes.fromhex('5583ec10c3'), BASE, floor=FLOOR)) == 0x10   # push ebp; sub esp, 0x10
    assert asm.frame(asm.decode(bytes.fromhex('81ec00010000c3'), BASE, floor=FLOOR)) == 0x100
    assert asm.frame(asm.decode(RETAIL, BASE, floor=FLOOR)) == 0


# ------------------------------------------------------------------ registers read at entry

def test_live_in():
    assert asm.live_in(bytes.fromhex('568bf1c3')) == {'ecx'}                 # push esi; mov esi, ecx
    assert asm.live_in(bytes.fromhex('33c0c3')) == set()                     # xor eax, eax
    assert asm.live_in(bytes.fromhex('8b442404c3')) == set()                 # mov eax, [esp+4]
    assert asm.live_in(bytes.fromhex('03c3c3')) == {'eax', 'ebx'}            # add eax, ebx
    assert asm.live_in(bytes.fromhex('8ac2c3')) == {'edx'}                   # mov al, dl
    assert asm.live_in(bytes.fromhex('8bc08bc1c3')) == {'ecx'}               # mov eax, eax (nop); mov eax, ecx


def test_live_in_stops_at_the_first_branch():
    # test ecx, ecx / je +2 / mov eax, ebx / ret: ebx is read only after the branch
    assert asm.live_in(bytes.fromhex('85c974028bc3c3')) == {'ecx'}


def test_live_in_push_is_a_save_only_for_the_registers_named():
    push_ecx = bytes.fromhex('5133c0c3')                                     # push ecx; xor eax, eax
    assert asm.live_in(push_ecx) == {'ecx'}                                  # an argument pushed
    assert asm.live_in(push_ecx, saves=asm.GPR) == set()                     # room for a local


# ------------------------------------------------------------------ lockstep

def test_lockstep_identical():
    count, lines, complete = lockstep.differing_instructions(RETAIL, 0, RETAIL, BASE, set())
    assert (count, complete) == (0, True)
    assert len(lines) == 7 and not any('!!' in l for l in lines)


def test_lockstep_counts_one_differing_instruction():
    ours = RETAIL[:5] + b'\x7c' + RETAIL[6:]                                 # jl where retail has je
    count, lines, complete = lockstep.differing_instructions(ours, BASE, RETAIL, BASE, set())
    assert (count, complete) == (1, True)
    assert [l for l in lines if '!!' in l][0].split('|')[0].split()[1] == 'jl'


def test_lockstep_skips_masked_bytes():
    ours = bytes.fromhex('568bf185f67405e8000000005ec3')
    assert lockstep.differing_instructions(ours, 0, RETAIL, BASE, set())[0] == 1
    assert lockstep.differing_instructions(ours, 0, RETAIL, BASE, set(range(8, 12)))[0] == 0
    # the opcode byte is not an address field: masking the field does not hide a different opcode
    jmp = ours[:7] + b'\xe9' + ours[8:]
    assert lockstep.differing_instructions(jmp, 0, RETAIL, BASE, set(range(8, 12)))[0] == 1


def test_lockstep_forced_offsets_count_whatever_the_bytes():
    count, _, _ = lockstep.differing_instructions(RETAIL, BASE, RETAIL, BASE, set(), forced={8})
    assert count == 1


def test_lockstep_does_not_realign_after_an_insertion():
    theirs = bytes.fromhex('568bf1c3')                                       # push esi; mov esi, ecx; ret
    ours = b'\x90' + theirs                                                  # one nop first
    count, lines, complete = lockstep.differing_instructions(ours, 0, theirs, BASE, set())
    assert count == 4 and complete                                           # every line, and ours' extra one
    assert len(lines) == 4


def test_lockstep_reports_bytes_nothing_decodes_from():
    # ours ends in the middle of an instruction (a call missing its displacement)
    count, _, complete = lockstep.differing_instructions(b'\x56\xe8\x00', 0, b'\x56\xc3', BASE, set())
    assert count == 1 and not complete


def test_lockstep_jump_table_entries_are_compared_as_dwords():
    # ret, then a two-entry table of absolute addresses into the function, on both sides
    theirs = b'\xc3' + struct.pack('<II', BASE, BASE)
    ours = b'\xc3' + struct.pack('<II', 0, 0)
    masked = set(range(1, 9))
    count, lines, complete = lockstep.differing_instructions(ours, 0, theirs, BASE, masked, entries={1, 5})
    assert (count, complete) == (0, True)
    assert [l.split('|')[1].strip() for l in lines[1:]] == [f'dd {BASE:#x}'] * 2
    # an entry that really differs (not masked) is one differing line
    count, _, _ = lockstep.differing_instructions(ours, 0, theirs, BASE, set(range(1, 5)), entries={1, 5})
    assert count == 1
