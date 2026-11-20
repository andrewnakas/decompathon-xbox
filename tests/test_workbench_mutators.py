"""The source side of the workbench: finding a function's body in a unit, the
brace normaliser, the policy and a few mutators, on small unit texts. No game
file, no compiler."""
import random

import pytest

from xbx.workbench import mutators, permute_lib

ADDR = 0x11070

UNIT = '''/* cflags: /O2 */
struct Node {
    Node *next;
    unsigned int flags;
    Node *child;
    void func_00011070();
};

void func_001BA8A0();   // { not a body }

void Node::func_00011070() {
    for (Node *node = this; node; node = node->next) {
        if (!(node->flags & 0x80000000)) {
            func_001BA8A0();   /* } */
            if (node->child) {
                node->child->func_00011070();
            }
        }
    }
}
'''


# ------------------------------------------------------------------ the function's body

def test_function_body_is_the_text_between_the_definition_s_braces():
    s, e = mutators.function_body(UNIT, ADDR)
    assert UNIT[s - 1] == '{' and UNIT[e] == '}'
    assert UNIT[e:] == '}\n'                                   # the last brace: comments' braces do not count
    body = UNIT[s:e]
    assert body.lstrip().startswith('for (Node *node = this') and 'Node::func_00011070' not in body
    assert mutators.body_of(UNIT, ADDR) == body


def test_function_body_skips_declarations_and_calls_of_the_same_name():
    span = permute_lib.function_span(UNIT, ADDR)
    assert UNIT[span['head']:].startswith('void Node::func_00011070() {')
    assert UNIT[span['name']:].startswith('func_00011070() {')


def test_function_body_finds_a_free_function_and_any_case_of_the_address():
    text = 'int func_0012A3F0(int a);\nint func_0012a3f0(int a)\n{\n    return a + 1;\n}\n'
    s, e = mutators.function_body(text, 0x12A3F0)
    assert text[s:e] == '\n    return a + 1;\n'


def test_function_body_of_a_unit_without_the_function_is_an_error():
    with pytest.raises(ValueError):
        mutators.function_body('int data_00200000 = 3;\n', ADDR)
    with pytest.raises(ValueError):
        mutators.function_body('void func_00011070();\n', ADDR)      # declared, never defined


def test_splice_replaces_only_the_body():
    new = mutators.splice(UNIT, ADDR, '\n    return;\n')
    assert new.endswith('void Node::func_00011070() {\n    return;\n}\n')
    assert new.startswith(UNIT[:UNIT.index('void Node::func_00011070() {')])
    assert mutators.splice(UNIT, ADDR, mutators.body_of(UNIT, ADDR)) == UNIT


def test_with_helpers_puts_them_before_the_definition():
    text = mutators.with_helpers(UNIT, ADDR, ['value'])
    assert text.index('value_00011070(T const &member)') < text.index('void Node::func_00011070() {')
    assert text.index('void func_001BA8A0();') < text.index('value_00011070')
    assert mutators.with_helpers(UNIT, ADDR, []) == UNIT


# ------------------------------------------------------------------ braces on their own lines

def test_allman_moves_block_braces_and_nothing_else():
    body = mutators.body_of(UNIT, ADDR)
    norm = mutators.allman(body)
    assert norm.split('\n')[1:4] == ['    for (Node *node = this; node; node = node->next)', '    {',
                                     '        if (!(node->flags & 0x80000000))']
    assert '            func_001BA8A0();   /* } */' in norm.split('\n')
    assert ''.join(norm.split()) == ''.join(body.split())      # the same tokens
    assert mutators.allman(norm) == norm
    mutators.Model(norm)                                       # the mutators' model reads it


def test_allman_else_chains_and_what_it_leaves_alone():
    body = ('\n    if (a) {\n        x = 1;\n    } else if (b) {\n        x = 2;\n    } else {\n        x = 3;\n    }\n'
            '    int t[2] = {\n        1, 2\n    };\n'
            '    do {\n        x++;\n    } while (x < 9);\n'
            '    if (c) { y = 1; }\n'
            '    s = "if (q) {";\n')
    assert mutators.allman(body).split('\n') == [
        '', '    if (a)', '    {', '        x = 1;', '    }', '    else if (b)', '    {', '        x = 2;', '    }',
        '    else', '    {', '        x = 3;', '    }',
        '    int t[2] = {', '        1, 2', '    };',
        '    do', '    {', '        x++;', '    } while (x < 9);',
        '    if (c) { y = 1; }',
        '    s = "if (q) {";', '']


# ------------------------------------------------------------------ types and mutators

def context():
    norm = mutators.allman(mutators.body_of(UNIT, ADDR))
    return norm, mutators.Ctx(ADDR, UNIT, seed=0)


def test_types_come_from_the_unit():
    norm, cx = context()
    assert cx.types(norm) == {'this': 'Node *', 'node': 'Node *'}
    assert cx.etype('node->flags', norm) == 'unsigned int'
    assert cx.etype('node->child', norm) == 'Node *'
    assert cx.etype('nothing->here', norm) is None


def test_every_mutator_returns_bodies_that_still_parse_as_blocks():
    norm, cx = context()
    produced = 0
    for m in mutators.ALL:
        for body, note, helpers in m(norm, cx):
            assert isinstance(note, str) and set(helpers) <= set(mutators.HELPERS)
            mutators.Model(body)                               # balanced (it raises otherwise)
            # whatever the body, it goes back into the unit and the function is still found
            text = mutators.with_helpers(mutators.splice(UNIT, ADDR, body), ADDR, helpers)
            assert mutators.body_of(text, ADDR) == body
            produced += 1
    assert produced >= 6


def test_loop_forms_on_the_unit():
    norm, cx = context()
    notes = {note: body for body, note, _ in mutators.loop_forms(norm, cx)}
    assert 'for loop as while' in notes
    lines = notes['for loop as while'].split('\n')
    assert '    Node *node = this;' in lines and '    while (node)' in lines
    assert '        node = node->next;' in lines


def test_a_for_scoped_variable_is_not_read_before_its_loop():
    # node is declared by the for statement: no mutator may name it on a line before the loop
    norm, cx = context()
    for m in mutators.ENUMERATED:
        for body, note, _ in m(norm, cx):
            lines = body.split('\n')
            loop = next(i for i, l in enumerate(lines) if 'node = this' in l)
            assert not any('node' in l for l in lines[:loop]), (m.__name__, note)


def test_member_read_into_a_local():
    body = '\n\tlong total = 0;\n\ttotal += item->count;\n\tfunc_00020000(total);\n\treturn total;\n'
    text = ('struct Item\n{\n\tlong count;\n};\nvoid func_00020000(long);\n'
            'long func_00011070(Item *item)\n{' + body + '}\n')
    cx = mutators.Ctx(ADDR, text)
    got = [b for b, note, _ in mutators.t4_member_local(body, cx)]
    # the local is declared at each place before the read; its name avoids every name the body uses
    assert got == ['\n\tlong count2 = item->count;\n\tlong total = 0;\n\ttotal += count2;\n\tfunc_00020000(total);\n\treturn total;\n',
                   '\n\tlong total = 0;\n\tlong count2 = item->count;\n\ttotal += count2;\n\tfunc_00020000(total);\n\treturn total;\n']


def test_sampled_mutations_are_deterministic_for_a_seed():
    norm, _ = context()
    runs = []
    for _ in range(2):
        cx = mutators.Ctx(ADDR, UNIT, seed=7)
        runs.append([[b for b, _, _ in m(norm, cx)] for m in mutators.ALL if m.__name__.startswith('up_')])
    assert runs[0] == runs[1]
    assert any(runs[0])


def test_names_are_unique_and_weights_name_real_mutators():
    assert len(mutators.NAMES) == len(mutators.ALL)
    named = set()
    for table in list(mutators.CLASS_WEIGHTS.values()) + list(mutators.HINT_WEIGHTS.values()):
        named |= set(table)
    assert named <= set(mutators.NAMES)


def test_code_hides_comments_and_literals_and_keeps_columns():
    line = '    s = "a{b" + x->y; /* } */ z = \'}\';  // {'
    c = mutators.code(line)
    assert '{' not in c and '}' not in c
    assert c.index('x->y') == line.index('x->y') and c.index('z =') == line.index('z =')
    assert mutators.code('a = 1;\n// note\nb = 2;') == 'a = 1;\n\nb = 2;'      # a comment ends with its line


# ------------------------------------------------------------------ policy

START = '\n\tlong a = get();\n\tuse(a);\n\tuse(a);\n'


def test_policy_rejects_what_review_rejects():
    reject = lambda body: mutators.policy(body, START)[0]
    assert reject(START) is None
    assert reject('\n\tvolatile long a = get();\n\tuse(a);\n\tuse(a);\n') == 'volatile'
    assert reject('\n#pragma optimize("", off)' + START) == '#pragma'
    assert reject(START + '\tgoto out;\nout:\n\treturn;\n') == 'goto'
    assert reject(START + '\tlong b = get();\n').startswith('unused local')
    assert reject(START + '\t{\n\t\tlong a = 2;\n\t\tuse(a);\n\t}\n').startswith('local declared twice')
    assert reject('\n\t// volatile in a comment\n' + START.lstrip('\n')) is None


def test_policy_flags_a_new_local_read_once():
    reason, flags = mutators.policy(START + '\tlong b = get();\n\tuse(b);\n', START)
    assert reason is None and flags == ['one-use b']
    # what the start already had is not held against a candidate
    assert mutators.policy(START + '\tlong b = get();\n\tuse(b);\n', START + '\tlong b = get();\n\tuse(b);\n') == (None, [])


def test_permute_lib_mutations_keep_the_signature():
    rng = random.Random(3)
    body = '\n\tlong a = x + y;\n\tif (a > 3)\n\t{\n\t\tfoo(a);\n\t}\n\telse\n\t{\n\t\tbar(a);\n\t}\n\treturn a;\n'
    seen = set()
    for fn in permute_lib.MUTATIONS:
        for _ in range(4):
            out = fn(body, rng)
            assert out is None or isinstance(out, str)
            if out and out != body:
                seen.add(fn.__name__)
    assert {'mut_swap_operands', 'mut_swap_if_else'} <= seen
