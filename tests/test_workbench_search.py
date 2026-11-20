"""The beam search and the command line's formatting, with the compiler
replaced by a scoring function over the text. No game file, no compiler."""
import difflib
import os
import struct
import types

import pytest

from xbx.workbench import cli, ledger, mutators, scorer, search

ADDR = 0x11070
BASE = 0x11070

UNIT = '''struct Node {
    Node *next;
    unsigned int flags;
    Node *child;
    void func_00011070();
};

void func_001BA8A0();

void Node::func_00011070() {
    for (Node *node = this; node; node = node->next) {
        if (!(node->flags & 0x80000000)) {
            func_001BA8A0();
            if (node->child) {
                node->child->func_00011070();
            }
        }
    }
}
'''

RETAIL = bytes.fromhex('568bf185f67405') + b'\xe8' + struct.pack('<i', 0x1BA8A0 - (BASE + 12)) + bytes.fromhex('5ec3')
OURS = bytes.fromhex('568bf185f67c05e8000000005ec3')


def step(body, mutator, note):
    """The body a mutator makes for the candidate with this note."""
    cx = mutators.Ctx(ADDR, UNIT)
    return next(b for b, n, _ in mutator(body, cx) if n == note)


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A project of one unit whose "compiler" scores a text by how many lines
    of its function body differ from a target two rewrites away from the start."""
    start = mutators.allman(mutators.body_of(UNIT, ADDR))
    target = step(step(start, mutators.loop_forms, 'for loop as while'),
                  mutators.t2_byref, 'node->flags read through a reference helper')
    unit = tmp_path / 'src' / 'func_00011070.cpp'
    unit.parent.mkdir()
    unit.write_text(UNIT)
    project = types.SimpleNamespace(
        workdir=lambda addr: str(tmp_path / 'out' / f'{addr:08x}'), unit_glob='func_*.cpp', includes=[],
        matched_dir=str(unit.parent), nonmatching_dir=str(tmp_path / 'none'),
        image=types.SimpleNamespace(base=0x10000), rows={ADDR: dict(_size=len(RETAIL), conv='', owner='game')})
    compiler = types.SimpleNamespace(timeout=120)
    scored = []

    def vector(text):
        body = mutators.body_of(text, ADDR)
        if 'value_00011070(' in body and 'value_00011070(T const &member)' not in text:
            return dict(error='x.cpp(1) : error C2065: value_00011070 : undeclared identifier',
                        problems=['does not compile'])
        a, b = mutators.allman(body).split('\n'), target.split('\n')
        n = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
                difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op != 'equal')
        if n == 0:
            return dict(match=1, lock=0, dist=0, sem=0, frame=0, first=-1, problems=[])
        return dict(match=0, lock=n, dist=n, sem=n, frame=0, first=4, problems=['code differs'])

    def score_text(addr, text, origin='manual', note='', parent=None, keep=True):
        h = scorer.digest(text)
        v = ledger.get(addr, h)
        if v is None:
            v = vector(text)
            scored.append((origin, note))
            ledger.put(addr, h, v, parent, origin, note)
        return v

    def examine(addr, text, origin='manual', note='', keep=True):
        verdict = types.SimpleNamespace(ok=False, addr=addr, ours=OURS, mask=frozenset(range(8, 12)), retail=RETAIL,
                                        first=5, defines='?func_00011070@Node@@QAEXXZ', problems=['code differs'])
        return verdict, vector(text)

    monkeypatch.setattr(scorer, 'session', lambda: (project, compiler, None))
    monkeypatch.setattr(scorer, 'salt', lambda: 'test\n')
    monkeypatch.setattr(scorer, 'tree_unit', lambda addr: str(unit) if addr == ADDR else None)
    monkeypatch.setattr(scorer, 'score_text', score_text)
    monkeypatch.setattr(scorer, 'examine', examine)
    monkeypatch.delenv('XBX_TOOL_TIMEOUT', raising=False)
    ledger.use(str(tmp_path / 'out' / 'xbx.db'))
    yield types.SimpleNamespace(project=project, compiler=compiler, target=target, start=start, scored=scored,
                                unit=str(unit), out=tmp_path / 'out' / f'{ADDR:08x}' / 'search')
    ledger.use(None)


def test_search_reaches_a_match_two_rewrites_away_and_writes_it(bench):
    lines = []
    s = search.Search(ADDR, seconds=60, beam=4, seed=1, only={'loop_forms', 't2_byref', 't11_constref'},
                      log=lines.append)
    best = s.run()
    assert best.v['match'] == 1
    assert best.body == bench.target
    # the result is a whole unit: the start's text around the new body, the helper it calls before the function
    assert best.text.startswith(UNIT[:UNIT.index('void Node::func_00011070()')])
    assert best.text.index('value_00011070(T const &member)') < best.text.index('void Node::func_00011070()')
    assert mutators.body_of(best.text, ADDR) == bench.target
    # written, logged and recorded
    written = sorted(p.name for p in bench.out.iterdir())
    assert written[-1] == 'log.txt' and written[0].startswith('best_0001') and len(written) >= 2
    last = sorted(bench.out.glob('best_*.cpp'))[-1]
    assert last.read_text() == best.text
    assert any(l.startswith('== search 00011070 start lock') and 'classes semantic' in l for l in lines)
    assert any(l.startswith('== done 00011070') and '-> best MATCH' in l for l in lines)
    assert (bench.out / 'log.txt').read_text().count('\n') == len(lines)
    rows = ledger.history(ADDR)
    assert rows[0][3] == 1 and rows[0][8] == str(last)          # the match, with the file that holds it
    assert rows[0][1].startswith('search:')
    assert bench.compiler.timeout == search.SEARCH_TOOL_TIMEOUT   # a wedged compile may not eat the time limit


def test_search_works_on_the_body_with_its_braces_on_their_own_lines(bench):
    s = search.Search(ADDR, seconds=60, beam=2, seed=0, only={'loop_forms'}, log=lambda l: None)
    s.run()
    assert s.body0 == bench.start != mutators.body_of(UNIT, ADDR)
    assert ('search:start', 'start') in bench.scored
    assert ('search:start', 'start, braces on their own lines') in bench.scored


def test_search_learns_mutator_rates_from_the_ledger(bench):
    search.Search(ADDR, seconds=60, beam=4, seed=1, only={'loop_forms', 't2_byref'}, log=lambda l: None).run()
    rates = search.ledger_rates()
    assert set(rates) <= {'loop_forms', 't2_byref', 'ablate', 'start'}
    assert rates['loop_forms'][0] >= 1 and rates['loop_forms'][1] >= 1      # tried, and improved its parent
    # a second search starts from the ledger: nothing it already scored is scored again
    before = len(bench.scored)
    again = search.Search(ADDR, seconds=60, beam=4, seed=1, only={'loop_forms', 't2_byref'}, log=lambda l: None)
    assert again.run().v['match'] == 1
    assert len(bench.scored) == before
    assert again.w['loop_forms'] > 0


def test_search_stops_where_the_body_is_not_what_differs(bench, monkeypatch):
    same = dict(match=0, lock=0, dist=0, sem=0, frame=0, first=8, problems=['func_001BA8A0 is 0x1ba8a0, retail refers to 0x1ba8b0'])
    monkeypatch.setattr(scorer, 'score_text', lambda *a, **k: dict(same))
    lines = []
    best = search.Search(ADDR, seconds=60, log=lines.append).run()
    assert best.v['match'] == 0
    assert any('equals retail up to address fields' in l for l in lines)
    assert any(l.strip().startswith('! func_001BA8A0 is') for l in lines)
    assert not list(bench.out.glob('*.cpp'))


def test_search_needs_a_unit_with_the_function(bench, tmp_path):
    with pytest.raises(ValueError):
        search.Search(0x12340)                                   # no unit in the tree
    other = tmp_path / 'other.cpp'
    other.write_text('void func_00099990()\n{\n}\n')
    with pytest.raises(ValueError):
        search.Search(ADDR, start=str(other))                    # a unit without func_00011070


# ------------------------------------------------------------------ the command line's formatting

def test_fmt():
    assert cli.fmt(dict(match=1, lock=0, dist=0, sem=0, frame=0, first=-1)) == 'MATCH'
    assert cli.fmt(dict(match=0, lock=21, dist=8, sem=10, frame=-4, first=3)) == \
        'lock   21  dist    8  sem  10  frame -4  first +0x3'
    assert cli.fmt(dict(match=0, lock=0, dist=0, sem=0, frame=0, first=-1)).endswith('first -')
    error = ('cl.exe failed (2):\nv_1.cpp\n'
             'Z:\\home\\someone\\project\\build\\xbx\\00011070\\v_1.cpp(19) : error C2065: \'x\' : undeclared identifier\n')
    assert cli.fmt(dict(error=error)) == "ERROR v_1.cpp(19) : error C2065: 'x' : undeclared identifier"
    assert cli.fmt(dict(error='cl.exe gave no result in 120s, 3 times')) == 'ERROR cl.exe gave no result in 120s, 3 times'


def test_why_lists_problems_or_the_compiler_s_messages():
    assert cli.why(dict(match=1)) == []
    assert cli.why(dict(match=0, problems=['a', 'b'])) == ['    ! a', '    ! b']
    assert cli.why(dict(error='one\ntwo', problems=['does not compile']), '  ') == ['    one', '    two']
    assert cli.why(dict(error='no code', problems=['the object defines no function'])) == \
        ['    ! the object defines no function']


def test_variants_are_separated_by_a_line_of_equals_signs():
    text = 'int a;\n=====\nint b = 1 == 2;\n==========   \nint c;\n'
    assert [v.strip() for v in cli.SEPARATOR.split(text)] == ['int a;', 'int b = 1 == 2;', 'int c;']


def test_arguments():
    p = cli.parser()
    a = p.parse_args(['score', '0x11070', 'a.cpp', 'b.cpp'])
    assert (a.cmd, a.addr, a.files) == ('score', 0x11070, ['a.cpp', 'b.cpp'])
    assert p.parse_args(['diff', '00011070']).file is None
    a = p.parse_args(['try', '11070', 'v.txt', '--start', 'base.cpp'])
    assert (a.addr, a.files, a.start) == (0x11070, ['v.txt'], 'base.cpp')
    a = p.parse_args(['triage', '--owner', 'game'])
    assert (a.items, a.owner) == ([], 'game')
    a = p.parse_args(['search', '11070', '--time-limit', '30', '--beam', '2'])
    assert (a.addr, a.time_limit, a.beam, a.start) == (0x11070, 30.0, 2, None)
    with pytest.raises(SystemExit):
        p.parse_args(['score', 'not-hex'])


def test_show_keeps_paths_outside_the_current_directory_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'sub').mkdir()
    assert cli.show(str(tmp_path / 'sub' / 'a.cpp')) == os.path.join('sub', 'a.cpp')
    assert cli.show('/somewhere/else/a.cpp') == '/somewhere/else/a.cpp'


def test_function_span_through_sym_lines():
    """Units whose function is a constructor, destructor or override have no func_ name: the
    workbench finds them through the /* sym: */ line that gives them the unit's address."""
    from xbx.workbench import permute_lib as pl
    dtor = ('/* sym: ??1Thing@@UAE@XZ = 0x000728C0 */\n'
            'struct Thing { virtual ~Thing(); };\nThing::~Thing()\n{\n    x = 1;\n}\n')
    ctor = ('/* sym: ??0Thing@@QAE@XZ = 0x00012100 */\n'
            'struct Thing { Thing(); };\nThing::Thing()\n{\n    y = 2;\n}\n')
    ovr = ('/* sym: ?func_00011C90@Thing@@UAEXXZ = 0x000728D0 */\n'
           'struct Thing { virtual void func_00011C90(); };\nvoid Thing::func_00011C90()\n{\n    z = 3;\n}\n')
    for text, addr, body in ((dtor, 0x728c0, 'x = 1;'), (ctor, 0x12100, 'y = 2;'), (ovr, 0x728d0, 'z = 3;')):
        s, e = pl.function_body(text, addr)
        assert text[s:e].strip() == body


def test_function_span_for_operators():
    from xbx.workbench import permute_lib as pl
    member = ('/* sym: ??4Thing@@QAEAAU0@ABU0@@Z = 0x0016AAD0 */\n'
              'struct Thing { Thing &operator=(const Thing &); int a; };\n'
              'Thing &Thing::operator=(const Thing &o)\n{\n    a = o.a;\n    return *this;\n}\n')
    free = ('/* sym: ??8@YA_NABU_GUID@@0@Z = 0x0015FA30 */\n'
            'bool operator==(const _GUID &a, const _GUID &b)\n{\n    return memcmp(&a, &b, 16) == 0;\n}\n')
    s, e = pl.function_body(member, 0x16aad0)
    assert member[s:e].strip().startswith('a = o.a;')
    s, e = pl.function_body(free, 0x15fa30)
    assert 'memcmp' in free[s:e]


def test_function_span_for_a_constructor_with_initializers():
    from xbx.workbench import permute_lib as pl
    text = ('/* sym: ??0Thing@@QAE@PAUNode@@H@Z = 0x000C3860 */\n'
            'struct Node { Node(Node *, int); };\nstruct Thing : Node { Thing(Node *, int); int a; };\n'
            'Thing::Thing(Node *owner, int id)\n    : Node(owner, id), a(0)\n{\n    a = 2;\n}\n')
    s, e = pl.function_body(text, 0xc3860)
    assert text[s:e].strip() == 'a = 2;'
    # a ternary's colon after a call is not an initializer list
    assert pl._definition_brace('f(x) : y;', 1) is None
