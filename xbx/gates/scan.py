"""scan: what a unit file may contain. Text only: needs no game file and no compiler.

A unit is plain C++ that the original compiler could have been given. Refused:
  - inline assembly in any spelling, __emit, naked functions
  - every #pragma except pack; #line, #import, #using; token pasting (##),
    trigraphs and digraphs (ways to spell a refused word without writing it)
  - __declspec other than noreturn, novtable and align
  - __FILE__, __DATE__, __TIME__, __TIMESTAMP__ (they compile the build machine into the code)
  - bytes that are not ASCII
  - #include of anything outside the project's include directories
  - compiler flags outside the project's allow-list
  - a /* sym: */ line for a name that is not a special member (??...)
  - a file whose name is not func_XXXXXXXX.cpp (upper-case hex) for a function
    in the table, a function that has a unit in both tiers, a unit for a
    compiler-made stub
  - statements that only steer the compiler: a bare expression statement
    (`m_count;`), a self-assignment (`x = x;`), a do { } while (0) wrapper
Whether a number written in a unit is really an address is checked on the
machine code, by the match gate.
"""
import os
import re

from .. import units
from .common import Report

UNIT_NAME = re.compile(r'^func_([0-9A-F]{8})\.cpp$')
RULES = [
    (re.compile(r'\b(?:__asm|_asm|__asm__|asm)\b'), 'inline assembly is not allowed: write C++'),
    (re.compile(r'\b(?:__emit|_emit)\b'), '__emit is not allowed'),
    (re.compile(r'\bnaked\b'), 'naked functions are not allowed'),
    (re.compile(r'^[ \t]*#[ \t]*pragma\b(?![ \t]+pack\b)', re.M), '#pragma is not allowed (only #pragma pack)'),
    (re.compile(r'^[ \t]*#[ \t]*(?:line|import|using)\b', re.M), '#line, #import and #using are not allowed'),
    (re.compile(r'##'), 'token pasting (##) is not allowed'),
    (re.compile(r'\?\?[=/\'()!<>-]|%:|<%|%>|<:|:>'), 'trigraphs and digraphs are not allowed'),
    (re.compile(r'\b__(?:FILE|DATE|TIME|TIMESTAMP)__\b'), '__FILE__, __DATE__, __TIME__ and __TIMESTAMP__ are not allowed'),
    (re.compile(r'\bdo\s*\{[^{}]*\}\s*while\s*\(\s*(?:0|false)\s*\)'),
     'a do { } while (0) wrapper only steers the compiler: write the statements'),
]
DECLSPEC = re.compile(r'\b(?:__declspec|_declspec)\s*\(\s*([A-Za-z_]\w*)')
DECLSPEC_OK = {'noreturn', 'novtable', 'align'}
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*(.*)$', re.M)
INCLUDE_FORM = re.compile(r'(?:"([^"]+)"|<([^>]+)>)[ \t]*$')
BARE = re.compile(r'(?:^|[;{}])[ \t\n]*((?:\*+|&)?[A-Za-z_]\w*(?:(?:\.|->|::)\w+|\[[^\[\]();]*\])*)[ \t\n]*;')
BARE_OK = {'break', 'continue', 'return', 'default', 'public', 'private', 'protected', 'else', 'do'}
SELF_ASSIGN = re.compile(r'(?:^|[;{})])\s*([A-Za-z_][\w.\->\[\]]*)\s*=\s*([A-Za-z_][\w.\->\[\]]*)\s*;')


def strip(text):
    """The code of a source text: line splices joined, comments removed, and the
    contents of string and character literals blanked (the quotes stay)."""
    text = text.replace('\\\r\n', '').replace('\\\n', '')
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(' ' + '\n' * text.count('\n', i, j))
            i = j
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif text[i] in '"\'':
            q, j = text[i], i + 1
            while j < n and text[j] != q and text[j] != '\n':
                j += 2 if text[j] == '\\' else 1
            out.append(q + q)
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def outside_parentheses(code):
    """code with everything inside parentheses blanked, so a `for (a; b; c)` header
    is not read as three statements."""
    out, depth = [], 0
    for c in code:
        if c == '(':
            depth += 1
            out.append(c if depth == 1 else ' ')
        elif c == ')':
            depth = max(0, depth - 1)
            out.append(c if depth == 0 else ' ')
        else:
            out.append(c if depth == 0 or c == '\n' else ' ')
    return ''.join(out)


def includes_of(text):
    """The #include targets of a source text, with comments removed but strings kept."""
    out, i, n, keep = [], 0, len(text), []
    while i < n:
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        else:
            keep.append(text[i])
            i += 1
    for m in INCLUDE.finditer(''.join(keep).replace('\\\n', '')):
        out.append(m.group(1).strip())
    return out


def scan_text(project, path, text, raw, tier):
    problems = []
    if any(b > 126 or (b < 32 and b not in (9, 10, 13)) for b in raw):
        problems.append('contains bytes that are not printable ASCII')
    unit = units.parse(text, path)
    first_line = text.split('\n', 1)[0]
    if 'cflags:' in text and not units.CFLAGS.match(text):
        problems.append('a /* cflags: */ comment only works as the very first thing in the file')
    elif units.CFLAGS.match(text) and '*/' not in first_line:
        problems.append('the /* cflags: */ comment must fit on the first line')
    for flag in unit.cflags:
        if flag not in project.allowed_cflags:
            problems.append(f'compiler flag {flag} is not in the allow-list of xbx.toml')
    for name in unit.syms:
        if not name.startswith('??'):
            problems.append(f'/* sym: {name} */: only special members (names starting ??) take an address this way; '
                            'call everything else func_XXXXXXXX or data_XXXXXXXX')
    for name in unit.duplicate_syms:
        problems.append(f'/* sym: {name} */ is given two different addresses')
    if tier == 'matched' and unit.fuzzy is not None:
        problems.append('/* fuzzy: */ belongs to the non-matching tier; a unit here is a proven match')
    if tier == 'nonmatching' and unit.fuzzy is None:
        problems.append('a non-matching unit needs its /* fuzzy: NN.N */ line (`xbx match --write-fuzzy` writes it)')

    code = strip(text)
    for rx, why in RULES:
        if rx.search(code):
            problems.append(why)
    for m in DECLSPEC.finditer(code):
        if m.group(1) not in DECLSPEC_OK:
            problems.append(f'__declspec({m.group(1)}) is not allowed (only noreturn, novtable, align)')
    for target in includes_of(text):
        m = INCLUDE_FORM.match(target)
        name = m and (m.group(1) or m.group(2))
        if not name:
            problems.append(f'#include {target}: write a plain "file" or <file>')
        elif name.startswith(('/', '\\')) or '..' in re.split(r'[\\/]', name) or ':' in name:
            problems.append(f'#include {target}: only headers inside the project\'s include directories')
        elif not any(os.path.isfile(os.path.join(base, name.replace('\\', '/'))) for base in project.includes):
            problems.append(f'#include {target}: no such header in the project\'s include directories')
    flat = outside_parentheses(code)
    for m in BARE.finditer(flat):
        if m.group(1) not in BARE_OK:
            problems.append(f'bare expression statement `{m.group(1)};` does nothing but steer the compiler')
    for m in SELF_ASSIGN.finditer(flat):
        if m.group(1) == m.group(2):
            problems.append(f'self-assignment `{m.group(1)} = {m.group(2)};` does nothing but steer the compiler')
    return list(dict.fromkeys(problems))


def run(project, paths=None):
    report = Report('scan', project)
    rows = project.rows
    seen = {}
    count = 0
    for tier, folder in (('matched', project.matched_dir), ('nonmatching', project.nonmatching_dir)):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if paths and os.path.abspath(path) not in paths:
                continue
            if os.path.isdir(path):
                report.problem(path, 'a unit directory holds only unit files')
                continue
            count += 1
            m = UNIT_NAME.match(name)
            if not m:
                report.problem(path, 'file name must be func_XXXXXXXX.cpp (eight upper-case hex digits)')
                continue
            addr = int(m.group(1), 16)
            row = rows.get(addr)
            if row is None:
                report.problem(path, f'{addr:#010x} is not a function in the function table')
            elif row.get('kind', 'func') != 'func':
                report.problem(path, 'this address is a compiler-made stub: it is matched with the function it belongs to')
            if addr in seen:
                report.problem(path, f'the function also has a unit at {project.relative(seen[addr])}: keep one')
            seen[addr] = path
            with open(path, 'rb') as f:
                raw = f.read()
            for why in scan_text(project, path, raw.decode('utf-8', errors='replace'), raw, tier):
                report.problem(path, why)
    return report.finish(f'{count} unit files')
