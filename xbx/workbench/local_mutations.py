"""Mutations for moves permute_lib lacks, sampled by the search like its own.

mut_wrap_block    wraps a run of complete statements that starts with a
                  declaration in a bare { } block, ending the declared locals'
                  lifetime early.
mut_sse_store     rewrites a plain `a = b;` copy as _mm_store_ps((float *)&a, b),
                  in a body that already uses SSE intrinsics. Non-vector copies
                  fail to compile and are discarded by the search.
mut_parenthesise  wraps one `a * b` / `a / b` term in parentheses (the compiler
                  schedules a parenthesised float term as a unit).
mut_hoist_decl    `T x = e;` inside a loop becomes `T x;` before the outermost
                  enclosing loop and `x = e;` in place: the variable outlives
                  the loop, which keeps a store the compiler would otherwise drop.
Signature (body, rng) -> new body or None, like permute_lib.MUTATIONS.

Derived from the h2m workbench of the halo2-decompiled project (GPL-3.0-or-later).
"""
import re

DECL = re.compile(r'^\s*(?:const\s+)?[A-Za-z_][\w:<>]*(?:\s+const)?[\s\*&]+(?:const\s+)?[A-Za-z_]\w*\s*(?:=|\(|;|\[)')
SKIP = ('return', 'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default', 'goto', '//', '#', '{', '}')


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def _statement_runs(lines, start):
    """Ends j (exclusive) such that lines[start:j] is a sequence of complete
    statements at start's indentation (nested blocks balanced, no dangling
    else)."""
    ind = len(_indent(lines[start]))
    depth, ends = 0, []
    for j in range(start, len(lines)):
        s = lines[j].strip()
        if depth == 0 and j > start and s and len(_indent(lines[j])) < ind:
            break  # the enclosing block closes
        depth += lines[j].count('{') - lines[j].count('}')
        if depth < 0:
            break
        nxt = lines[j + 1].strip() if j + 1 < len(lines) else ''
        if depth == 0 and (s.endswith(';') or s == '}') and not nxt.startswith('else'):
            ends.append(j + 1)
    return ends


def mut_wrap_block(body, rng):
    lines = body.split('\n')
    starts = [i for i, l in enumerate(lines)
              if i > 1 and DECL.match(l) and not l.strip().startswith(SKIP) and l.rstrip().endswith(';')]
    rng.shuffle(starts)
    for i in starts[:8]:
        ends = [e for e in _statement_runs(lines, i) if e > i]
        if not ends:
            continue
        e = rng.choice(ends)
        ind = _indent(lines[i])
        unit = '\t' if ind.startswith('\t') or not ind else ' ' * 4
        inner = [(unit + l) if l.strip() else l for l in lines[i:e]]
        return '\n'.join(lines[:i] + [ind + '{'] + inner + [ind + '}'] + lines[e:])
    return None


COPY = re.compile(r'^(\s*)([A-Za-z_][\w\.\->\[\]]*)\s*=\s*([^=;][^;]*);\s*$')


def mut_sse_store(body, rng):
    if '_mm_' not in body and '__m128' not in body:
        return None                                    # no SSE in this function: every candidate would fail to compile
    lines = body.split('\n')
    cands = [i for i, l in enumerate(lines)
             if COPY.match(l) and ('.' in l or '->' in l or '[' in l) and '_mm_' not in l.split('=')[0]]
    if not cands:
        return None
    i = rng.choice(cands)
    m = COPY.match(lines[i])
    lines[i] = f'{m.group(1)}_mm_store_ps((float *)&{m.group(2)}, {m.group(3).strip()});'
    return '\n'.join(lines)


PRODUCT = re.compile(r'(?<![\w\)\]\(])([A-Za-z_][\w\.\->\[\]]*\s*[\*/]\s*[A-Za-z_][\w\.\->\[\]]*)(?![\w\(\[])')


def mut_parenthesise(body, rng):
    spots = [m for m in PRODUCT.finditer(body) if not body[:m.start()].rstrip().endswith('(')
             or not body[m.end():].lstrip().startswith(')')]
    spots = [m for m in spots if '//' not in body[body.rfind('\n', 0, m.start()):m.start()]]
    if not spots:
        return None
    m = rng.choice(spots)
    return body[:m.start()] + '(' + m.group(1) + ')' + body[m.end():]


INIT_DECL = re.compile(r'^(\s*)(?:const\s+)?((?:unsigned\s+|signed\s+)?[A-Za-z_][\w:]*(?:\s*\*)*)\s+(?:const\s+)?([A-Za-z_]\w*)\s*=\s*([^;]+);\s*$')
LOOP_HEAD = re.compile(r'^\s*(for|while|do)\b')


def mut_hoist_decl(body, rng):
    lines = body.split('\n')
    cands = []
    for i, l in enumerate(lines):
        m = INIT_DECL.match(l)
        if not m or l.strip().startswith(SKIP) or '&' in m.group(2):
            continue
        ind = len(m.group(1))
        loop = None
        for j in range(i - 1, -1, -1):
            lj = lines[j]
            if lj.strip() and len(_indent(lj)) < ind and LOOP_HEAD.match(lj):
                loop = j
                ind = len(_indent(lj))
        if loop is not None:
            cands.append((i, loop, m))
    if not cands:
        return None
    i, loop, m = rng.choice(cands)
    ind = _indent(lines[loop])
    lines[i] = f'{m.group(1)}{m.group(3)} = {m.group(4)};'
    lines.insert(loop, f'{ind}{m.group(2)} {m.group(3)};')
    return '\n'.join(lines)


LOCAL_MUTATIONS = [mut_wrap_block, mut_sse_store, mut_parenthesise, mut_hoist_decl]
