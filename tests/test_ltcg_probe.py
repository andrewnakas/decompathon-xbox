"""Which registers a function reads at entry: the idioms that only look like reads."""
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

from xbx import ltcg_probe


def reads(code):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    return ltcg_probe.entry_register_args(md, bytes.fromhex(code), 0x1000)


def test_a_real_read_is_reported():
    assert reads('8b4804c3') == {'eax'}             # mov ecx, [eax+4]; ret
    assert reads('03c1c3') == {'eax'}               # add eax, ecx; ret


def test_results_that_do_not_depend_on_the_register_are_writes():
    assert reads('33c0c3') == set()                 # xor eax, eax
    assert reads('2bc0c3') == set()                 # sub eax, eax
    assert reads('3bca1bc0f7d8c3') == set()         # cmp ecx, edx; sbb eax, eax; neg eax
    assert reads('83c8ffc3') == set()               # or eax, -1
    assert reads('83e000c3') == set()               # and eax, 0


def test_a_mixed_sbb_still_reads():
    assert reads('1bc1c3') == {'eax'}               # sbb eax, ecx


def test_a_register_moved_onto_itself_is_a_no_op():
    assert reads('8bffc3') == set()                 # mov edi, edi; ret
    assert reads('8bff8b4704c3') == {'edi'}         # mov edi, edi; mov eax, [edi+4]: the second is a read
