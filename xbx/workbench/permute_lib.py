"""Source mutations sampled at random by the search, and the small C++ text
model they share: literal-aware scanning, brace matching, declared types.

Derived from tools/permute.py and tools/reduced.py of the halo2-decompiled
project (GPL-3.0-or-later): the mutation functions `xbx search` uses, with that
project's own type names and macros taken out (only the standard scalar types
are known; a typedef of one is read from the unit and its headers) and the
indentation taken from the text instead of assumed.

Every mutation is  fn(body, rng) -> new body or None  (MUTATIONS), or
fn(body, rng, context) (TYPED_MUTATIONS, which read declared types from a
Context). body is the text between a function's braces.

function_body(text, addr) finds that text in a unit: the body of the function
named func_XXXXXXXX.
"""
import os
import re

NOT_NAMES = {'long', 'int', 'short', 'unsigned', 'signed', 'char', 'bool', 'const', 'void', 'static', 'sizeof',
             'float', 'double', 'struct', 'return'}
STATEMENT_STOP = ('for', 'while', 'if', 'else', 'do', 'return', 'break', 'continue', 'goto', 'switch', 'case')
MAX_CONTEXT = 4 << 20   # characters of unit + headers a Context reads


# ------------------------------------------------------------------ scanning

def _skip_literal(text, i):
    """If a comment, string or character literal starts at i, the index after it, else i."""
    if text.startswith('//', i):
        j = text.find('\n', i)
        return len(text) if j < 0 else j
    if text.startswith('/*', i):
        j = text.find('*/', i + 2)
        return len(text) if j < 0 else j + 2
    if text[i] in '"\'':
        q, j = text[i], i + 1
        while j < len(text) and text[j] != q:
            j += 2 if text[j] == '\\' else 1
        return j + 1
    return i


def match_brace(text, open_index):
    """The index of the '}' matching the '{' at open_index."""
    depth, i = 0, open_index
    while i < len(text):
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError('unbalanced braces')


def _split_top(text, start):
    """Index just after the balanced '(...)' that starts at start."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _skip_space(text, i):
    """The index of the next character at or after i outside whitespace and comments."""
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text.startswith(('//', '/*'), i):
            i = _skip_literal(text, i)
        else:
            break
    return i


_AFTER_PARAMETERS = re.compile(r'(?:const|volatile)\b|throw\s*\(')


def _definition_brace(text, k):
    """k: just after a function's name. The index of the '{' that opens its
    body when a definition follows (parameters, then const / volatile /
    throw(...)), else None (a declaration, a call)."""
    k = _skip_space(text, k)
    if k >= len(text) or text[k] != '(':
        return None
    k = _split_top(text, k)
    while k is not None:
        k = _skip_space(text, k)
        m = _AFTER_PARAMETERS.match(text, k)
        if not m:
            break
        k = _split_top(text, m.end() - 1) if m.group(0).endswith('(') else m.end()
    return k if k is not None and k < len(text) and text[k] == '{' else None


def _sym_names(text, addr):
    """Regexes for the C++ names that the unit's /* sym: */ lines give address addr: Class::Class,
    Class::~Class, or Class::name for an override that carries another function's address."""
    out = []
    for m in re.finditer(r'/\*\s*sym:\s*(\S+)\s*=\s*(0[xX][0-9A-Fa-f]+)\s*\*/', text):
        name, value = m.group(1), int(m.group(2), 16)
        if value != addr:
            continue
        special = re.match(r'\?\?([01])([A-Za-z_]\w*)@', name)
        member = re.match(r'\?([A-Za-z_]\w*)@([A-Za-z_]\w*)@', name)
        if special:
            cls = re.escape(special.group(2))
            tail = cls if special.group(1) == '0' else r'~\s*' + cls
            out.append(re.compile(r'(?<![0-9A-Za-z_])%s\s*::\s*%s(?![0-9A-Za-z_])' % (cls, tail)))
        elif member:
            out.append(re.compile(r'(?<![0-9A-Za-z_])%s\s*::\s*%s(?![0-9A-Za-z_])'
                                  % (re.escape(member.group(2)), re.escape(member.group(1)))))
    return out


def function_span(text, addr):
    """Where a unit's text defines its function: the one named func_XXXXXXXX (addr in hex, eight
    digits, either case; any scope or qualification before it), else the constructor, destructor or
    override that a /* sym: */ line gives address addr. {'head': start of the definition's first
    token, 'name': index of the name, 'open': its '{', 'close': the matching '}'}. ValueError when
    the text has no such definition."""
    names = [re.compile(r'(?<![0-9A-Za-z_])func_%08x(?![0-9A-Za-z_])' % addr, re.I)]
    for name in names + _sym_names(text, addr):
        span = _definition_of(text, name)
        if span is not None:
            return span
    raise ValueError(f'the text defines no function named func_{addr:08X}, and no /* sym: */ line gives '
                     'a constructor, destructor or override that address')


def _definition_of(text, name):
    i, boundary = 0, 0
    while i < len(text):
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        c = text[i]
        if c in ';{}':
            boundary = i + 1
        elif c == '#' and not text[text.rfind('\n', 0, i) + 1:i].strip():
            while True:                                 # a preprocessor line, with its continuations
                e = text.find('\n', i)
                e = len(text) if e < 0 else e
                if not text[i:e].rstrip().endswith('\\') or e >= len(text):
                    break
                i = e + 1
            i = boundary = e
            continue
        else:
            m = name.match(text, i)
            if m:
                open_ = _definition_brace(text, m.end())
                if open_ is not None:
                    return dict(head=_skip_space(text, boundary), name=m.start(), open=open_,
                                close=match_brace(text, open_))
                i = m.end()
                continue
        i += 1
    return None


def function_body(text, addr):
    """(start, end) such that text[start:end] is the body of the function named
    func_XXXXXXXX: the text between its braces."""
    span = function_span(text, addr)
    return span['open'] + 1, span['close']


# ----------------------------------------------------------------- mutations

OPERAND = (r'(?:\([A-Za-z_][\w \t\*]*\))?~?(?:\([^()\n]*\)|[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*)*(?:\[[^\]\n]*\])?'
           r'|\d\w*)')
COMMUTATIVE = ['+', '*', '&', '|', '^', '==', '!=']
FLIPPED = {'<': '>', '>': '<', '<=': '>=', '>=': '<='}
BINARY = re.compile(r'(?P<a>' + OPERAND + r')\s*(?P<op><=|>=|==|!=|[-+*&|^<>])\s*(?P<b>'
                    + OPERAND + r')(?=\s*[;,)])')
GUARD_BEFORE = re.compile(r'(?:^|[(=,;{\n]|\breturn)[ \t]*$')


def binary_spans(text):
    """Matches of a lone 'A op B' with simple operands, delimited so that the
    whole text between the delimiters is the expression."""
    spans = []
    for m in BINARY.finditer(text):
        if GUARD_BEFORE.search(text[:m.start('a')]):
            spans.append(m)
    return spans


def mut_swap_operands(body, rng):
    spans = [m for m in binary_spans(body) if m.group('op') in COMMUTATIVE or m.group('op') in FLIPPED]
    if not spans:
        return None
    m = rng.choice(spans)
    op = m.group('op')
    new = f"{m.group('b')} {FLIPPED.get(op, op)} {m.group('a')}"
    return body[:m.start()] + new + body[m.end('b'):]


def _names(stmt):
    return {n for n in re.findall(r'[A-Za-z_]\w*', stmt) if n not in NOT_NAMES}


def _has_call(stmt):
    return re.search(r'\b(?!sizeof\b)\w+\s*\(', stmt) is not None


def _is_simple_statement(line):
    s = line.strip()
    return (s.endswith(';') and not s.startswith('//') and '{' not in s and '}' not in s
            and not re.match(r'(?:' + '|'.join(STATEMENT_STOP) + r')\b', s))


def mut_reorder(body, rng):
    lines = body.split('\n')
    pairs = []
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if not (_is_simple_statement(a) and _is_simple_statement(b)):
            continue
        if len(a) - len(a.lstrip()) != len(b) - len(b.lstrip()):
            continue
        if _names(a) & _names(b) or (_has_call(a) and _has_call(b)):
            continue
        pairs.append(i)
    if not pairs:
        return None
    i = rng.choice(pairs)
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return '\n'.join(lines)


def _paren_groups(line):
    """(start, end) of balanced parenthesized groups that look like arithmetic."""
    out, stack = [], []
    for i, ch in enumerate(line):
        if ch == '(':
            stack.append(i)
        elif ch == ')' and stack:
            s = stack.pop()
            inner = line[s + 1:i]
            before = line[:s].rstrip()
            if re.search(r'[-+*&|^/%<>]', inner) and not re.fullmatch(r'[\w \t\*]+', inner) \
                    and not (before and (before[-1].isalnum() or before[-1] == '_')):
                out.append((s, i))
    return out


def _fresh_temp(body):
    n = 0
    while re.search(r'\btmp%d\b' % n, body):
        n += 1
    return f'tmp{n}'


def mut_introduce_temp(body, rng):
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not _is_simple_statement(line) and not s.startswith('return'):
            continue
        if not s.endswith(';') or s.startswith('//'):
            continue
        for a, b in _paren_groups(line):
            cands.append((i, a, b + 1, line[a + 1:b]))
        for m in binary_spans(line):
            cands.append((i, m.start(), m.end(), m.group(0)))
    if not cands:
        return None
    i, a, b, expr = rng.choice(cands)
    name = _fresh_temp(body)
    line = lines[i]
    indent = line[:len(line) - len(line.lstrip())]
    lines[i:i + 1] = [f'{indent}{rng.choice(["long", "unsigned long", "int"])} {name} = {expr};',
                      line[:a] + name + line[b:]]
    return '\n'.join(lines)


TEMP_DECL = re.compile(r'^[ \t]*(?P<type>long|int|unsigned long|unsigned int|unsigned|[\w ]+\*)[ \t]*(?P<name>\w+)'
                       r'\s*=\s*(?P<expr>[^;]+);\s*$')


def mut_inline_temp(body, rng):
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        m = TEMP_DECL.match(line)
        if not m or m.group('type').split()[0] in ('return',):
            continue
        name, expr = m.group('name'), m.group('expr').strip()
        uses = [j for j, l in enumerate(lines) if j != i and re.search(r'\b%s\b' % name, l)]
        if len(uses) != 1 or uses[0] < i:
            continue
        j = uses[0]
        if len(re.findall(r'\b%s\b' % name, body)) != 2 or re.search(r'\b%s\b\s*(?:[-+*&|^]?=[^=]|\+\+|--)' % name, lines[j]):
            continue
        # nothing between the two lines may touch what expr reads
        deps = _names(expr)
        if any(deps & _names(l) for l in lines[i + 1:j]) or any(_has_call(l) for l in lines[i + 1:j]):
            continue
        if re.search(r'\b%s\b' % name, lines[j].split('//')[0]) is None or lines[j].lstrip().startswith(('for', 'while')):
            continue
        cands.append((i, j, name, expr))
    if not cands:
        return None
    i, j, name, expr = rng.choice(cands)
    simple = re.fullmatch(r'[\w.\->]+|\([^()]*\)', expr) is not None
    lines[j] = re.sub(r'\b%s\b' % name, (expr if simple else f'({expr})').replace('\\', '\\\\'), lines[j])
    del lines[i]
    return '\n'.join(lines)


def _negate(cond):
    cond = cond.strip()
    m = re.fullmatch(r'(?P<a>[^<>=!&|()]+?)\s*(?P<op>==|!=|<=|>=|<|>)\s*(?P<b>[^<>=!&|()]+)', cond)
    if m:
        inverse = {'==': '!=', '!=': '==', '<': '>=', '>': '<=', '<=': '>', '>=': '<'}[m.group('op')]
        return f"{m.group('a')} {inverse} {m.group('b')}"
    m = re.fullmatch(r'!\s*(\w+)', cond)
    if m:
        return m.group(1)
    m = re.fullmatch(r'!\s*\((.*)\)', cond)
    if m and _split_top(cond, cond.index('(')) == len(cond):
        return m.group(1)
    return f'!({cond})'


def mut_swap_if_else(body, rng):
    cands = []
    for m in re.finditer(r'\bif\s*\(', body):
        end = _split_top(body, m.end() - 1)
        if end is None:
            continue
        k = end
        while k < len(body) and body[k] in ' \t\r\n':
            k += 1
        if k >= len(body) or body[k] != '{':
            continue
        close = match_brace(body, k)
        e = re.compile(r'\s*else\s*\{').match(body, close + 1)
        if not e:
            continue
        eopen = e.end() - 1
        cands.append((m, end, k, close, eopen, match_brace(body, eopen)))
    if not cands:
        return None
    m, end, k, close, eopen, eclose = rng.choice(cands)
    cond = body[m.end():end - 1]
    then, other = body[k + 1:close], body[eopen + 1:eclose]
    gap = body[end:k]
    mid = body[close + 1:eopen]
    return (body[:m.end()] + _negate(cond) + ')' + gap + '{' + other + '}' + mid + '{' + then + '}'
            + body[eclose + 1:])


FOR = re.compile(r'^(?P<indent>[ \t]*)for\s*\((?P<init>[^;{}]*);(?P<cond>[^;{}]*);(?P<inc>[^;{}]*)\)\s*\{\s*$')


def mut_for_while(body, rng):
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        m = FOR.match(line)
        if m and m.group('cond').strip() and m.group('inc').strip():
            close = _closing_line(lines, i)
            if close is not None and not any(re.search(r'\bcontinue\b', l) for l in lines[i + 1:close]):
                cands.append(('for', i, close))
        if re.match(r'^[ \t]*while\s*\(.*\)\s*\{\s*$', line) and i > 0 and _is_simple_statement(lines[i - 1]):
            close = _closing_line(lines, i)
            if close is None or close - 1 <= i or not _is_simple_statement(lines[close - 1]):
                continue
            last = lines[close - 1].strip()
            if re.fullmatch(r'(?:\w+(?:\+\+|--)|(?:\+\+|--)\w+|\w+\s*[-+]=\s*[\w.]+);', last) \
                    and not any(re.search(r'\bcontinue\b', l) for l in lines[i + 1:close]):
                cands.append(('while', i, close))
    if not cands:
        return None
    kind, i, close = rng.choice(cands)
    if kind == 'for':
        m = FOR.match(lines[i])
        ind = m.group('indent')
        inc = m.group('inc').strip()
        lines[close:close] = [f'{ind}{_indent_unit(body)}{inc};']
        lines[i:i + 1] = ([f'{ind}{m.group("init").strip()};'] if m.group('init').strip() else []) + \
                         [f'{ind}while ({m.group("cond").strip()}) {{']
        return '\n'.join(lines)
    w = re.match(r'^([ \t]*)while\s*\((.*)\)\s*\{\s*$', lines[i])
    init = lines[i - 1].strip().rstrip(';')
    inc = lines[close - 1].strip().rstrip(';')
    ind = w.group(1)
    new = [f'{ind}for ({init}; {w.group(2)}; {inc}) {{']
    lines[i - 1:close] = new + lines[i + 1:close - 1]
    return '\n'.join(lines)


def _closing_line(lines, i):
    depth = 0
    for k in range(i, len(lines)):
        depth += lines[k].count('{') - lines[k].count('}')
        if depth == 0:
            return k
    return None


COMPOUND = re.compile(r'^(?P<indent>[ \t]*)(?P<lhs>[A-Za-z_][\w.\->\[\]]*)\s*(?P<op>[-+*&|^])=\s*(?P<rhs>[^;=]+);\s*$')
EXPANDED = re.compile(r'^(?P<indent>[ \t]*)(?P<lhs>[A-Za-z_][\w.\->\[\]]*)\s*=\s*(?P=lhs)\s*(?P<op>[-+*&|^])\s*(?P<rhs>[^;=]+);\s*$')


def mut_compound(body, rng):
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        for kind, pat in (('expand', COMPOUND), ('fold', EXPANDED)):
            m = pat.match(line)
            if m and '(' not in m.group('lhs') and '++' not in line and '--' not in line:
                rhs = m.group('rhs').strip()
                if kind == 'fold' and not re.fullmatch(r'%s|\([^()]*\)' % OPERAND, rhs):
                    continue
                cands.append((kind, i, m))
    if not cands:
        return None
    kind, i, m = rng.choice(cands)
    ind, lhs, op, rhs = m.group('indent'), m.group('lhs'), m.group('op'), m.group('rhs').strip()
    if kind == 'expand':
        wrapped = rhs if re.fullmatch(OPERAND, rhs) else f'({rhs})'
        lines[i] = f'{ind}{lhs} = {lhs} {op} {wrapped};'
    else:
        if rhs.startswith('(') and rhs.endswith(')') and _split_top(rhs, 0) == len(rhs):
            rhs = rhs[1:-1]
        lines[i] = f'{ind}{lhs} {op}= {rhs};'
    return '\n'.join(lines)


STEP = re.compile(r'(?:(?<=^)|(?<=;)|(?<=\n))(?P<pre>[ \t]*)(?:(?P<a>\+\+|--)(?P<n1>[A-Za-z_]\w*)|(?P<n2>[A-Za-z_]\w*)(?P<b>\+\+|--))(?=[ \t]*[;)])')
FOR_STEP = re.compile(r'(?<=;)(?P<pre>[ \t]*)(?:(?P<a>\+\+|--)(?P<n1>[A-Za-z_]\w*)|(?P<n2>[A-Za-z_]\w*)(?P<b>\+\+|--))(?=[ \t]*\))')


def mut_increment(body, rng):
    spans = list(STEP.finditer(body)) + list(FOR_STEP.finditer(body))
    plus_one = list(re.finditer(r'(?m)^(?P<pre>[ \t]*)(?P<n>[A-Za-z_]\w*)\s*(?P<s>[-+])=\s*1;', body))
    if not spans and not plus_one:
        return None
    if plus_one and (not spans or rng.random() < 0.3):
        m = rng.choice(plus_one)
        return body[:m.start()] + f'{m.group("pre")}{m.group("n")}{m.group("s")}{m.group("s")};' + body[m.end():]
    m = rng.choice(spans)
    if m.group('a'):
        new = f'{m.group("pre")}{m.group("n1")}{m.group("a")}'
    else:
        new = f'{m.group("pre")}{m.group("b")}{m.group("n2")}'
    return body[:m.start()] + new + body[m.end():]


_SPLIT_TYPES = r'unsigned long|unsigned int|unsigned char|long|int|short|char|[\w ]+\*'
DECL_INIT = re.compile(r'^(?P<indent>[ \t]*)(?P<type>' + _SPLIT_TYPES + r')[ \t]*(?P<name>\w+)\s*=\s*(?P<expr>[^;]+);\s*$')
DECL_ONLY = re.compile(r'^(?P<indent>[ \t]*)(?P<type>' + _SPLIT_TYPES + r')[ \t]*(?P<name>\w+);\s*$')


def mut_split_decl(body, rng):
    """'T x = e;' <-> 'T x;' followed by 'x = e;'."""
    lines = body.split('\n')
    cands = []
    for i, l in enumerate(lines):
        m = DECL_INIT.match(l)
        if m and not m.group('type').strip().startswith(('return', 'else')):
            cands.append(('split', i, m))
        m = DECL_ONLY.match(l)
        if m and i + 1 < len(lines):
            n = re.match(r'^[ \t]*%s\s*=\s*([^;]+);\s*$' % m.group('name'), lines[i + 1])
            if n:
                cands.append(('merge', i, m))
    if not cands:
        return None
    kind, i, m = rng.choice(cands)
    ind, name = m.group('indent'), m.group('name')
    if kind == 'split':
        lines[i:i + 1] = [f'{ind}{m.group("type").strip()} {name};', f'{ind}{name} = {m.group("expr").strip()};']
    else:
        n = re.match(r'^[ \t]*%s\s*=\s*([^;]+);\s*$' % name, lines[i + 1])
        lines[i:i + 2] = [f'{ind}{m.group("type").strip()} {name} = {n.group(1).strip()};']
    return '\n'.join(lines)


# Idiom mutations: the same behaviour spelled the way the original source may have spelled it.

LOOSER = {'&&': {'||', '?', ',', '='}, '||': {'?', ',', '='}}   # operators that bind more loosely than the key


def _top_level(text):
    """The operators of text outside brackets, strings and comments that matter for precedence:
    '&&', '||', '?', ',' and assignments (plain or compound), as (operator, index) pairs."""
    found, depth, i = [], 0, 0
    while i < len(text):
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        c = text[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif depth == 0:
            if text.startswith(('&&', '||'), i):
                found.append((text[i:i + 2], i))
                i += 2
                continue
            if c in '?,':
                found.append((c, i))
            elif c == '=':
                if text.startswith('==', i):
                    i += 2
                    continue
                before = text[max(i - 2, 0):i]
                if before[-1:] not in ('!', '<', '>') or before in ('<<', '>>'):
                    found.append(('=', i))
        i += 1
    return found


def _split_logical(cond, op):
    """The operands of a top-level 'a op b op c' (op is '&&' or '||'), else None: also None
    when cond has a looser operator at the top level (a conditional, an assignment, a comma)."""
    ops = _top_level(cond)
    if any(o in LOOSER[op] for o, _ in ops):
        return None
    cuts = [i for o, i in ops if o == op]
    if not cuts:
        return None
    parts = [cond[a:b].strip() for a, b in zip([0] + [i + 2 for i in cuts], cuts + [len(cond)])]
    return parts if all(parts) else None


def _wrap(text, op):
    """text as an operand of op, in parentheses when it holds a looser operator."""
    return f'({text})' if any(o in LOOSER[op] for o, _ in _top_level(text)) else text


def _plain_literal(text):
    """True for a literal that every scalar type, bool included, holds unchanged."""
    return text in ('0', '1', 'true', 'false')


PLACE = r'[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*|\[\w+\])?'


def _independent(x, y):
    """True when storing to the place x cannot change where the place y is, nor the reverse."""
    if not (re.fullmatch(PLACE, x) and re.fullmatch(PLACE, y)):
        return False
    return not any(re.fullmatch(r'[A-Za-z_]\w*', p) and p in _names(q) for p, q in ((x, y), (y, x)))


def _starts_statement(lines, i, labels=True):
    """True when line i begins a statement, not the one-statement body of an if, else, for or while;
    a label before it counts as a start only with labels=True (a declaration cannot follow one)."""
    before = next((l.strip() for l in reversed(lines[:i]) if l.strip() and not l.strip().startswith('//')), ';')
    if before.endswith(':') and labels:
        return re.fullmatch(r'(?:case\b[^:]*|default|\w+)\s*:', before) is not None
    return before.endswith((';', '{', '}'))


CHAIN = re.compile(r'^(?P<indent>[ \t]*)(?P<a>[A-Za-z_][\w.\->\[\]]*)\s*=\s*(?P<b>[A-Za-z_][\w.\->\[\]]*)\s*=\s*'
                   r'(?P<v>\w+)\s*;\s*$')
PLAIN = re.compile(r'^(?P<indent>[ \t]*)(?P<a>[A-Za-z_][\w.\->\[\]]*)\s*=\s*(?P<v>\w+)\s*;\s*$')


def mut_chain_assign(body, rng):
    """'a = b = 0;' <-> 'b = 0; a = 0;': the chain stores to b first, the pair in either order.
    Only 0, 1, true and false, so that a bool or narrow b cannot change what a receives."""
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        if not _starts_statement(lines, i):
            continue
        m = CHAIN.match(line)
        if m and _plain_literal(m.group('v')) and _independent(m.group('a'), m.group('b')):
            cands.append(('split', i))
        m = PLAIN.match(line)
        if m and _plain_literal(m.group('v')) and i + 1 < len(lines):
            n = PLAIN.match(lines[i + 1])
            if n and n.group('indent') == m.group('indent') and n.group('v') == m.group('v') \
                    and _independent(m.group('a'), n.group('a')):
                cands.append(('merge', i))
    if not cands:
        return None
    kind, i = rng.choice(cands)
    if kind == 'split':
        m = CHAIN.match(lines[i])
        order = rng.choice([(m.group('b'), m.group('a')), (m.group('a'), m.group('b'))])
        lines[i:i + 1] = [f'{m.group("indent")}{x} = {m.group("v")};' for x in order]
    else:
        m, n = PLAIN.match(lines[i]), PLAIN.match(lines[i + 1])
        first, second = rng.choice([(m, n), (n, m)])
        lines[i:i + 2] = [f'{m.group("indent")}{first.group("a")} = {second.group("a")} = {m.group("v")};']
    return '\n'.join(lines)


def _if_head(line):
    """(indent, condition, rest of the line) of a line that starts with 'if (', else None."""
    m = re.match(r'^([ \t]*)if\s*\(', line)
    end = _split_top(line, m.end() - 1) if m else None
    return (m.group(1), line[m.end():end - 1], line[end:]) if end else None


def mut_bool_flag(body, rng):
    """'if (a && b) S' <-> 'bool flag0 = false; if (a) flag0 = b; if (flag0) S': a named bool gets a home."""
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        h = _if_head(line)
        if h and h[2].strip() in ('', '{') and _split_logical(h[1], '&&'):
            if _starts_statement(lines, i, labels=False):
                cands.append(('name', i))
        m = re.match(r'^([ \t]*)bool\s+(flag\d+)\s*=\s*false;\s*$', line)
        if m and i + 3 < len(lines) and len(re.findall(r'\b%s\b' % m.group(2), body)) == 3:
            a = _if_head(lines[i + 1])
            b = re.match(r'^[ \t]*%s\s*=\s*([^;]+);\s*$' % m.group(2), lines[i + 2])
            c = _if_head(lines[i + 3])
            if a and b and c and a[2].strip() == '' and c[1].strip() == m.group(2):
                cands.append(('inline', i))
    if not cands:
        return None
    kind, i = rng.choice(cands)
    if kind == 'name':
        ind, cond, rest = _if_head(lines[i])
        parts = _split_logical(cond, '&&')
        n = 0
        while re.search(r'\bflag%d\b' % n, body):
            n += 1
        lines[i:i + 1] = [f'{ind}bool flag{n} = false;', f'{ind}if ({parts[0]})',
                          f'{ind}{_indent_unit(body)}flag{n} = {" && ".join(parts[1:])};', f'{ind}if (flag{n}){rest}']
    else:
        ind = re.match(r'^[ \t]*', lines[i]).group(0)
        first = _if_head(lines[i + 1])[1].strip()
        second = re.match(r'^[ \t]*\w+\s*=\s*([^;]+);', lines[i + 2]).group(1).strip()
        rest = _if_head(lines[i + 3])[2]
        lines[i:i + 4] = [f'{ind}if ({_wrap(first, "&&")} && {_wrap(second, "&&")}){rest}']
    return '\n'.join(lines)


def _braced_if(body, m):
    """For the match m of 'if (': (condition, end of the condition, index of '{', index of '}'), else None."""
    end = _split_top(body, m.end() - 1)
    if end is None:
        return None
    k = end
    while k < len(body) and body[k] in ' \t\r\n':
        k += 1
    if k >= len(body) or body[k] != '{':
        return None
    return body[m.end():end - 1], end, k, match_brace(body, k)


def _flat(text):
    return re.sub(r'\s+', ' ', text).strip()


def _line_indent(body, pos):
    return re.match(r'[ \t]*', body[body.rfind('\n', 0, pos) + 1:]).group(0)


def _deeper(block, unit):
    return re.sub(r'\n(?=[^\n])', lambda _: '\n' + unit, block)


def _shallower(block, unit):
    return re.sub(r'(?<=\n)' + re.escape(unit), '', block)


def mut_or_split(body, rng):
    """'if (a || b) {S}' <-> 'if (a) {S} else if (b) {S}' and
    'if (a && b) {S}' (without an else) <-> 'if (a) { if (b) {S} }'."""
    cands = []
    for m in re.finditer(r'(?:\belse\s+)?\bif\s*\(', body):
        info = _braced_if(body, m)
        if not info:
            continue
        cond, end, k, close = info
        block = body[k:close + 1]
        chain = re.compile(r'\s*else\s+if\s*\(').match(body, close + 1)
        has_else = re.compile(r'\s*else\b').match(body, close + 1) is not None
        ors = _split_logical(cond, '||')
        if ors and len(ors) == 2 and not re.search(r'^[ \t]*\w+:[ \t]*$|\bstatic\b', block, re.M):
            cands.append(('split_or', m, end, k, close, ors))
        if chain:
            other = _braced_if(body, chain)
            if other and _flat(body[other[2]:other[3] + 1]) == _flat(block):
                cands.append(('join_or', m, end, k, close, (cond, other[0], other[3])))
        if m.group(0).startswith('else') or has_else:
            continue
        ands = _split_logical(cond, '&&')
        if ands and len(ands) == 2:
            cands.append(('split_and', m, end, k, close, ands))
        inner = body[k + 1:close].strip()
        im = re.match(r'if\s*\(', inner)
        sub = _braced_if(inner, im) if im else None
        if sub and sub[3] == len(inner) - 1 and not re.compile(r'\s*else\b').match(inner, sub[3] + 1):
            cands.append(('join_and', m, end, k, close, (cond, sub[0], inner[sub[1]:])))
    if not cands:
        return None
    kind, m, end, k, close, ops = rng.choice(cands)
    head, gap, block = body[m.start():m.end()], body[end:k], body[k:close + 1]
    unit = _indent_unit(body)
    stop = close + 1
    if kind == 'split_or':
        new = f'{head}{ops[0]}){gap}{block} else if ({ops[1]}){gap}{block}'
    elif kind == 'split_and':
        indent = _line_indent(body, m.start())
        inner_gap = gap if '\n' not in gap else f'\n{indent}{unit}'
        new = (f'{head}{ops[0]}){gap}{{\n{indent}{unit}if ({ops[1]}){inner_gap}{_deeper(block, unit)}\n{indent}}}')
    elif kind == 'join_or':
        new = f'{head}{_wrap(ops[0], "||")} || {_wrap(ops[1], "||")}){gap}{block}'
        stop = ops[2] + 1
    else:
        new = f'{head}{_wrap(ops[0], "&&")} && {_wrap(ops[1], "&&")}){gap}{_shallower(ops[2].strip(), unit)}'
    return body[:m.start()] + new + body[stop:]


ARM = r'(?:-?\d\w*|true|false)'
TERNARY = re.compile(r'^(?P<indent>[ \t]*)(?P<lhs>[A-Za-z_][\w.\->\[\]]*)\s*=(?!=)\s*(?P<cond>[^?;:]+?)\s*\?\s*(?P<a>'
                     + ARM + r')\s*:\s*(?P<b>' + ARM + r')\s*;\s*$')
ARM_STORE = re.compile(r'\s*(?P<open>\{\s*)?(?P<lhs>[A-Za-z_][\w.\->\[\]]*)\s*=(?!=)\s*(?P<v>' + ARM + r')\s*;')


def _value(literal):
    if literal in ('true', 'false'):
        return int(literal == 'true')
    try:
        return int(literal.rstrip('uUlL'), 0)
    except ValueError:
        return None


def _arms_ok(a, b):
    """Both arms are literals that give a ?: the same value as each store: a negative next to an
    unsigned or 32-bit-wide literal would put them in unsigned arithmetic."""
    va, vb = _value(a), _value(b)
    if va is None or vb is None:
        return False
    wide = any(re.search(r'[uU][lL]*$', x) or v >= 2 ** 31 for x, v in ((a, va), (b, vb)))
    return not (wide and (va < 0 or vb < 0))


def _arm(body, pos):
    """(place, literal, end) of a branch at pos that only stores a literal: 'x = 1;' or '{ x = 1; }'."""
    m = ARM_STORE.match(body, pos)
    if not m:
        return None
    end = m.end()
    if m.group('open'):
        close = re.compile(r'\s*\}').match(body, end)
        if not close:
            return None
        end = close.end()
    return m.group('lhs'), m.group('v'), end


def _if_else_stores(body):
    """(start, end, place, condition, a, b) of each 'if (c) x = A; else x = B;' (braces optional)."""
    found = []
    for m in re.finditer(r'\bif\s*\(', body):
        end = _split_top(body, m.end() - 1)
        first = _arm(body, end) if end else None
        e = re.compile(r'\s*else\b').match(body, first[2]) if first else None
        second = _arm(body, e.end()) if e else None
        if second and second[0] == first[0] and _arms_ok(first[1], second[1]):
            found.append((m.start(), second[2], first[0], body[m.end():end - 1].strip(), first[1], second[1]))
    return found


def mut_ternary(body, rng):
    """'x = c ? 1 : 0;' <-> 'if (c) x = 1; else x = 0;' (literal arms only)."""
    lines = body.split('\n')
    cands = []
    for i, line in enumerate(lines):
        m = TERNARY.match(line)
        if m and not {'?', ',', '='} & {o for o, _ in _top_level(m.group('cond'))} \
                and _arms_ok(m.group('a'), m.group('b')):
            cands.append(('expand', i))
    cands += [('fold', f) for f in _if_else_stores(body)]
    if not cands:
        return None
    kind, where = rng.choice(cands)
    if kind == 'expand':
        m = TERNARY.match(lines[where])
        ind, lhs, unit = m.group('indent'), m.group('lhs'), _indent_unit(body)
        lines[where] = (f'{ind}if ({m.group("cond").strip()})\n{ind}{unit}{lhs} = {m.group("a")};\n'
                        f'{ind}else\n{ind}{unit}{lhs} = {m.group("b")};')
        return '\n'.join(lines)
    start, end, lhs, cond, a, b = where
    cond = f'({cond})' if {'?', ',', '='} & {o for o, _ in _top_level(cond)} else cond
    return body[:start] + f'{lhs} = {cond} ? {a} : {b};' + body[end:]


TERNARY_ANY = re.compile(r'^(?P<head>[ \t]*(?:return|[A-Za-z_][\w.\->\[\]]*\s*=(?!=)))\s*(?P<cond>[^?;:]+?)\s*\?\s*'
                         r'(?P<a>' + OPERAND + r')\s*:\s*(?P<b>' + OPERAND + r')\s*;\s*$')


def mut_swap_ternary(body, rng):
    """'c ? a : b' -> '!c ? b : a' (the condition negated the way mut_swap_if_else negates one)."""
    lines = body.split('\n')
    idx = []
    for i, line in enumerate(lines):
        m = TERNARY_ANY.match(line)
        if m and not {'?', ',', '='} & {o for o, _ in _top_level(m.group('cond'))}:
            idx.append(i)
    if not idx:
        return None
    i = rng.choice(idx)
    m = TERNARY_ANY.match(lines[i])
    lines[i] = f'{m.group("head")} {_negate(m.group("cond"))} ? {m.group("b")} : {m.group("a")};'
    return '\n'.join(lines)


def _call_args(text, open_index):
    """([(start, end) of each argument], index after the call) for the call whose '(' is at open_index."""
    end = _split_top(text, open_index)
    if end is None:
        return None
    spans, depth, start, i = [], 0, open_index + 1, open_index + 1
    while i < end - 1:
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        if text[i] in '([{':
            depth += 1
        elif text[i] in ')]}':
            depth -= 1
        elif text[i] == ',' and depth == 0:
            spans.append((start, i))
            start = i + 1
        i += 1
    spans.append((start, end - 1))
    return spans, end


MUTATIONS = [mut_swap_operands, mut_reorder, mut_introduce_temp, mut_inline_temp, mut_swap_if_else,
             mut_for_while, mut_compound, mut_increment, mut_split_decl, mut_chain_assign, mut_bool_flag,
             mut_or_split, mut_ternary, mut_swap_ternary]


# ----------------------------------------------------------- typed mutations
#
# The rewrites below need types (to declare a local), so they take a Context
# besides the body: the function's parameters and return type, and the text of
# its unit and the headers it includes, where structures, globals and callees
# are declared. Each returns None when it has nothing to rewrite.

KEYWORDS = {'return', 'goto', 'delete', 'else', 'case', 'throw', 'new', 'break', 'continue', 'typedef', 'do', 'if',
            'while', 'for', 'switch', 'sizeof', 'default', 'using', 'namespace', 'operator', 'template', 'public',
            'private', 'protected', 'friend', 'extern', 'inline', 'virtual', 'true', 'false'}
SCALAR_WORDS = {'char', 'bool', 'short', 'long', 'int', 'unsigned', 'signed', 'float', 'double',
                '__int8', '__int16', '__int32', '__int64', 'wchar_t'}
QUALIFIERS = {'const', 'volatile', 'static', 'struct', 'class', 'union', 'enum'}
TYPE = (r'(?:(?:const|volatile|unsigned|signed|struct|class|union|enum|static)\s+)*'
        r'[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*(?:\s+(?:long|short|int|char|const|volatile)\b)*')
POINTERS = r'(?:\s*\*\s*(?:const\b|volatile\b)?)*'
VARIABLE = re.compile(r'(?P<type>' + TYPE + r')(?P<ptr>' + POINTERS + r')\s*\b(?P<name>[A-Za-z_]\w*)\s*'
                      r'(?P<array>\[[^\]\n]*\])?\s*(?=[=;,)]|$)')
DECLARATION = re.compile(r'^[ \t]*' + VARIABLE.pattern, re.M)
FOR_DECLARATION = re.compile(r'\bfor\s*\(\s*' + VARIABLE.pattern)   # for (T *x = ...
COMPARISONS = {'==', '!=', '<', '>', '<=', '>='}
ASSIGNMENTS = {'=', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>='}
OPERATORS = ['<<=', '>>=', '->', '&&', '||', '==', '!=', '<=', '>=', '<<', '>>', '+=', '-=', '*=', '/=', '%=', '&=',
             '|=', '^=', '++', '--', '::', '?', ':', '=', '<', '>', '+', '-', '*', '/', '%', '&', '|', '^', ',', '!',
             '~', '.']
LVALUE = r'[A-Za-z_]\w*(?:\s*(?:\.|->)\s*[A-Za-z_]\w*|\s*\[[^\[\]\n]*\])*'
ASSIGNMENT = re.compile(r'^\s*(?P<lhs>' + LVALUE + r')\s*(?P<op><<|>>|[-+*/%&|^])?=(?!=)(?P<rhs>.*);\s*$')
MEMBER_CHAIN = re.compile(r'(?<![\w.])(?<!->)(?P<chain>[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+)(?!\s*(?:\(|\[|->|\.|\w))')
_SCALAR_TYPEDEF = re.compile(r'\btypedef\s+((?:(?:const|volatile|unsigned|signed|char|short|int|long|float|double|bool|'
                             r'__int8|__int16|__int32|__int64|wchar_t)\s+)+)([A-Za-z_]\w*)\s*;')


class Context:
    """What the typed mutations know beyond the body: the function's parameter
    list and return type, text (its unit and the headers that unit includes)
    to look up structure fields, globals and callees in, and own (the unit
    alone; text when not given)."""

    def __init__(self, params='', returns='', text='', own=None, scope=''):
        self.params, self.returns, self.text = params, returns, text
        self.own = text if own is None else own
        self.scope = scope            # the class of a member function ('' for a free function): the type `this` points to
        self._cache = {}

    def _find(self, key, pattern, accept=lambda m: True):
        if key not in self._cache:
            self._cache[key] = next((m for m in re.finditer(pattern, self.text, re.M) if accept(m)), None)
        return self._cache[key]

    def global_type(self, name):
        """The declared type of a global variable, or None."""
        m = self._find(('global', name), r'^[ \t]*(?:(?:extern|static)[ \t]+)*' + VARIABLE.pattern.replace(
            r'(?P<name>[A-Za-z_]\w*)', re.escape(name)), _declares)
        return _var_type(m) if m else None

    def function_type(self, name):
        """The return type of a function declared or defined in the text, or None."""
        m = self._find(('function', name), r'^[ \t]*(?:(?:extern|static|inline|virtual)[ \t]+)*(?P<type>' + TYPE
                       + r')(?P<ptr>' + POINTERS + r')\s*(?:__\w+\s+)?\b' + re.escape(name) + r'\s*\(', _declares)
        return _var_type(m) if m else None

    def field_type(self, struct, field, depth=0):
        """The type of a structure's field (looked up in its bases too), or None."""
        if not struct:  # the expression's type is unknown, so there is no structure to look in
            return None
        key = ('field', struct, field)
        if key in self._cache:
            return self._cache[key]
        found = None
        for m in re.finditer(r'\b(?:struct|class|union)\s+' + re.escape(struct) + r'\s*(?::\s*(?:public\s+|private\s+|'
                             r'protected\s+)?(?P<base>[A-Za-z_]\w*)\s*)?\{', self.text):
            try:
                close = match_brace(self.text, m.end() - 1)
            except ValueError:
                continue
            f = re.search(r'^[ \t]*(?P<type>' + TYPE + r')(?P<ptr>' + POINTERS + r')\s*\b' + re.escape(field)
                          + r'\s*(?::\s*\w+\s*)?;', self.text[m.end():close], re.M)
            if f and f.group('type').split()[0] not in KEYWORDS:
                found = _var_type(f)
            elif m.group('base') and depth < 4:
                found = self.field_type(m.group('base'), field, depth + 1)
            if found:
                break
        self._cache[key] = found
        return found

    def is_type(self, name):
        """Whether name (a word) names a type: a scalar, or one the text declares."""
        if name in SCALAR_WORDS:
            return True
        return self._find(('type', name), r'\b(?:struct|class|union|enum)\s+' + re.escape(name) + r'\b|\btypedef\b[^;]*\b'
                          + re.escape(name) + r'\s*;') is not None

    def scalar_names(self):
        """The names the text typedefs to a scalar type (typedef unsigned int u32;)."""
        if 'scalars' not in self._cache:
            self._cache['scalars'] = {m.group(2) for m in _SCALAR_TYPEDEF.finditer(self.text)}
        return self._cache['scalars']


def _declares(m):
    return m.group('type').split()[0] not in KEYWORDS


def _open_paren(text, close):
    """The index of the '(' that matches the ')' at close, or None."""
    depth = 0
    for i in range(close, -1, -1):
        if text[i] == ')':
            depth += 1
        elif text[i] == '(':
            depth -= 1
            if depth == 0:
                return i
    return None


def _with_includes(text, folders, seen, budget):
    """text followed by the text of the files it includes (by "name" or <name>)
    that are found in folders, and of theirs, up to budget[0] characters."""
    parts = [text]
    for name in re.findall(r'^[ \t]*#[ \t]*include[ \t]*["<]([^">]+)[">]', text, re.M):
        for folder in folders:
            path = os.path.normpath(os.path.join(folder, name))
            if os.path.isfile(path):
                if path not in seen and budget[0] > 0:
                    seen.add(path)
                    with open(path, encoding='utf-8', errors='replace') as f:
                        included = f.read()
                    budget[0] -= len(included)
                    parts.append(_with_includes(included, [os.path.dirname(path)] + list(folders), seen, budget))
                break
    return '\n'.join(parts)


def function_context(text, start, folders=()):
    """The Context of the function whose body starts at start in text, a unit
    whose includes are looked up in folders."""
    head = re.sub(r'\bconst\s*$', '', text[:start - 1].rstrip()).rstrip()
    params = returns = scope = ''
    if head.endswith(')'):
        o = _open_paren(head, len(head) - 1)
        if o is not None:
            params = head[o + 1:-1]
            m = re.match(r'(?P<ret>.*?)(?P<name>[\w:~]+)\s*$', head[head.rfind('\n', 0, o) + 1:o])
            if m:
                ret = re.sub(r'\b(?:static|inline|virtual|__\w+)\b', ' ', m.group('ret'))
                returns = _normal(ret.replace('*', ' '), ret.count('*')) if ret.strip() else ''
                scope = m.group('name').rpartition('::')[0].rpartition('::')[2]
    return Context(params, returns, _with_includes(text, list(folders), set(), [MAX_CONTEXT]), text, scope)


def _normal(type_, stars=0):
    """A type's text without qualifiers that do not change it here: 'long', 'Item *'."""
    words = ' '.join(w for w in type_.split() if w not in QUALIFIERS - {'const'})
    return words + (' ' + '*' * stars if stars else '')


def _pointer(t):
    return t + ('*' if t.endswith('*') else ' *')


def _deref(t):
    return t[:-1].rstrip() if t and t.endswith('*') else None


def _struct_name(t):
    words = [w for w in t.replace('*', ' ').split() if w not in QUALIFIERS]
    return words[-1] if words else None


def _var_type(m):
    """The type a match of TYPE and POINTERS (and an optional array) declares."""
    t = _normal(m.group('type'), m.group('ptr').count('*'))
    return _pointer(t) if m.groupdict().get('array') else t


def _is_declaration(m):
    return (m.group('type').split()[0] not in KEYWORDS and m.group('type').split()[-1] not in KEYWORDS
            and m.group('name') not in KEYWORDS)


def _decl(t, name):
    return f'{t}{name}' if t.endswith('*') else f'{t} {name}'


def _squash(text):
    return re.sub(r'\s+', '', text)


def _parens(expr):
    """expr, in parentheses unless it is a single operand."""
    return f'({expr})' if _operators(expr) else expr


def _zero_compared(expr):
    """(x, '==' or '!=') when expr is x == 0 or x != 0, else None."""
    ops = _operators(expr)
    if len(ops) == 1 and ops[0][1] in ('==', '!=') and expr[ops[0][0] + 2:].strip() == '0':
        return expr[:ops[0][0]].strip(), ops[0][1]
    return None


def declared_types(body, context=None):
    """name -> type of the function's parameters and of the locals the body declares."""
    found = {}
    if context is not None and context.scope:
        found['this'] = context.scope + ' *'
    if context is not None and context.params:
        for a, b in _arg_spans(context.params, 0, len(context.params)):
            m = VARIABLE.match(context.params[a:b].strip())
            if m and _is_declaration(m):
                found[m.group('name')] = _var_type(m)
    for pattern in (DECLARATION, FOR_DECLARATION):
        for m in pattern.finditer(body):
            if _is_declaration(m):
                found.setdefault(m.group('name'), _var_type(m))
    return found


def _arg_spans(text, start, end):
    """(start, end) of each comma-separated part of text[start:end] at nesting depth 0."""
    spans, depth, i, s = [], 0, start, start
    while i < end:
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        c = text[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            spans.append((s, i))
            s = i + 1
        i += 1
    spans.append((s, end))
    return spans


def _is_cast(inner):
    """Whether the text between a pair of parentheses reads as a type (a cast) with no context at hand."""
    inner = inner.strip()
    if not re.fullmatch(TYPE + POINTERS, inner):
        return False
    words = inner.replace('*', ' ').split()
    return '*' in inner or words[-1] in SCALAR_WORDS


def _operators(text):
    """(index, operator) of the binary and ternary operators at nesting depth 0
    of an expression (a '(' that reads as a cast makes the next operator unary)."""
    out, depth, i, operand = [], 0, 0, False
    while i < len(text):
        j = _skip_literal(text, i)
        if j != i:
            i, operand = j, True
            continue
        c = text[i]
        if c.isspace():
            i += 1
        elif c in '([{':
            if depth == 0 and c == '(' and not operand:
                close = _split_top(text, i)
                if close is not None and _is_cast(text[i + 1:close - 1]):
                    i, operand = close, False
                    continue
            depth += 1
            i += 1
            operand = False
        elif c in ')]}':
            depth -= 1
            i += 1
            operand = True
        elif c.isalnum() or c == '_':
            i = re.compile(r'[\w.]+').match(text, i).end()
            operand = True
        else:
            op = next(o for o in OPERATORS + [c] if text.startswith(o, i))
            if op in ('->', '::', '.'):
                operand = False
            elif op in ('++', '--') and operand:
                pass  # postfix
            else:
                if depth == 0 and operand:
                    out.append((i, op))
                operand = False
            i += len(op)
    return out


def _parenthesized(text):
    text = text.strip()
    return text.startswith('(') and _split_top(text, 0) == len(text)


def expr_type(expr, types, context=None):
    """The type of an expression, as far as declarations tell it, or None."""
    e = expr.strip()
    if not e:
        return None
    ops = {op for _, op in _operators(e)}
    if ops & (ASSIGNMENTS | {',', '?', ':'}):
        return None
    if ops & ({'&&', '||'} | COMPARISONS):
        return 'bool'
    if ops:
        return 'long'
    if _parenthesized(e):
        return expr_type(e[1:-1], types, context)
    m = re.match(r'\*\s*\(\s*(?P<type>' + TYPE + r')(?P<ptr>' + POINTERS + r')\s*\)', e)
    if m and m.group('ptr').count('*'):
        return _normal(m.group('type'), m.group('ptr').count('*') - 1)
    if e.startswith('('):
        close = _split_top(e, 0)
        m = re.fullmatch(r'\s*(?P<type>' + TYPE + r')(?P<ptr>' + POINTERS + r')\s*', e[1:close - 1])
        if m and (m.group('ptr').count('*') or (context and context.is_type(_struct_name(m.group('type'))))
                  or _struct_name(m.group('type')) in SCALAR_WORDS):
            return _var_type(m)
        return None
    if e.startswith('!'):
        return 'bool'
    if e.startswith(('-', '~')):
        return 'long'
    if e.startswith('&'):
        t = expr_type(e[1:], types, context)
        return _pointer(t) if t else None
    if e.startswith('*'):
        return _deref(expr_type(e[1:], types, context))
    if re.fullmatch(r'\d[\w.]*', e):
        return 'float' if re.search(r'\.|^\d+f$', e) and not e.startswith('0x') else 'long'
    if e in ('true', 'false'):
        return 'bool'
    m = re.fullmatch(r'(?P<name>[A-Za-z_]\w*)\s*\((?P<args>.*)\)', e, re.S)
    if m:
        if _split_top(e, m.start('args') - 1) != len(e) or context is None:
            return None
        t = context.function_type(m.group('name'))
        return None if t == 'void' else t
    m = re.fullmatch(r'(?P<root>[A-Za-z_]\w*)(?P<rest>(?:\s*(?:\.|->)\s*[A-Za-z_]\w*|\s*\[[^\[\]]*\])*)', e)
    if not m:
        return None
    t = types.get(m.group('root')) or (context.global_type(m.group('root')) if context else None)
    for step in re.finditer(r'\[[^\[\]]*\]|(?P<op>\.|->)\s*(?P<field>[A-Za-z_]\w*)', m.group('rest')):
        if t is None:
            return None
        if step.group('op') is None:
            t = _deref(t)
            continue
        if step.group('op') == '->':
            t = _deref(t)
        if t is None or t.endswith('*') or context is None:
            return None
        t = context.field_type(_struct_name(t), step.group('field'))
    return t


def _scalar(t, context=None):
    """Whether a type is a pointer or a scalar (a standard one, or a typedef of one the context's text declares)."""
    if t is None:
        return False
    name = _struct_name(t)
    return t.endswith('*') or name in SCALAR_WORDS or (context is not None and name in context.scalar_names())


def _indent_unit(body):
    return '\t' if re.search(r'^\t', body, re.M) else '    '


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def _indented(text_lines, unit):
    return [unit + l if l.strip() else l for l in text_lines]


def _outdented(text_lines, unit):
    return [l[len(unit):] if l.startswith(unit) else l for l in text_lines]


def _block_lines(text):
    """The lines of text, without the blank ones it starts and ends with."""
    lines = text.split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _keyword_before(body, pos):
    """(keyword, its index) of the control statement whose head ends right
    before pos: 'if', 'while', 'for' or 'switch' with its condition, or
    'else' or 'do'; (None, None) when there is none."""
    i = pos - 1
    while i >= 0 and body[i].isspace():
        i -= 1
    if i < 0:
        return None, None
    if body[i] == ')':
        o = _open_paren(body, i)
        m = re.search(r'\b(if|while|for|switch)\s*$', body[:o]) if o is not None else None
    else:
        m = re.search(r'\b(else|do)$', body[:i + 1])
    return (m.group(1), m.start(1)) if m else (None, None)


def _controlled(body, pos):
    """Whether the statement at pos is the whole body of an if, else, for, while or do without braces."""
    return _keyword_before(body, pos)[0] in ('if', 'while', 'for', 'else', 'do')


def _line_start(lines, i):
    return sum(len(l) + 1 for l in lines[:i]) + len(_indent(lines[i]))


def _opens_statement(lines, i):
    """Whether a declaration can go before line i: the line begins a statement,
    or it is the whole body of a control statement (_insert_before braces
    both then)."""
    return _starts_statement(lines, i) or _controlled('\n'.join(lines), _line_start(lines, i))


def _needs_braces(lines, i):
    """Whether a declaration put before line i needs braces around the two: line
    i is the whole body of a control statement, or follows a label, which a
    declaration cannot, or is a statement (not a declaration) right in a
    switch, where a later case label would skip the declaration's
    initialization."""
    body, pos = '\n'.join(lines), _line_start(lines, i)
    if _controlled(body, pos) or not _starts_statement(lines, i, labels=False):
        return True
    o = _enclosing(body, pos, _brace_map(body))
    d = DECLARATION.match(lines[i])
    return o is not None and _block_kind(body, o) == 'switch' and not (d and _is_declaration(d))


def _insert_before(lines, i, new, unit):
    """Inserts the lines new before line i, with braces around them and line i
    when _needs_braces says so (line i must then be a whole statement)."""
    if _needs_braces(lines, i):
        if not lines[i].rstrip().endswith(';'):
            raise ValueError(f'line {i} is not a whole statement')
        ind = _indent(lines[i])
        outer = ind[:-len(unit)] if ind.endswith(unit) else ind
        lines[i:i + 1] = [outer + '{', *new, lines[i], outer + '}']
    else:
        lines[i:i] = new


def _statement_line(line):
    s = line.strip()
    return _is_simple_statement(line) or (re.match(r'return\b', s) is not None and s.endswith(';'))


def _effects(stmt):
    """(variables written, memory written (lvalue texts), names read, makes a call) of a simple statement."""
    s = stmt.strip()
    writes, memory = set(), set()
    m = DECLARATION.match(stmt)
    a = ASSIGNMENT.match(s)
    if m and _is_declaration(m):
        writes.add(m.group('name'))
        reads = _names(stmt[m.end('name'):])
    elif a:
        lhs = a.group('lhs')
        if re.fullmatch(r'[A-Za-z_]\w*', lhs):
            writes.add(lhs)
            reads = _names(a.group('rhs')) | ({lhs} if a.group('op') else set())
        else:
            memory.add(_squash(lhs))
            reads = _names(lhs) | _names(a.group('rhs'))
    else:
        reads = _names(s)
    writes |= {x or y for x, y in re.findall(r'(?:\+\+|--)\s*([A-Za-z_]\w*)|([A-Za-z_]\w*)\s*(?:\+\+|--)', s)}
    writes |= set(re.findall(r'(?<!&)&\s*([A-Za-z_]\w*)\b(?!\s*(?:->|\.|\[))', s))
    return writes, memory, reads, _has_call(s)


def _reorderable(a, b):
    """Whether two simple statements can run in either order: neither writes a
    variable the other reads or writes, nor memory (an lvalue) the other names,
    and they do not both make calls."""
    wa, ma, ra, ca = _effects(a)
    wb, mb, rb, cb = _effects(b)
    if (ca and cb) or wa & (wb | rb) or wb & (wa | ra):
        return False
    flat_a, flat_b = _squash(a), _squash(b)
    return not any(x in flat_b for x in ma) and not any(x in flat_a for x in mb)


def mut_reorder_fields(body, rng, context=None):
    """Swaps two adjacent statements that share a name, which mut_reorder
    leaves alone, when they are independent all the same: stores to two
    fields through one pointer, or two locals set from one value."""
    lines = body.split('\n')
    pairs = []
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if not (_is_simple_statement(a) and _is_simple_statement(b)) or _indent(a) != _indent(b):
            continue
        if not (_names(a) & _names(b)) and not (_has_call(a) and _has_call(b)):
            continue  # mut_reorder's
        if _reorderable(a, b) and not _controlled(body, _line_start(lines, i)):
            pairs.append(i)
    if not pairs:
        return None
    i = rng.choice(pairs)
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return '\n'.join(lines)


def mut_move_statement(body, rng, context=None):
    """Moves one statement two or more places up or down a run of simple
    statements at one indent, past only statements it is independent of
    (_reorderable): the order of a block of stores, which the adjacent swaps
    of mut_reorder and mut_reorder_fields reach only in several steps."""
    lines = body.split('\n')
    moves = []
    for i, line in enumerate(lines):
        if not _is_simple_statement(line):
            continue
        for step in (-1, 1):
            j = i + step
            while (0 <= j < len(lines) and _is_simple_statement(lines[j]) and _indent(lines[j]) == _indent(line)
                   and _reorderable(line, lines[j])):
                if abs(j - i) >= 2:
                    moves.append((i, j))
                j += step
    moves = [(i, j) for i, j in moves
             if not any(_controlled(body, _line_start(lines, k)) for k in range(min(i, j), max(i, j) + 1))]
    if not moves:
        return None
    i, j = rng.choice(moves)
    lines.insert(j, lines.pop(i))
    return '\n'.join(lines)


def _call_spans(text):
    """(open, close) indices of the parentheses of each call's argument list in text."""
    out = []
    for m in re.finditer(r'([A-Za-z_]\w*)\s*\(', text):
        if m.group(1) in KEYWORDS:
            continue
        end = _split_top(text, m.end() - 1)
        if end is not None:
            out.append((m.end() - 1, end - 1))
    return out


TRIVIAL = re.compile(r'[A-Za-z_]\w*|-?\d[\w.]*|".*"|\'.*\'')


def mut_hoist_argument(body, rng, context=None):
    """Computes a call's argument into a new local just before the statement,
    so that it is evaluated before the call's other arguments."""
    lines = body.split('\n')
    types = declared_types(body, context)
    cands = []
    for i, line in enumerate(lines):
        if not _statement_line(line) or not _opens_statement(lines, i):
            continue
        for open_, close in _call_spans(line):
            for a, b in _arg_spans(line, open_ + 1, close):
                part = line[a:b]
                arg = part.strip()
                if not arg or TRIVIAL.fullmatch(arg) or '++' in arg or '--' in arg:
                    continue
                t = expr_type(arg, types, context)  # None for an assignment
                if t is not None and t != 'void':
                    s = a + len(part) - len(part.lstrip())
                    cands.append((i, s, s + len(arg), arg, t))
    if not cands:
        return None
    i, a, b, arg, t = rng.choice(cands)
    name = _fresh_temp(body)
    line = lines[i]
    lines[i] = line[:a] + name + line[b:]
    _insert_before(lines, i, [f'{_indent(line)}{_decl(t, name)} = {arg};'], _indent_unit(body))
    return '\n'.join(lines)


def _writes_name(line, name):
    return re.search(r'\b%s\b\s*(?:(?:<<|>>|[-+*/%%&|^])?=(?!=)|\+\+|--)|(?:\+\+|--)\s*%s\b|(?<!&)&\s*%s\b'
                     % (name, name, name), line) is not None


def mut_inline_local(body, rng, context=None):
    """Replaces a local used once by the expression it is set to, for the
    types mut_inline_temp leaves alone (short, bool, char, float, ...); a cast
    keeps the local's type when the expression's differs."""
    lines = body.split('\n')
    types = declared_types(body, context)
    cands = []
    for i, line in enumerate(lines):
        m = DECLARATION.match(line)
        if not m or not _is_declaration(m) or TEMP_DECL.match(line) or m.group('array') \
                or 'volatile' in m.group('type') + m.group('ptr'):
            continue
        e = re.fullmatch(r'\s*=\s*(?P<expr>[^;]+);\s*', line[m.end():])
        if not e:
            continue
        name, expr = m.group('name'), e.group('expr').strip()
        uses = [j for j, l in enumerate(lines) if j != i and re.search(r'\b%s\b' % name, l)]
        if len(uses) != 1 or uses[0] < i or len(re.findall(r'\b%s\b' % name, body)) != 2:
            continue
        j = uses[0]
        if _writes_name(lines[j], name) or lines[j].lstrip().startswith(('for', 'while', 'do')):
            continue
        if any(_names(expr) & _names(l) or _has_call(l) for l in lines[i + 1:j]):
            continue
        cands.append((i, j, name, expr, _var_type(m)))
    if not cands:
        return None
    i, j, name, expr, t = rng.choice(cands)
    value = _parens(expr)
    if expr_type(expr, types, context) not in (t, None):
        value = f'({t}){value}'
    lines[j] = re.sub(r'\b%s\b' % name, lambda _: value, lines[j])
    del lines[i]
    return '\n'.join(lines)


def _chain_reads(line):
    """The member chains (p->a.b) a line reads: (squashed text, start, end)."""
    out = []
    for m in MEMBER_CHAIN.finditer(line):
        before, after = line[:m.start()], line[m.end():]
        if re.search(r'(?:&|\+\+|--)\s*$', before) or re.match(r'\s*(?:(?:<<|>>|[-+*/%&|^])?=(?!=)|\+\+|--)', after):
            continue
        out.append((_squash(m.group('chain')), m.start(), m.end()))
    return out


def _writes_chain(line, key):
    """Whether a line writes the chain key (squashed), takes its address, or sets the chain's root."""
    root = re.match(r'[A-Za-z_]\w*', key).group(0)
    flat = _squash(line)
    k = re.escape(key)
    return (re.search(k + r'(?:(?:<<|>>|[-+*/%&|^])?=(?!=)|\+\+|--)', flat) is not None
            or re.search(r'(?:&|\+\+|--)' + k + r'(?![\w\[])', flat) is not None or _writes_name(line, root))


def mut_cache_field(body, rng, context=None):
    """Reads p->field (or a longer chain) into a new local before its first
    read, and reads the local instead to the end of that block, or until the
    field or p is written."""
    lines = body.split('\n')
    types = declared_types(body, context)
    found = {}  # chain -> (the line of its first read, its type)
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(('//', '#', 'else', '}', 'case', 'default')) or not s:
            continue
        d = DECLARATION.match(line)
        whole = _squash(line[d.end():]).lstrip('=').rstrip(';') if d and _is_declaration(d) else None
        for key, _, _ in _chain_reads(line):
            if key not in found and key != whole:  # caching all of a local's value only renames it
                found[key] = i, expr_type(key, types, context)
    braced = {i: _needs_braces(lines, i) for i, _ in found.values()}  # then the local lives in line i only
    cands = [k for k, (i, t) in found.items()
             if _scalar(t, context) and (not braced[i] or lines[i].rstrip().endswith(';')) and _opens_statement(lines, i)]
    if not cands:
        return None
    key = rng.choice(cands)
    i, t = found[key]
    field = re.findall(r'[A-Za-z_]\w*', key)[-1]
    used = set(re.findall(r'[A-Za-z_]\w*', body)) | set(types)
    name = field if field not in used and field not in KEYWORDS else _fresh_temp(body)
    depth = 0
    for j in range(i, len(lines)):
        if j > i and (braced[i] or _writes_chain(lines[j], key)):
            break
        line = lines[j]
        for k, a, b in reversed(_chain_reads(line)):
            if k == key:
                line = line[:a] + name + line[b:]
        lines[j] = line
        depth += line.count('{') - line.count('}')
        if depth < 0 or (j == i and _writes_chain(lines[j], key)):
            break
    _insert_before(lines, i, [f'{_indent(lines[i])}{_decl(t, name)} = {key};'], _indent_unit(body))
    return '\n'.join(lines)


def mut_uncache_field(body, rng, context=None):
    """Replaces a local set from p->field and read more than once by p->field
    itself at each read (mut_inline_temp and mut_inline_local take the locals
    read once)."""
    lines = body.split('\n')
    types = declared_types(body, context)
    cands = []
    for i, line in enumerate(lines):
        m = DECLARATION.match(line)
        if not m or not _is_declaration(m) or m.group('array'):
            continue
        e = re.fullmatch(r'\s*=\s*(?P<expr>[^;]+?)\s*;\s*', line[m.end():])
        if not e or not MEMBER_CHAIN.fullmatch(e.group('expr')):
            continue
        name, expr = m.group('name'), e.group('expr')
        if len(re.findall(r'(?<![.>])\b%s\b' % name, body)) < 3 or any(_writes_name(l, name) for l in lines[i + 1:]):
            continue
        if expr_type(expr, types, context) not in (_var_type(m), None):
            continue
        cands.append((i, name, expr))
    if not cands:
        return None
    i, name, expr = rng.choice(cands)
    for j in range(i + 1, len(lines)):
        lines[j] = re.sub(r'(?<![.>])\b%s\b' % name, lambda _: expr, lines[j])
    del lines[i]
    return '\n'.join(lines)


def _boolean(expr):
    """Whether an expression is a comparison, a negation or a logical and/or."""
    return expr_type(expr, {}) == 'bool'


def _bool_operands(text, s, e, out):
    """Collects (start, end, kind) of the operands of a condition text[s:e]
    that are tested for truth: kind 'value' (x), 'not' (!x) or 'compare'
    (x == 0, x != 0)."""
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    if s >= e:
        return
    part = text[s:e]
    ops = _operators(part)
    if _parenthesized(part):
        if _boolean(part[1:-1]):
            _bool_operands(text, s + 1, e - 1, out)
        else:
            out.append((s, e, 'value'))
        return
    for sep in ('||', '&&'):
        cuts = [k for k, op in ops if op == sep]
        if cuts:
            prev = s
            for k in cuts:
                _bool_operands(text, prev, s + k, out)
                prev = s + k + 2
            _bool_operands(text, prev, e, out)
            return
    kinds = {op for _, op in ops}
    if kinds & (ASSIGNMENTS | {'?', ':', ','}):
        return
    if part.startswith('!') and not ops:
        out.append((s, e, 'not'))
        rest = part[1:].strip()
        if _parenthesized(rest) and _boolean(rest[1:-1]):
            _bool_operands(text, s + part.index('(') + 1, e - 1, out)
    elif _zero_compared(part):
        out.append((s, e, 'compare'))
    elif not kinds & COMPARISONS:
        out.append((s, e, 'value'))


def _zero_test(part, kind):
    """The other spelling of a truth test: x <-> x != 0, !x <-> x == 0."""
    if kind == 'value':
        return f'{_parens(part)} != 0'
    if kind == 'not':
        return f'{part[1:].strip()} == 0'
    x, op = _zero_compared(part)
    return x if op == '!=' else f'!{_parens(x)}'


def mut_zero_compare(body, rng, context=None):
    """x <-> x != 0 and !x <-> x == 0 where a condition tests x for truth, and
    b = x <-> b = x != 0 when b is a bool (a local, a field, or the return value)."""
    types = declared_types(body, context)
    cands = []
    for m in re.finditer(r'\b(?:if|while)\s*\(', body):
        end = _split_top(body, m.end() - 1)
        if end is not None:
            found = []
            _bool_operands(body, m.end(), end - 1, found)
            cands.extend((s, e, _zero_test(body[s:e], kind)) for s, e, kind in found)
    sets = [m.span('rhs') for m in re.finditer(r'(?m)^[ \t]*(?P<lhs>(?:bool\s+)?' + LVALUE
                                               + r')\s*=(?!=)\s*(?P<rhs>[^;]+?)\s*;', body)
            if m.group('lhs').startswith('bool') or expr_type(m.group('lhs'), types, context) == 'bool']
    if context is not None and context.returns == 'bool':
        sets += [m.span('rhs') for m in re.finditer(r'\breturn\s+(?P<rhs>[^;]+?)\s*;', body)]
    for s, e in sets:
        rhs = body[s:e]
        compared = _zero_compared(rhs)
        if compared and compared[1] == '!=':
            cands.append((s, e, compared[0]))
        elif not ({op for _, op in _operators(rhs)} & ({'&&', '||', '?', ':', ','} | COMPARISONS | ASSIGNMENTS)
                  or rhs in ('true', 'false') or rhs.startswith('!')):
            cands.append((s, e, f'{_parens(rhs)} != 0'))
    if not cands:
        return None
    s, e, new = rng.choice(cands)
    return body[:s] + new + body[e:]


def _brace_map(body):
    pairs, stack, i = {}, [], 0
    while i < len(body):
        j = _skip_literal(body, i)
        if j != i:
            i = j
            continue
        if body[i] == '{':
            stack.append(i)
        elif body[i] == '}' and stack:
            o = stack.pop()
            pairs[o], pairs[i] = i, o
        i += 1
    return pairs


def _block_kind(body, open_):
    """What the '{' at open_ opens: 'if', 'else', 'while', 'for', 'switch', 'do', 'block' or 'other'."""
    keyword, _ = _keyword_before(body, open_)
    before = body[:open_].rstrip()
    return keyword or ('block' if not before or before[-1] in ';{}:' else 'other')


def _enclosing(body, pos, pairs):
    """The '{' of the innermost block around pos, or None."""
    best = None
    for o, c in pairs.items():
        if body[o] == '{' and o < pos < c and (best is None or o > best):
            best = o
    return best


def _reaches_return(body, i, name, pairs):
    """Whether control at i, between statements, goes straight to 'return
    name;': through ends of blocks (past an if's else), labels, gotos and a
    switch's break, and nothing else."""
    for _ in range(64):
        i = _skip_space(body, i)
        if i >= len(body):
            return False
        if body[i] == '}':
            o = pairs.get(i)
            kind = _block_kind(body, o) if o is not None else 'other'
            if kind not in ('if', 'else', 'block', 'switch'):
                return False
            i += 1
            if kind == 'if':
                j = _skip_space(body, i)
                if re.match(r'else\b', body[j:j + 5]):
                    j = _skip_space(body, j + 4)
                    if j >= len(body) or body[j] != '{':
                        return False
                    i = pairs[j] + 1
            continue
        m = re.compile(r'goto\s+([A-Za-z_]\w*)\s*;').match(body, i)
        if m:
            label = re.search(r'(?:^|[;{}])\s*%s\s*:(?!:)' % m.group(1), body)
            if not label:
                return False
            i = label.end()
            continue
        m = re.compile(r'(?!default\b)[A-Za-z_]\w*\s*:(?!:)').match(body, i)
        if m:
            i = m.end()
            continue
        if re.compile(r'break\s*;').match(body, i):
            o = _enclosing(body, i, pairs)
            while o is not None and _block_kind(body, o) not in ('switch', 'while', 'for', 'do'):
                o = _enclosing(body, o, pairs)
            if o is None or _block_kind(body, o) != 'switch':
                return False
            i = pairs[o] + 1
            continue
        return re.compile(r'return\s+%s\s*;' % re.escape(name)).match(body, i) is not None
    return False


def mut_early_return(body, rng, context=None):
    """In a function that ends 'return r;' with r a local: 'r = x;' from where
    control goes straight to that return -> 'return x;' (dropping a goto that
    follows), and back: 'if (c) { ...; return x; }' followed by the rest of
    its block -> 'if (c) { ...; r = x; } else { the rest }'."""
    last = re.search(r'\breturn\s+([A-Za-z_]\w*)\s*;\s*$', body)
    declared = {m.group('name'): m.start() for m in DECLARATION.finditer(body) if _is_declaration(m)}
    if not last or last.group(1) not in declared:
        return None
    name = last.group(1)
    pairs = _brace_map(body)
    cands = []
    for m in re.finditer(r'(?m)^(?P<ind>[ \t]*)%s\s*=(?!=)\s*(?P<x>[^;]+);' % re.escape(name), body):
        if _reaches_return(body, m.end(), name, pairs):
            g = re.compile(r'[ \t]*\n[ \t]*goto\s+\w+\s*;').match(body, m.end())
            cands.append((m.start(), g.end() if g else m.end(), f'{m.group("ind")}return {m.group("x").strip()};'))
    for r in re.finditer(r'\breturn\s+(?P<x>[^;]+);', body):
        if r.start() < last.start():
            found = _single_exit(body, r, name, declared[name], last.start(), pairs, _indent_unit(body))
            if found is not None:
                cands.append(found)
    if not cands:
        return None
    s, e, new = rng.choice(cands)
    return body[:s] + new + body[e:]


def _single_exit(body, r, name, declared_at, last, pairs, unit):
    """For mut_early_return: (start, end, text) that turns the return r, the
    last statement of 'if (c) { ...; return x; } rest' (rest: the rest of the
    block), into 'if (c) { ...; name = x; } else { rest }'; None when that
    does not keep the meaning."""
    o = _enclosing(body, r.start(), pairs)
    if o is not None and _block_kind(body, o) == 'if' and not body[r.end():pairs[o]].strip():
        then_end, (keyword, if_start) = pairs[o] + 1, _keyword_before(body, o)
    elif _controlled(body, r.start()):
        o, then_end, (keyword, if_start) = None, r.end(), _keyword_before(body, r.start())
    else:
        return None
    if keyword != 'if' or if_start < declared_at or re.search(r'\belse\s*$', body[:if_start]):
        return None
    k = _skip_space(body, then_end)
    if re.match(r'else\b', body[k:k + 5]):
        return None
    outer = _enclosing(body, if_start, pairs)
    if outer is not None and _block_kind(body, outer) not in ('if', 'else', 'block'):
        return None
    rest_end = pairs[outer] if outer is not None else last
    rest = _block_lines(body[then_end:rest_end])
    if any(re.match(r'\s*(?:case\b|default\s*:|[A-Za-z_]\w*\s*:(?!:))', l) for l in rest):
        return None
    if not _reaches_return(body, rest_end, name, pairs):
        return None
    line = body[body.rfind('\n', 0, if_start) + 1:if_start]
    ind = line if not line.strip() else _indent(line)
    assign = f'{name} = {r.group("x").strip()};'
    if o is not None:
        then = body[if_start:r.start()] + assign + body[r.end():then_end]
    else:
        then = body[if_start:r.start()].rstrip() + f'\n{ind}{{\n{ind}{unit}{assign}\n{ind}}}'
    if not rest:
        return if_start, then_end, then
    closing = body[body.rfind('\n', 0, rest_end) + 1:rest_end]
    return (if_start, rest_end, then + f'\n{ind}else\n{ind}{{\n' + '\n'.join(_indented(rest, unit)) + f'\n{ind}}}\n'
            + (closing if not closing.strip() else ''))


def mut_redundant_cast(body, rng, context=None):
    """Casts a variable where it is read to the type it already has, or takes
    away such a cast."""
    types = declared_types(body, context)
    cands = []
    for m in re.finditer(r'\((?P<type>' + TYPE + r')(?P<ptr>' + POINTERS + r')\)\s*(?P<name>[A-Za-z_]\w*)\b'
                         r'(?!\s*(?:\(|\[|\.|->))', body):
        if types.get(m.group('name')) == _var_type(m):
            cands.append((m.start(), m.end(), m.group('name')))
    lines = body.split('\n')
    offset = 0
    for line in lines:
        s = line.strip()
        if _statement_line(line) or re.match(r'(?:if|while)\s*\(', s):
            d = DECLARATION.match(line)
            skip = d.end('name') if d and _is_declaration(d) else 0
            for m in re.finditer(r'(?<![\w.>&])(?<!\+\+)(?<!--)(?<!::)\b(?P<name>[A-Za-z_]\w*)\b(?!\s*(?:\(|\[|\.|->|\+\+|--|'
                                 r'(?:<<|>>|[-+*/%&|^])?=(?!=)))', line):
                t = types.get(m.group('name'))
                if m.start() >= skip and _scalar(t, context) and not re.search(r'\)\s*$', line[:m.start()]):
                    cands.append((offset + m.start(), offset + m.end(), f'({t}){m.group("name")}'))
        offset += len(line) + 1
    if not cands:
        return None
    a, b, new = rng.choice(cands)
    return body[:a] + new + body[b:]


def mut_do_while(body, rng, context=None):
    """while (c) { b } <-> if (c) { do { b } while (c); }"""
    unit = _indent_unit(body)
    cands = []
    for m in re.finditer(r'(?m)^(?P<ind>[ \t]*)(?P<kw>while|if)\s*\(', body):
        close = _split_top(body, m.end() - 1)
        if close is None:
            continue
        k = _skip_space(body, close)
        if k >= len(body) or body[k] != '{':
            continue
        end = match_brace(body, k)
        ind, cond = m.group('ind'), body[m.end():close - 1].strip()
        if m.group('kw') == 'while':
            inner = _indented(_block_lines(body[k + 1:end]), unit)
            cands.append((m.start(), end + 1, f'{ind}if ({cond})\n{ind}{{\n{ind}{unit}do\n{ind}{unit}{{\n'
                          + '\n'.join(inner) + f'\n{ind}{unit}}} while ({cond});\n{ind}}}'))
            continue
        d = re.compile(r'\s*do\s*\{').match(body, k + 1)
        if not d:
            continue
        dend = match_brace(body, d.end() - 1)
        w = re.compile(r'\s*while\s*\((?P<c>.*)\)\s*;\s*$', re.S).match(body, dend + 1, end)
        j = _skip_space(body, end + 1)
        if w and _squash(w.group('c')) == _squash(cond) and not re.match(r'else\b', body[j:j + 5]):
            inner = _outdented(_block_lines(body[d.end():dend]), unit)
            cands.append((m.start(), end + 1, f'{ind}while ({cond})\n{ind}{{\n' + '\n'.join(inner) + f'\n{ind}}}'))
    if not cands:
        return None
    s, e, new = rng.choice(cands)
    return body[:s] + new + body[e:]


def mut_single_exit(body, rng, context=None):
    """The first step to one exit, which mut_early_return cannot take: in a
    function that returns a value from more than one place and ends 'return
    x;' (x not a local), declare 'T result;' (T the function's return type)
    and end 'result = x; return result;'. mut_early_return then turns the
    earlier returns into stores to result one at a time. Not when that last
    return is the unbraced body of a control statement."""
    returns = (context.returns if context else '').strip()
    if not returns or returns == 'void' or '&' in returns:
        return None
    exits = list(re.finditer(r'\breturn\s+(?P<x>[^;]+);', body))
    last = ([None] + list(re.finditer(r'(?m)^(?P<ind>[ \t]*)return\s+(?P<x>[^;]+);[ \t]*$', body)))[-1]
    if len(exits) < 2 or not last or body[last.end():].strip() or exits[-1].start() != last.start() + len(last.group('ind')):
        return None
    declared = {m.group('name') for m in DECLARATION.finditer(body) if _is_declaration(m)}
    if last.group('x').strip() in declared or _controlled(body, last.start() + len(last.group('ind'))):
        return None                     # the last return the whole body of an unbraced while/if: two statements there
    name, n = 'result', 0
    while re.search(r'\b%s\b' % name, body) or (context and re.search(r'\b%s\b' % name, context.params)):
        n += 1
        name = f'result{n}'
    unit = _indent_unit(body)
    decl = f'{returns}{name};' if returns.endswith('*') else f'{returns} {name};'
    ind = last.group('ind')
    tail = f'{ind}{name} = {last.group("x").strip()};\n{ind}return {name};'
    head = body[:last.start()] + tail + body[last.end():]
    return '\n' + unit + decl + head if head.startswith('\n') else unit + decl + '\n' + head


STATIC_HELPER = re.compile(r'^[ \t]*static[ \t]+(?:(?:__forceinline|__inline|inline)[ \t]+)?(?P<ret>(?:const[ \t]+)?[A-Za-z_]\w*'
                           r'(?:[ \t]*\*)*)[ \t]*\b(?P<name>[A-Za-z_]\w*)[ \t]*\((?P<params>[^()]*)\)\s*\{\s*'
                           r'return\s+(?P<expr>[^;{}]+);\s*\}', re.M)
SIMPLE_ARG = re.compile(r'^\s*(?:[A-Za-z_]\w*|-?\d[\w.]*)\s*$')


def _helper_params(params):
    """The (type, name) pairs of a helper's parameter list, or None when a
    parameter cannot be named or its type cannot be written as a cast (a
    reference, an array, a function pointer, a default or '...')."""
    out = []
    for p in [p.strip() for p in params.split(',')] if params.strip() not in ('', 'void') else []:
        m = re.search(r'([A-Za-z_]\w*)\s*$', p)
        if not m or m.group(1) in NOT_NAMES or p == m.group(1) or re.search(r'[&\[\]()=]|\.\.\.', p):
            return None
        out.append((p[:m.start()].strip(), m.group(1)))
    return out


def _param_name(name):
    """A parameter name as a whole word that is not a member after '.' or '->' ('a>b' still reads b)."""
    return r'(?<![\w.])(?<!->)%s\b' % re.escape(name)


def _volatile_names(body, context):
    """The locals and parameters declared volatile."""
    found = set()
    for text in (context.params if context else '', body):
        for m in VARIABLE.finditer(text):
            if 'volatile' in m.group('type') + m.group('ptr'):
                found.add(m.group('name'))
    return found


def _plain_arg(a, volatile, context):
    """A name or literal that reads the same however often it is evaluated: not a volatile
    local or parameter, and not a macro (an object-like macro can stand for anything)."""
    if not SIMPLE_ARG.match(a):
        return False
    a = a.strip()
    if a in volatile:
        return False
    return not (context and re.search(r'^[ \t]*#[ \t]*define[ \t]+%s\b' % re.escape(a), context.text, re.M))


def _declared_more_than_once(name, text):
    """Whether name has more than one definition or prototype in text (overloads, or a prototype
    besides the definition): the call might not be the definition the helper table holds."""
    n = 0
    for m in re.finditer(r'^[ \t]*(?:[A-Za-z_][\w:]*[ \t*&]+)+\**[ \t]*%s[ \t]*\(' % re.escape(name), text, re.M):
        words = re.findall(r'[A-Za-z_]\w*', m.group(0))
        if len(words) >= 2 and words[-2] not in KEYWORDS:
            n += 1
    return n > 1


def mut_inline_helper(body, rng, context=None):
    """A call to a static helper defined in the function's own unit whose body
    is one 'return e;' -> e itself, cast to the helper's return type, with
    each parameter replaced by its argument cast to the parameter's type (so a
    narrowing or a float conversion stays). An argument the expression reads
    other than exactly once must be a plain name or literal, so nothing is
    evaluated twice and no call is dropped; in an expression with '?', '&&' or
    '||', where a read can be skipped, every argument must be. A plain name is
    not a volatile local or a macro. Left alone: a name with more than one
    definition or prototype (an overload could be the one called), a helper
    whose expression has a comment, and one whose expression reads a name the
    function has a local or parameter of (it would read the local)."""
    own = getattr(context, 'own', None) if context else None
    if not own:
        return None
    helpers = {}
    types = declared_types(body, context)
    for m in STATIC_HELPER.finditer(own):
        params = _helper_params(m.group('params'))
        expr = m.group('expr').strip()
        if params is None or '//' in expr or '/*' in expr or _declared_more_than_once(m.group('name'), context.text):
            continue
        names = {n for _, n in params}
        free = set(re.findall(r'(?<![\w.])(?<!->)[A-Za-z_]\w*', expr)) - names - KEYWORDS
        if free & set(types):
            continue                                            # it would read the caller's local of that name
        helpers[m.group('name')] = (m.group('ret').strip(), params, expr)
    if not helpers:
        return None
    volatile = _volatile_names(body, context)
    cands = []
    for m in re.finditer(r'(?<![\w.])(?<!->)(?P<name>' + '|'.join(map(re.escape, helpers)) + r')\s*\(', body):
        ret, params, expr = helpers[m.group('name')]
        got = _call_args(body, m.end() - 1)
        if got is None:
            continue
        spans, end = got
        args = [body[s:e].strip() for s, e in spans]
        if args == [''] and not params:
            args = []
        if len(args) != len(params):
            continue
        conditional = re.search(r'\?|&&|\|\|', expr) is not None
        if any((conditional or len(re.findall(_param_name(p), expr)) != 1) and not _plain_arg(a, volatile, context)
               for (_, p), a in zip(params, args)):
            continue
        # substitute every parameter at once, so an argument naming another parameter stays put
        lookup = {p: f'(({ty}){a})' if _plain_arg(a, volatile, context) else f'(({ty})({a}))'
                  for (ty, p), a in zip(params, args)}
        new = expr
        if params:
            new = re.sub('|'.join(_param_name(p) for _, p in params), lambda x: lookup[x.group(0)], expr)
        cands.append((m.start(), end, f'(({ret})({new}))'))
    if not cands:
        return None
    s, e, new = rng.choice(cands)
    return body[:s] + new + body[e:]


TYPED_MUTATIONS = [mut_reorder_fields, mut_move_statement, mut_hoist_argument, mut_inline_local, mut_cache_field,
                   mut_uncache_field, mut_zero_compare, mut_early_return, mut_redundant_cast, mut_do_while,
                   mut_single_exit, mut_inline_helper]
PARSE_ERRORS = (ValueError, IndexError)  # unbalanced text (match_brace), a declaration with nowhere to go
