"""`xbx init`: which sections are code, what the templates are filled with,
and that nothing of any one game is left in them. No game file is needed: the
executable is a stand-in with three small sections."""
import collections
import datetime
import struct
import tomllib

import pytest

from xbx import scaffold

Section = collections.namedtuple('Section', 'name va vsize raw rsize flags')


def call(buf, va, at, target):
    buf[at] = 0xE8
    struct.pack_into('<i', buf, at + 1, target - (va + at + 5))


class Image:
    """.text calls two of its own functions and two in a library section,
    twice each. The data section holds bytes that read as calls, each to a
    target of its own, and .text has one such accident pointing into it. Every
    section carries the executable flag, as in a real XBE."""

    def __init__(self, library_version='1.0.4134'):
        text, lib, data = bytearray(0x200), bytearray(0x100), bytearray(0x100)
        for i, target in enumerate([0x11100, 0x11100, 0x11180, 0x11180, 0x12000, 0x12000, 0x12040, 0x12040]):
            call(text, 0x11000, 8 * i, target)
        call(text, 0x11000, 0x80, 0x13010)
        call(data, 0x13000, 0, 0x13080)
        call(data, 0x13000, 8, 0x13090)
        self.blobs = {'.text': bytes(text), 'D3D': bytes(lib), '.data': bytes(data)}
        self.sections = [Section('.text', 0x11000, 0x200, 0, 0x200, 0x16), Section('D3D', 0x12000, 0x100, 0, 0x100, 7),
                         Section('.data', 0x13000, 0x100, 0, 0x100, 7), Section('$$XTIMAGE', 0x14000, 0x100, 0, 0x100, 0x38)]
        self.base, self.end, self.entry = 0x10000, 0x14100, 0x11100
        self.libraries = [('XAPILIB', library_version, 1), ('D3D8', library_version, 1), ('LIBCMT', library_version, 1)]
        self.title, self.title_id, self.timestamp = 'Stand-in', 0x12345678, 1012176000
        self.thunk_va, self.thunks = 0x13000, [1, 2, 3]
        self.data = b'not a game'

    def section_bytes(self, s):
        return self.blobs.get(s.name, bytes(s.rsize))


ROWS = [
    {'addr': '00011100', 'size': '40', 'owner': 'game', 'kind': 'func', 'conv': ''},
    {'addr': '00011180', 'size': '24', 'owner': 'game', 'kind': 'func', 'conv': 'eax'},
    {'addr': '000111c0', 'size': '8', 'owner': 'game', 'kind': 'eh', 'conv': ''},
    {'addr': '00012000', 'size': '64', 'owner': 'lib:d3d8', 'kind': 'func', 'conv': ''},
]


def filled(**options):
    vals = scaffold.values(Image(), 'someone/example-decomp', commit='0123456789abcdef0123456789abcdef01234567',
                           today=datetime.date(2026, 1, 2), **options)
    vals.update(scaffold.table_values(ROWS))
    return vals


def test_code_sections_are_found_from_the_calls():
    assert scaffold.code_sections(Image()) == ['.text', 'D3D']


def test_values_come_from_the_executable_and_the_repository_name():
    vals = filled()
    assert (vals['NAME'], vals['OWNER'], vals['SHORT']) == ('example-decomp', 'someone', 'example')
    assert (vals['TITLE'], vals['TITLE_ID'], vals['VERSION']) == ('Stand-in', '12345678', '12345678')
    assert (vals['IMAGE_LOW'], vals['IMAGE_HIGH']) == ('0x10000', '0x14100')
    assert vals['LIBRARIES'] == 'XAPILIB, D3D8 and LIBCMT' and vals['LINKED'] == '2002-01-28'
    assert vals['CODE_SECTIONS'] == '".text", "D3D"' and '"D3D" = "lib:d3d8"' in vals['OWNER_SECTIONS']
    assert '`lib:d3d8`' in vals['LIB_SENTENCE'] and '3 slots' in vals['SECTION_FACTS']


def test_the_compiler_is_a_guess_unless_named():
    assert filled()['PROFILE'] == 'msvc7.0' and filled()['PROFILE_GUESSED']
    newer = scaffold.values(Image('1.0.5849'), 'someone/example-decomp', commit='0' * 40)
    assert (newer['PROFILE'], newer['BANNER']) == ('msvc7.1', '13.10.3077')
    named = filled(profile='msvc7.1')
    assert named['COMPILER'] == 'Visual C++ 7.1' and not named['PROFILE_GUESSED']


def test_table_values_count_by_owner_and_turn_exceptions_on():
    t = scaffold.table_values(ROWS)
    assert t['FUNCTION_COUNT'] == '4' and t['CONV_COUNT'] == '1' and t['CFLAGS'] == '"/O2", "/GX"'
    lines = t['OWNER_TABLE'].splitlines()
    assert lines[0] == '| `game` | 3 (2 kind `func`, 1 kind `eh`) | 72 | the headline |'
    assert lines[1] == '| `lib:d3d8` | 1 | 64 | the library category |'
    assert scaffold.table_values(ROWS[:2])['CFLAGS'] == '"/O2"'


def test_every_template_is_filled_and_the_configs_parse():
    files = scaffold.render(filled(title='Example Game'))
    for path, text in files.items():
        assert not scaffold.PLACEHOLDER.search(text), path
    for path in ('.gitignore', '.github/workflows/lint.yml', '.github/pull_request_template.md', 'tools/gate.sh',
                 'tools/linux/example-steward.service', 'config/symbols.csv', 'tests/fixtures/canary.cpp', 'LICENSE'):
        assert path in files, path
    assert not any(p.startswith('dot-') for p in files)
    config = tomllib.loads(files['xbx.toml'])
    assert config['image']['code_sections'] == ['.text', 'D3D'] and config['owners']['sections'] == {'D3D': 'lib:d3d8'}
    assert config['compiler']['cflags'] == ['/O2', '/GX'] and config['compiler']['banner'] == '13.00.9466'
    steward = tomllib.loads(files['steward.toml'])
    assert steward['project']['repo'] == 'someone/example-decomp' and steward['policy']['maintainers'] == ['someone']
    assert steward['policy']['identities'] == [] and steward['ai']['enabled'] is False
    assert files['requirements.txt'].rstrip().endswith('decompathon-xbox@0123456789abcdef0123456789abcdef01234567')
    assert files['CODEOWNERS'].strip() == '* @someone'
    assert '# Example Game (Xbox) decompilation' in files['README.md']
    assert 'VERSION: "12345678"' in files['.github/workflows/progress-report.yml']
    assert '--repo someone/example-decomp' in files['tools/linux/example-steward.service']


def test_the_template_holds_nothing_of_one_game():
    banned = ('Jet Set', 'JSRF', 'Smilebit', 'Sega', 'Keybade', '5345000A', 'fd190557', 'memgate')
    for path, text in scaffold.template_files():
        if path == 'docs/LESSONS.md':       # names the projects each lesson came from
            continue
        for word in banned:
            assert word not in text, f'{path}: {word}'


def test_an_unknown_placeholder_and_a_bad_repository_are_errors():
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.fill('a @@NO_SUCH_VALUE@@ b', {}, 'x.md')
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.values(Image(), 'not-a-repository', commit='0' * 40)


def test_short_name():
    assert scaffold.short_name('Halo2-Decomp') == 'halo2'
    assert scaffold.short_name('my game') == 'my-game'
