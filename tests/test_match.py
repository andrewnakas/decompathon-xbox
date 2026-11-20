"""The matcher and the build gate's linker, checked against each other on a
hand-written object: what the linker writes, the matcher must accept; change
one byte of the image anywhere the unit reaches, and the matcher must refuse."""
import struct

import pytest

from xbx import coff, match, units
from xbx.gates import build as build_gate

import cofftool

FUNC, CALLEE, STRING, GLOBAL = 0x11000, 0x12000, 0x20010, 0x30000
FUNC_NAME = '?func_00011000@@YAHXZ'
STRING_NAME = '??_C@_05CJBACGMB@hello?$AA@'
# push offset "hello"; call func_00012000; mov eax, [data_00030000]; add esp, 4; ret
CODE = bytes.fromhex('68 00000000 e8 00000000 a1 00000000 83c404 c3')


def new_object(code=CODE, extra_sections=(), extra_symbols=(), relocs=None, comdat=1, name=FUNC_NAME):
    relocs = relocs if relocs is not None else [(1, STRING_NAME, coff.REL_DIR32),
                                                 (6, '?func_00012000@@YAXPBD@Z', coff.REL_REL32),
                                                 (11, '?data_00030000@@3HA', coff.REL_DIR32)]
    sections = [dict(name='.text', data=code, flags=cofftool.TEXT, relocs=relocs, comdat=comdat),
                dict(name='.rdata', data=b'hello\0', flags=cofftool.RDATA, comdat=2), *extra_sections]
    symbols = [dict(name=name, section=1, type=cofftool.FUNCTION),
               dict(name=STRING_NAME, section=2),
               dict(name='?func_00012000@@YAXPBD@Z', type=cofftool.FUNCTION),
               dict(name='?data_00030000@@3HA'), *extra_symbols]
    return coff.Coff(cofftool.build(sections, symbols))


def new_project(functions=None):
    image = cofftool.Image([('.text', 0x11000, bytes(0x2000), 0), ('.rdata', 0x20000, bytes(0x100), 0),
                            ('.data', 0x30000, bytes(0x100), 0x100)])
    image.write(CALLEE, b'\xc3')
    return cofftool.Project(image, functions or {FUNC: len(CODE), CALLEE: 1})


def unit_for(text=''):
    return units.parse(text, 'src/funcs/func_00011000.cpp')


def linked(project, obj, unit, name=FUNC_NAME, extra=None):
    """Writes the object into the project's image the way a linker would."""
    blocks = {b.name: b for b in obj.blocks()}
    placement = {blocks[name].key: FUNC, blocks[STRING_NAME].key: STRING, **(extra or {})}
    for addr, data in build_gate.link(project, obj, unit, placement):
        project.image.write(addr, data)
    return placement


@pytest.fixture
def fixture():
    project, obj, unit = new_project(), new_object(), unit_for()
    linked(project, obj, unit)
    return project, obj, unit


def verdict(project, obj, unit, strict=True):
    return match.Matcher(project, strict).compare(obj, unit)


def test_linked_object_matches(fixture):
    project, obj, unit = fixture
    v = verdict(project, obj, unit)
    assert v.ok, v.problems
    assert v.links == {'?func_00012000@@YAXPBD@Z': CALLEE, '?data_00030000@@3HA': GLOBAL}
    assert {name for name, *_ in v.placed} == {FUNC_NAME, STRING_NAME}
    assert project.image.read(FUNC + 1, 4) == struct.pack('<I', STRING)
    assert project.image.read(FUNC + 6, 4) == struct.pack('<i', CALLEE - (FUNC + 10))


def test_one_code_byte(fixture):
    project, obj, unit = fixture
    project.image.write(FUNC + 17, b'\x08')          # add esp, 8
    v = verdict(project, obj, unit)
    assert not v.ok and v.first == 17 and v.differing == 1


def test_one_string_byte(fixture):
    project, obj, unit = fixture
    project.image.write(STRING + 1, b'a')            # "hallo"
    v = verdict(project, obj, unit)
    assert not v.ok and any('differs from retail' in p for p in v.problems)
    assert v.first == -1                             # the function's own bytes are equal


def test_wrong_call_target(fixture):
    project, obj, unit = fixture
    project.image.write(FUNC + 6, struct.pack('<i', CALLEE + 16 - (FUNC + 10)))
    v = verdict(project, obj, unit)
    assert not v.ok and any('retail refers to 0x12010' in p for p in v.problems)


def test_wrong_global(fixture):
    project, obj, unit = fixture
    project.image.write(FUNC + 11, struct.pack('<I', GLOBAL + 4))
    assert not verdict(project, obj, unit).ok


def test_string_elsewhere_still_checked(fixture):
    """Retail may keep the string anywhere; wherever its reference points, the bytes must be it."""
    project, obj, unit = fixture
    project.image.write(0x20040, b'hello\0')
    project.image.write(FUNC + 1, struct.pack('<I', 0x20040))
    assert verdict(project, obj, unit).ok
    project.image.write(FUNC + 1, struct.pack('<I', 0x20080))      # zeros there
    assert not verdict(project, obj, unit).ok


def test_constant_outside_file_backed_data_refused(fixture):
    project, obj, unit = fixture
    project.image.write(FUNC + 1, struct.pack('<I', 0x30100))      # past .data's file-backed bytes
    v = verdict(project, obj, unit)
    assert not v.ok and any('zero-filled' in p for p in v.problems)


def test_unknown_external_strict_and_lenient():
    project = new_project()
    relocs = [(1, STRING_NAME, coff.REL_DIR32), (6, '?Helper@@YAXPBD@Z', coff.REL_REL32),
              (11, '?data_00030000@@3HA', coff.REL_DIR32)]
    obj = new_object(relocs=relocs, extra_symbols=[dict(name='?Helper@@YAXPBD@Z', type=cofftool.FUNCTION)])
    unit = unit_for()
    project.shared_symbols = {'?Helper@@YAXPBD@Z': CALLEE}
    linked(project, obj, unit)
    assert verdict(project, obj, unit).ok                          # the shared table names it
    project.shared_symbols = {}
    strict = verdict(project, obj, unit)
    assert not strict.ok and any('has no address' in p for p in strict.problems)
    lenient = verdict(project, obj, unit, strict=False)
    assert lenient.ok and lenient.assumed == {'?Helper@@YAXPBD@Z': CALLEE}


def test_sym_directive_only_for_special_members():
    project = new_project()
    obj = new_object(name='??0Thing@@QAE@XZ')
    unit = unit_for('/* sym: ??0Thing@@QAE@XZ = 0x11000 */\n')
    linked(project, obj, unit, name='??0Thing@@QAE@XZ')
    assert verdict(project, obj, unit).ok
    plain = new_object(name='?Update@Thing@@QAEXXZ')
    v = verdict(project, plain, unit_for('/* sym: ?Update@Thing@@QAEXXZ = 0x11000 */\n'))
    assert not v.ok and any('special member' in p for p in v.problems)


def test_unused_directives_refused(fixture):
    project, obj, unit = fixture
    v = verdict(project, obj, unit_for('/* sym: ??1Other@@QAE@XZ = 0x12000 */\n/* const: 0x20000 */\n'))
    assert not v.ok and len([p for p in v.problems if 'remove it' in p]) == 2


def test_unverified_data_refused(fixture):
    project, _, unit = fixture
    obj = new_object(extra_sections=[dict(name='.data', data=b'\1\0\0\0', flags=cofftool.RDATA | 0x80000000)],
                     extra_symbols=[dict(name='?g_count@@3HA', section=3)])
    v = verdict(project, obj, unit)
    assert not v.ok and any('unverified data' in p for p in v.problems)


def test_inline_copy_refused(fixture):
    project, _, unit = fixture
    v = verdict(project, new_object(comdat=2), unit)
    assert not v.ok and any('inline copy' in p for p in v.problems)


def test_number_in_the_image_range_needs_a_const():
    code = bytes.fromhex('b8 00000200 c3')           # mov eax, 0x20000; ret
    project = new_project({FUNC: len(code), CALLEE: 1})
    obj = new_object(code=code, relocs=[])
    project.image.write(FUNC, code)
    v = verdict(project, obj, unit_for())
    assert any('0x20000' in p for p in v.problems)
    # the string block is unreferenced in this object, which is a problem of its own
    excused = verdict(project, obj, unit_for('/* const: 0x20000 */\n'))
    assert [p for p in excused.problems if 'unverified' not in p] == []


def test_absolute_memory_operand_cannot_be_excused():
    code = bytes.fromhex('a1 00000300 c3')           # mov eax, [0x30000]; ret
    project = new_project({FUNC: len(code), CALLEE: 1})
    obj = new_object(code=code, relocs=[])
    project.image.write(FUNC, code)
    v = verdict(project, obj, unit_for('/* const: 0x30000 */\n'))
    assert any('declare data_00030000' in p for p in v.problems)


def test_size_and_padding():
    project = new_project()
    padded = new_object(code=CODE + b'\x90')
    linked(project, padded, unit_for())
    assert verdict(project, padded, unit_for()).ok       # a trailing fill byte is not part of the row
    short = new_project({FUNC: len(CODE) + 8, CALLEE: 1})
    linked(short, new_object(), unit_for())
    v = verdict(short, new_object(), unit_for())
    assert not v.ok and any(p.startswith('size:') for p in v.problems)


def test_weak_external_falls_back_to_its_default():
    """A vtable slot naming ??_E (weak) is the ??_G the same object defines."""
    code = bytes.fromhex('c7 01 00000000 c3')        # mov dword [ecx], offset vtable; ret
    deleter = bytes.fromhex('c2 0400')
    project = new_project({FUNC: len(code), CALLEE: 1, 0x11100: len(deleter)})
    obj = coff.Coff(cofftool.build(
        [dict(name='.text', data=code, flags=cofftool.TEXT, comdat=1, relocs=[(2, '??_7Thing@@6B@', coff.REL_DIR32)]),
         dict(name='.text', data=deleter, flags=cofftool.TEXT, comdat=2),
         dict(name='.rdata', data=bytes(4), flags=cofftool.RDATA, comdat=2,
              relocs=[(0, '??_EThing@@UAEPAXI@Z', coff.REL_DIR32)])],
        [dict(name=FUNC_NAME, section=1, type=cofftool.FUNCTION),
         dict(name='??_GThing@@UAEPAXI@Z', section=2, type=cofftool.FUNCTION),
         dict(name='??_7Thing@@6B@', section=3),
         dict(name='??_EThing@@UAEPAXI@Z', type=cofftool.FUNCTION, storage=coff.CLASS_WEAK,
              default='??_GThing@@UAEPAXI@Z')]))
    blocks = {b.name: b for b in obj.blocks()}
    placement = {blocks[FUNC_NAME].key: FUNC, blocks['??_7Thing@@6B@'].key: 0x20020,
                 blocks['??_GThing@@UAEPAXI@Z'].key: 0x11100}
    unit = unit_for()
    for addr, data in build_gate.link(project, obj, unit, placement):
        project.image.write(addr, data)
    v = verdict(project, obj, unit)
    assert v.ok, v.problems
    assert v.covers == [0x11100]
    project.image.write(0x20020, struct.pack('<I', CALLEE))    # the slot points somewhere else
    assert not verdict(project, obj, unit).ok


def test_weak_default_named_by_an_undefined_entry():
    """What cl 13.00 writes: the weak ??_E falls back to an undefined ??_G entry,
    and a second entry of the same name is the COMDAT that defines it. Both
    gates must place and compare that COMDAT."""
    code = bytes.fromhex('c7 01 00000000 c3')
    deleter = bytes.fromhex('c2 0400')
    project = new_project({FUNC: len(code), CALLEE: 1, 0x11100: len(deleter)})
    obj = coff.Coff(cofftool.build(
        [dict(name='.text', data=code, flags=cofftool.TEXT, comdat=1, relocs=[(2, '??_7Thing@@6B@', coff.REL_DIR32)]),
         dict(name='.text', data=deleter, flags=cofftool.TEXT, comdat=2),
         dict(name='.rdata', data=bytes(4), flags=cofftool.RDATA, comdat=2,
              relocs=[(0, '??_EThing@@UAEPAXI@Z', coff.REL_DIR32)])],
        [dict(name=FUNC_NAME, section=1, type=cofftool.FUNCTION),
         dict(name='??_GThing@@UAEPAXI@Z', section=2, type=cofftool.FUNCTION),
         dict(name='??_GThing@@UAEPAXI@Z', type=cofftool.FUNCTION),    # the entry the weak default names
         dict(name='??_7Thing@@6B@', section=3),
         dict(name='??_EThing@@UAEPAXI@Z', type=cofftool.FUNCTION, storage=coff.CLASS_WEAK,
              default='??_GThing@@UAEPAXI@Z')]))
    blocks = {b.name: b for b in obj.blocks()}
    placement = {blocks[FUNC_NAME].key: FUNC, blocks['??_7Thing@@6B@'].key: 0x20020,
                 blocks['??_GThing@@UAEPAXI@Z'].key: 0x11100}
    unit = unit_for()
    for addr, data in build_gate.link(project, obj, unit, placement):
        project.image.write(addr, data)
    v = verdict(project, obj, unit)
    assert v.ok, v.problems
    assert v.covers == [0x11100]


def test_an_unreferenced_inline_copy_is_dropped():
    """An inline helper the compiler inlined everywhere still leaves its COMDAT
    copy in the object; nothing refers to it, so the linker drops it."""
    code = bytes.fromhex('b8 01000000 c3')                 # mov eax, 1; ret
    helper = bytes.fromhex('8b 44 24 04 c3')               # the helper's own copy
    project = new_project({FUNC: len(code)})
    project.image.write(FUNC, code)
    obj = coff.Coff(cofftool.build(
        [dict(name='.text', data=code, flags=cofftool.TEXT, comdat=1),
         dict(name='.text', data=helper, flags=cofftool.TEXT, comdat=2)],
        [dict(name=FUNC_NAME, section=1, type=cofftool.FUNCTION),
         dict(name='?helper@@YAHH@Z', section=2, type=cofftool.FUNCTION)]))
    v = verdict(project, obj, unit_for())
    assert v.ok, v.problems
    assert any('unreferenced COMDAT' in n for n in v.notes)
    blocks = {b.name: b for b in obj.blocks()}
    assert build_gate.link(project, obj, unit_for(), {blocks[FUNC_NAME].key: FUNC}) == [(FUNC, code)]
