"""The metric vector, the diagnosis and the ledger, on fabricated Verdict-like
objects built from hand-assembled bytes. No game file, no compiler."""
import struct
import types

import pytest

from xbx.workbench import classify, ledger, metrics

FLOOR = 0x10000
BASE = 0x11070
CALL_FIELD = frozenset(range(8, 12))


def call(at, target):
    return b'\xe8' + struct.pack('<i', target - (at + 5))


# retail:  push esi / mov esi, ecx / test esi, esi / je +5 / call 0x1ba8a0 / pop esi / ret
RETAIL = bytes.fromhex('568bf185f67405') + call(BASE + 7, 0x1BA8A0) + bytes.fromhex('5ec3')
# ours as an object holds it: the call's displacement is a relocation, still zero
OURS = bytes.fromhex('568bf185f67405e8000000005ec3')


def verdict(ours, retail=RETAIL, ok=False, mask=CALL_FIELD, first=-1, defines='', problems=()):
    """What metrics and classify read of a match.Verdict."""
    return types.SimpleNamespace(ok=ok, addr=BASE, ours=ours, mask=mask, retail=retail, first=first,
                                 defines=defines, problems=list(problems))


# ------------------------------------------------------------------ vector

def test_a_match_is_the_verdict_s_word_not_a_byte_compare():
    assert metrics.vector(verdict(OURS, ok=True), FLOOR) == dict(match=1, lock=0, dist=0, sem=0, frame=0, first=-1)
    # the same bytes with a verdict that is not ok (say the call resolves to another function):
    # every number is 0 and it still is not a match
    v = metrics.vector(verdict(OURS, ok=False, first=8), FLOOR)
    assert v == dict(match=0, lock=0, dist=0, sem=0, frame=0, first=8)


def test_one_different_instruction():
    ours = OURS[:5] + b'\x7c' + OURS[6:]                       # jl where retail has je
    v = metrics.vector(verdict(ours, first=5), FLOOR)
    assert v == dict(match=0, lock=1, dist=1, sem=2, frame=0, first=5)


def test_register_choice_is_distance_but_not_semantics():
    # retail keeps `this` in esi, ours in edi: same operations, other register
    retail = bytes.fromhex('8bf1' '8b4604' 'c3')               # mov esi, ecx; mov eax, [esi+4]; ret
    ours = bytes.fromhex('8bf9' '8b4704' 'c3')                 # mov edi, ecx; mov eax, [edi+4]; ret
    v = metrics.vector(verdict(ours, retail, mask=frozenset()), FLOOR)
    assert (v['match'], v['lock'], v['dist'], v['sem'], v['first']) == (0, 2, 2, 0, 0)


def test_an_insertion_costs_one_in_dist_and_everything_after_it_in_lock():
    retail = bytes.fromhex('568bf1' '8b4604' '8b4e08' '5ec3')            # push esi; mov esi,ecx; 2 loads; pop; ret
    ours = bytes.fromhex('568bf1' '33d2' '8b4604' '8b4e08' '5ec3')       # + xor edx, edx after the prologue
    v = metrics.vector(verdict(ours, retail, mask=frozenset()), FLOOR)
    assert v['dist'] == 1 and v['sem'] == 1
    assert v['first'] == 3                                     # where retail's listing stops agreeing
    assert v['lock'] >= 4                                      # every instruction after it, plus 2 bytes of length


def test_frame_is_ours_minus_retail_and_not_counted_in_sem():
    retail = bytes.fromhex('83ec08' '8b442410' '83c408' 'c3')  # sub esp, 8; mov eax, [esp+0x10]; add esp, 8; ret
    ours = bytes.fromhex('83ec10' '8b442418' '83c410' 'c3')    # sub esp, 0x10; mov eax, [esp+0x18]; ...
    v = metrics.vector(verdict(ours, retail, mask=frozenset()), FLOOR)
    assert v['frame'] == 8
    assert v['sem'] == 0                                       # stack slots and the frame adjustment are masked
    assert v['dist'] == 3 and v['lock'] == 3


def test_padding_after_ours_and_bytes_after_retail_are_not_the_function():
    ours = OURS[:5] + b'\x7c' + OURS[6:] + b'\xcc\xcc'
    v = metrics.vector(verdict(ours, RETAIL + b'\xcc\xcc', first=5), FLOOR, size=len(RETAIL))
    assert v == dict(match=0, lock=1, dist=1, sem=2, frame=0, first=5)


def test_shorter_ours_counts_the_missing_bytes():
    ours = OURS[:13]                                           # no ret
    v = metrics.vector(verdict(ours, first=13), FLOOR)
    assert v['dist'] == 1 and v['sem'] == 1 and v['first'] == 13
    assert v['lock'] == 1 + 1                                  # the instruction retail has alone, and its byte


def test_key_orders_match_then_dist_then_sem_then_lock():
    vs = [dict(match=0, lock=1, dist=4, sem=0),
          dict(match=0, lock=9, dist=2, sem=3),
          dict(match=0, lock=3, dist=2, sem=3),
          dict(match=0, lock=50, dist=2, sem=1),
          dict(error='does not compile'),
          dict(match=1, lock=0, dist=0, sem=0)]
    order = sorted(vs, key=metrics.key)
    assert order[0]['match'] == 1
    assert [(v['dist'], v['sem'], v['lock']) for v in order[1:5]] == [(2, 1, 50), (2, 3, 3), (2, 3, 9), (4, 0, 1)]
    assert 'error' in order[-1]


# ------------------------------------------------------------------ diagnosis

def test_calling_convention_from_the_decorated_name():
    cc = classify.calling_convention
    assert cc('?func_00011070@Node@@QAEXXZ') == 'thiscall'     # public member
    assert cc('?f@C@@UAEXXZ') == 'thiscall'                    # virtual member
    assert cc('?f@C@@QBEHXZ') == 'thiscall'                    # const member
    assert cc('??0Foo@@QAE@XZ') == 'thiscall'                  # constructor
    assert cc('?f@C@@SAXXZ') == 'cdecl'                        # static member
    assert cc('?func_001BA8A0@@YAXXZ') == 'cdecl'
    assert cc('?f@@YGXH@Z') == 'stdcall'
    assert cc('?f@@YIXHH@Z') == 'fastcall'
    assert cc('_func_0012A340@8') == 'stdcall'
    assert cc('@func_0012A340@8') == 'fastcall'
    assert cc('_main') == 'cdecl'
    assert cc('?data_001EFC88@@3HA') is None                   # a variable
    assert cc('') is None


def test_diagnose_a_member_function_takes_ecx_on_both_sides():
    ours = OURS[:5] + b'\x7c' + OURS[6:]
    d = classify.diagnose(verdict(ours, first=5, defines='?func_00011070@Node@@QAEXXZ'), FLOOR,
                          dict(_size=len(RETAIL), conv=''))
    assert d['classes'] == ['semantic']
    assert d['convention'] == []                               # ecx is `this`: not a custom convention
    assert d['our_convention'] == 'thiscall'
    assert d['missing'] == [('je L', 1)] and d['extra'] == [('jl L', 1)]
    assert d['frame'] == (0, 0)
    lines = classify.side_by_side(d)
    marked = [l for l in lines if l.startswith('!!')]
    assert len(marked) == 1 and 'je Lc' in marked[0] and 'jl Lc' in marked[0]
    assert marked[0].index('je Lc') < marked[0].index('|') < marked[0].index('jl Lc')   # retail on the left


def test_diagnose_reports_registers_of_the_row_s_convention_ours_does_not_take():
    # retail reads esi at entry (the row's conv column says so); ours is a plain cdecl function reading the stack
    retail = bytes.fromhex('8b4604' 'c3')                      # mov eax, [esi+4]; ret
    ours = bytes.fromhex('8b442404' '8b4004' 'c3')             # mov eax, [esp+4]; mov eax, [eax+4]; ret
    d = classify.diagnose(verdict(ours, retail, mask=frozenset(), defines='?func_00011070@@YAHPAX@Z'), FLOOR,
                          dict(_size=len(retail), conv='esi'))
    assert 'convention' in d['classes']
    assert d['convention'] == ['esi']
    assert 'custom' in d['hint_keys']


def test_diagnose_ecx_read_by_retail_and_a_free_function_of_ours():
    # retail takes ecx; ours is declared as a free cdecl function: the observation is "a member function?"
    retail = bytes.fromhex('8b4104' 'c3')                      # mov eax, [ecx+4]; ret
    ours = bytes.fromhex('8b442404' '8b4004' 'c3')
    d = classify.diagnose(verdict(ours, retail, mask=frozenset(), defines='?func_00011070@@YAHPAX@Z'), FLOOR,
                          dict(_size=len(retail), conv=''))
    assert d['convention'] == ['ecx']
    assert 'this' in d['hint_keys']
    assert not any('0x' in h and 'see ' in h for h in d['hints'])


def test_diagnose_register_renaming():
    retail = bytes.fromhex('56' '8bf1' '8b4604' '5e' 'c3')     # push esi; mov esi, ecx; mov eax, [esi+4]; pop esi; ret
    ours = bytes.fromhex('57' '8bf9' '8b4704' '5f' 'c3')       # the same through edi
    d = classify.diagnose(verdict(ours, retail, mask=frozenset(), defines='?f@C@@QAEHXZ'), FLOOR,
                          dict(_size=len(retail), conv=''))
    assert d['classes'] == ['regs']
    assert d['reg_map']['esi'][0][0] == 'edi'


# ------------------------------------------------------------------ ledger

@pytest.fixture
def book(tmp_path):
    ledger.use(str(tmp_path / 'ledger' / 'xbx.db'))
    yield ledger
    ledger.use(None)


def test_ledger_round_trip(book):
    h = book.digest('int f() { return 1; }\n', 'salt')
    assert h != book.digest('int f() { return 1; }\n', 'other salt') and len(h) == 16
    assert book.get(BASE, h) is None
    v = dict(match=0, lock=3, dist=2, sem=1, frame=-4, first=0x1c, problems=['differs at +0x1c', 'second'])
    book.put(BASE, h, v, origin='try', note='a.txt#0')
    assert book.get(BASE, h) == v
    book.put(BASE, 'e' * 16, dict(error='x.cpp(3) : error C2065: undeclared', problems=['does not compile']))
    got = book.get(BASE, 'e' * 16)
    assert got['error'].endswith('undeclared') and 'match' not in got


def test_ledger_history_lists_what_compiled_best_first(book):
    book.put(BASE, 'a' * 16, dict(match=0, lock=5, dist=4, sem=2, frame=0, first=3), origin='file', note='far')
    book.put(BASE, 'b' * 16, dict(match=0, lock=9, dist=1, sem=2, frame=0, first=3), origin='try', note='near')
    book.put(BASE, 'c' * 16, dict(match=1, lock=0, dist=0, sem=0, frame=0, first=-1), origin='search:x', note='hit',
             path='/somewhere/best_0001.cpp')
    book.put(BASE, 'd' * 16, dict(error='no'), origin='try')
    book.put(BASE + 0x100, 'f' * 16, dict(match=1, lock=0, dist=0, sem=0, frame=0, first=-1))
    rows = book.history(BASE)
    assert [r[2] for r in rows] == ['hit', 'near', 'far']
    assert rows[0][3] == 1 and rows[0][8] == '/somewhere/best_0001.cpp'
    book.set_path(BASE, 'b' * 16, '/somewhere/v_b.cpp')
    assert book.history(BASE, 2)[1][8] == '/somewhere/v_b.cpp'


def test_ledger_facts(book):
    assert book.fact(BASE, 'classes') is None
    book.set_fact(BASE, 'classes', ['regs', 'stack'])
    assert book.fact(BASE, 'classes') == ['regs', 'stack']
    assert book.fact(BASE + 4, 'classes', []) == []
