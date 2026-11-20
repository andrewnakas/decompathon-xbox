"""The one compile -> compare -> measure path.

score_text(addr, text) -> the metric vector of a variant (the full text of a
unit file) plus the Verdict's problems, or {'error': ..., 'problems': [...]}
when it gives no code to measure. The variant is written under
project.workdir(addr), compiled and compared by match.check (the unit parsed
from the text, the variant file as its source), and recorded in the ledger by
the hash of its text, so the same text is never compiled twice.

MATCH is match.check's verdict under the gate's rules (STRICT): every byte
equal, every relocation resolved to the address retail holds, nothing
unverified in the object.

Two caches sit under this: the ledger (variant text -> vector, keyed with
salt()) and match.compile_unit's object cache (text + flags + include
directories + compiler -> object), which is what makes `diff` of a text that
was already scored cheap.
"""
import functools, hashlib, os, re

from .. import match, project as project_module, toolchain, units
from . import ledger, metrics

STRICT = True                      # the rules `xbx match --strict` and the gate apply
HEADERS = ('.h', '.hpp', '.hxx', '.inc', '.inl')
_COMPILER_ERROR = re.compile(r'\b(?:fatal )?error [A-Z]{1,4}\d+')
_recent = {}                       # (addr, hash) -> Verdict of this process (the last few compiled)


@functools.lru_cache(None)
def session():
    """(project, compiler, matcher) of this process."""
    project = project_module.current()
    return project, toolchain.for_project(project), match.Matcher(project, STRICT)


def unit_dirs(project):
    """Where the tree's units live."""
    return [d for d in (project.matched_dir, project.nonmatching_dir) if os.path.isdir(d)]


@functools.lru_cache(None)
def header_dirs():
    """The unit directories that hold headers. A variant is compiled from the
    work directory, so these are added to its include path for the headers a
    unit includes by name from next to it."""
    return tuple(d for d in unit_dirs(session()[0])
                 if any(name.lower().endswith(HEADERS) for name in os.listdir(d)))


@functools.lru_cache(None)
def salt():
    """Everything outside a variant's text that decides its score: the rules,
    the compiler and its flags, the executable, the function and symbol
    tables, and the headers (names, sizes and modification times of the files
    in the include directories and of the headers next to the units). Part of
    every ledger key, so a change to any of them starts a new cache."""
    project = session()[0]
    h = hashlib.sha1(repr((STRICT, project.profile, project.banner, project.cflags, project.xbe_sha256)).encode())

    def stamp(p):
        try:
            s = os.stat(p)
            h.update(f'{p}\0{s.st_size}\0{s.st_mtime_ns}\n'.encode())
        except OSError:
            h.update(f'{p}\0missing\n'.encode())

    for p in (project.functions_csv, project.symbols_csv):
        if p:
            stamp(p)
    for d in project.includes:
        for folder, dirs, files in os.walk(d):
            dirs.sort()
            for name in sorted(files):
                stamp(os.path.join(folder, name))
    for d in unit_dirs(project):
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(HEADERS):
                stamp(os.path.join(d, name))
    return 'xbx:' + h.hexdigest()[:12] + '\n'


def digest(text):
    return ledger.digest(text, salt())


def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def tree_unit(addr):
    """The unit file of a function in the tree (matched tier first), or None."""
    project = session()[0]
    for tier in ('matched', 'nonmatching'):
        p = project.unit_path(addr, tier)
        if os.path.exists(p):
            return p
    return None


def variant_path(addr, h):
    project = session()[0]
    ext = os.path.splitext(project.unit_glob)[1] or '.cpp'   # the extension decides C or C++
    return os.path.join(project.workdir(addr), f'v_{h}{ext}')


def check_text(addr, text):
    """Compiles and compares a variant. Returns (Verdict, the file it was
    compiled from): this process's own copy, since another process may be
    scoring the same text; _settle() keeps it under the variant's name or
    removes it."""
    project, compiler, matcher = session()
    h = digest(text)
    src = variant_path(addr, f'{h}.{os.getpid()}')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(text)
    unit = units.parse(text, src, addr)
    unit.cflags = unit.cflags + ['/I' + d for d in header_dirs()]
    # match's object cache knows the include directories, not headers next to the units:
    # with such headers every variant is compiled (the ledger still answers a repeated text)
    verdict = match.check(project, compiler, unit, STRICT, source_path=src, matcher=matcher,
                          cache=not header_dirs())
    if len(_recent) >= 4:
        _recent.pop(next(iter(_recent)))
    _recent[addr, h] = verdict
    return verdict, src


def compiled(verdict):
    """Whether the compiler ran and gave its answer (code, or its own errors):
    a wedged or missing tool is not a fact about the variant and is not recorded."""
    return 'does not compile' not in verdict.problems or bool(_COMPILER_ERROR.search(verdict.output or ''))


def result(verdict):
    """The vector of a Verdict with its problems, or {'error', 'problems'}."""
    project = session()[0]
    problems = list(verdict.problems)
    if not verdict.ours:
        if 'does not compile' in problems:
            return dict(error=(verdict.output or '').strip()[-1500:] or 'does not compile', problems=problems)
        return dict(error='; '.join(problems) or 'the object has no code for this unit', problems=problems)
    row = project.rows.get(verdict.addr)
    v = metrics.vector(verdict, project.image.base, row['_size'] if row else None)
    v['problems'] = problems
    return v


def _settle(addr, h, v, src, keep):
    """The variant's file (<work directory>/v_<hash>.cpp) when it is kept, else None."""
    if keep and 'error' not in v:
        path = variant_path(addr, h)
        os.replace(src, path)
        return path
    if os.path.exists(src):
        os.remove(src)
    return None


def _record(addr, h, v, verdict, src, parent, origin, note, keep):
    path = _settle(addr, h, v, src, keep)
    if compiled(verdict):
        ledger.put(addr, h, v, parent, origin, note, path)


def score_text(addr, text, origin='manual', note='', parent=None, keep=True):
    """The vector of a variant (see the module text). keep: leave the variant
    file in the work directory and record its path (search turns it off)."""
    h = digest(text)
    v = ledger.get(addr, h)
    if v is not None:
        if keep and 'error' not in v:
            src = variant_path(addr, h)
            if not os.path.exists(src):
                with open(src, 'w', encoding='utf-8') as f:
                    f.write(text)
            ledger.set_path(addr, h, src)
        return v
    verdict, src = check_text(addr, text)
    v = result(verdict)
    _record(addr, h, v, verdict, src, parent, origin, note, keep)
    return v


def examine(addr, text, origin='manual', note='', keep=True):
    """(Verdict, vector) of a variant. The Verdict holds the bytes a diagnosis
    needs and the ledger does not, so this compiles unless this process
    already compiled the same text."""
    h = digest(text)
    verdict = _recent.get((addr, h))
    if verdict is not None:
        return verdict, result(verdict)
    known = ledger.get(addr, h)
    verdict, src = check_text(addr, text)
    v = result(verdict)
    if known is None:
        _record(addr, h, v, verdict, src, None, origin, note, keep)
    else:
        path = _settle(addr, h, v, src, keep)
        if path:
            ledger.set_path(addr, h, path)
    return verdict, v
