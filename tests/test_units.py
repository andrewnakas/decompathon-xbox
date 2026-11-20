from xbx import fuzzy, pad, units
from xbx.gates import scan


def test_placeholders_in_every_decoration():
    for name, kind, addr in (('?func_0012A340@Foo@@QAEXH@Z', 'func', 0x12A340), ('_func_0012A340@8', 'func', 0x12A340),
                             ('@func_0012A340@8', 'func', 0x12A340), ('_func_0012A340', 'func', 0x12A340),
                             ('?data_001EFC88@@3HA', 'data', 0x1EFC88), ('__imp__data_001C3F60', 'data', 0x1C3F60)):
        assert units.placeholder(name) == (kind, addr)
    for name in ('?Update@Foo@@QAEXXZ', '_func_0012A34', '?func_0012A3400@@YAXXZ', '??0func_0012A340@@QAE@XZ',
                 '?my_func_0012A340@@YAXXZ'):
        assert units.placeholder(name) is None


def test_directives():
    text = ('/* cflags: /O1 /Oy- */\n/* sym: ??0Foo@@QAE@XZ = 0x12A340 */\n/* const: 0x20000 */\n'
            '/* fuzzy: 87.5 */\nint x; /* sym: ??1Foo@@QAE@XZ = 0x1 */\n')
    unit = units.parse(text, 'src/nonmatching/func_0012A340.cpp')
    assert unit.addr == 0x12A340 and unit.cflags == ['/O1', '/Oy-']
    assert unit.syms == {'??0Foo@@QAE@XZ': 0x12A340}        # a directive is a line of its own
    assert unit.consts == {0x20000} and unit.fuzzy == 87.5
    assert units.parse('int x;\n/* cflags: /O1 */\n', 'func_00011000.cpp').cflags == []


def test_write_fuzzy(tmp_path):
    path = tmp_path / 'func_00011000.cpp'
    path.write_text('/* cflags: /O1 */\nint x;\n')
    units.write_fuzzy(str(path), 61.26)
    assert path.read_text() == '/* cflags: /O1 */\n/* fuzzy: 61.3 */\nint x;\n'
    units.write_fuzzy(str(path), 70)
    assert path.read_text() == '/* cflags: /O1 */\n/* fuzzy: 70.0 */\nint x;\n'


def test_padding():
    assert pad.is_padding(b'') and pad.is_padding(b'\xcc\xcc\x90')
    assert pad.is_padding(bytes.fromhex('8da42400000000 8bff'))
    assert not pad.is_padding(b'\xc3') and not pad.is_padding(bytes.fromhex('8d49'))


def test_fuzzy():
    a = bytes.fromhex('8b4104 85c0 7402 33c0 c3')
    assert fuzzy.score(a, (), a, 0x11000) == 100.0
    b = bytes.fromhex('8b4108 85c0 7402 33c0 c3')
    assert 0 < fuzzy.score(b, (), a, 0x11000) < 100.0
    assert fuzzy.score(b'', (), a, 0x11000) == 0.0


class _Project:
    allowed_cflags = ['/O1']
    includes = []


def problems(text, tier='matched'):
    return scan.scan_text(_Project(), 'src/funcs/func_00011000.cpp', text, text.encode(), tier)


def test_scan_accepts_ordinary_code():
    text = '''/* cflags: /O1 */
struct Node { virtual void f(); Node *next; int n; void func_00011000(); };
static const char *name = "do { } while (0) and __asm in a string";
void Node::func_00011000()
{
    for (Node *node = this; node; node = node->next) {
        node->n++;   // __asm in a comment
    }
    do { n--; } while (n > 0);
    return;
}
'''
    assert problems(text) == []


def test_scan_refusals():
    cases = {
        'void f() { __asm { nop } }': 'inline assembly',
        '__declspec(naked) void f() {}': 'naked',
        '#pragma optimize("", off)\n': '#pragma',
        '#define A(x) x##y\n': 'token pasting',
        '??=pragma once\n': 'trigraphs',
        'const char *f = __FILE__;': '__FILE__',
        '__declspec(noinline) void f();': '__declspec(noinline)',
        '#include "../secret.h"\n': '#include',
        '#include <stdio.h>\n': 'no such header',
        'void f() { m_count; }': 'bare expression',
        'void f(int x) { x = x; }': 'self-assignment',
        'void f(int x) { do { x++; } while (0); }': 'do { } while (0)',
        'void f() { __a\\\nsm nop }': 'inline assembly',
        'int café;': 'ASCII',
        '/* cflags: /Od */\nint x;': 'allow-list',
        '/* sym: ?Run@Foo@@QAEXXZ = 0x11000 */\n': 'special members',
        '/* fuzzy: 50.0 */\nint x;': 'non-matching tier',
    }
    for text, expected in cases.items():
        found = problems(text)
        assert any(expected in p for p in found), (text, found)
    assert problems('#pragma pack(push, 1)\nstruct A { char c; };\n#pragma pack(pop)\n') == []
    # a member or variable of a type defined in place is a declaration
    assert problems('struct A { union { float value; int bits; } depth; struct { int x; } pos[2]; };') == []
    assert problems('enum { ONE, TWO } mode;') == []
    assert problems('void f() { try { g(); } catch (...) { throw; } }') == []
    # a block followed by a bare expression is still refused
    assert any('bare expression' in p for p in problems('void f(int x) { if (x) { x++; } m_count; }'))
    assert any('fuzzy' in p for p in problems('int x;', tier='nonmatching'))


def test_folded_function_aliases():
    from xbx import units
    assert units.placeholder('?func_00011C90_2@Node@@UAEXXZ') == ('func', 0x11c90)
    assert units.alias('?func_00011C90_2@Node@@UAEXXZ') == 2 and units.alias('?func_00011C90@Node@@UAEXXZ') == 0
    assert units.slot('?func_00011C90_12@A@@UAEXXZ', 0x11c90) == (0x11c90, 12)
    for name in ('?func_00011C90_x@A@@UAEXXZ', '_func_00011C90_100', '_func_00011C90_0', '_func_00011C90_'):
        assert units.placeholder(name) is None, name


class _Folded:
    folded = frozenset({0x11c90})
    shared_symbols = {}


def test_each_alias_is_declared_one_way():
    from xbx import match, names
    known = {(0x11c90, 0): ('?func_00011C90@Node@@UAEXXZ', 'src/funcs/func_00011C90.cpp'),
             (0x11c90, 1): ('?func_00011C90_1@Node@@UAEXXZ', 'src/funcs/func_00011000.cpp')}
    v = match.Verdict(0x12000)
    v.links = {'?func_00011C90_2@Other@@UAEXXZ': 0x11c90}          # a new alias: free
    assert names.conflicts(known, v, _Folded()) == []
    v.links = {'?func_00011C90_1@Other@@UAEXXZ': 0x11c90}          # a folded address takes any names
    assert names.conflicts(known, v, _Folded()) == []
    known[(0x12100, 0)] = ('?func_00012100@A@@QAEXXZ', 'src/funcs/func_00012100.cpp')
    v.links = {'?func_00012100@B@@QAEXXZ': 0x12100}                # any other address: one declaration
    assert len(names.conflicts(known, v, _Folded())) == 1


def test_overrides_take_their_address_from_a_sym_line():
    from xbx import units
    assert units.is_override('?func_00011C90@Derived@@UAEXXZ', 0x728d0)
    assert units.is_override('?func_00011C80@Derived@@MAEXH@Z', 0x7e020)        # protected virtual
    assert not units.is_override('?func_00011C90@Derived@@QAEXXZ', 0x728d0)    # not virtual
    assert not units.is_override('?func_00011C90@@YAXXZ', 0x728d0)             # a free function
    assert not units.is_override('?func_00011C90@Derived@@UAEXXZ', 0x11c90)    # its own address
    assert not units.is_override('?data_00011C90@Derived@@2HA', 0x728d0)
    assert problems('/* sym: ?func_00011C90@Derived@@UAEXXZ = 0x728D0 */\n') == []
    assert any('virtual overrides' in p for p in problems('/* sym: ?func_00011C90@@YAXXZ = 0x728D0 */\n'))


def test_an_alias_needs_a_listed_folded_function():
    from xbx import match, units
    m = object.__new__(match.Matcher)
    m.project = _Folded()
    unit = units.parse('', 'src/funcs/func_00012000.cpp')
    v = match.Verdict(0x12000)
    assert m.resolve('?func_00011C90_3@A@@UAEXXZ', unit, v) == 0x11c90 and v.problems == []
    assert m.resolve('?func_00012100_1@A@@UAEXXZ', unit, v) == 0x12100
    assert v.problems and 'folded' in v.problems[0]
    assert m.resolve('?data_00011C90_1@@3HA', unit, match.Verdict(0x12000)) == 0x11c90


def test_adjustor_thunks_are_compiler_made():
    from xbx import units
    assert units.is_adjustor('?AddRef@MemStream@@W3AGKXZ')
    assert units.is_adjustor('?Release@Object_001E3A5C@@W3AGKXZ')
    assert not units.is_adjustor('?AddRef@MemStream@@UAGKXZ')
    assert not units.is_adjustor('??_GMemStream@@UAEPAXI@Z')


def test_an_override_named_with_an_alias_is_checked_against_its_address():
    from xbx import match, names, units
    assert units.slot('?func_00011C90_4@Object_00114FD0@@UAEXXZ', 0x114fe0) == (0x114fe0, 0)
    assert units.slot('?func_00011C90_4@Node@@UAEXXZ', 0x11c90) == (0x11c90, 4)
    known = {(0x114fe0, 0): ('?func_00114FE0@Object_00114FE0@@QAEXXZ', 'src/funcs/func_00114FE0.cpp')}
    v = match.Verdict(0x114fd0)
    v.links = {'?func_00011C90_4@Object_00114FD0@@UAEXXZ': 0x114fe0}
    assert len(names.conflicts(known, v, _Folded())) == 1
