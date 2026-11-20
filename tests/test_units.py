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
    assert any('fuzzy' in p for p in problems('int x;', tier='nonmatching'))
