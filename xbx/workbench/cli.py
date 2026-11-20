"""The matching workbench (docs/WORKBENCH.md has one example per command; ADDR is hex, with or without 0x).

    xbx score ADDR [FILE...]         metric vector of the tree's unit, or of each variant file
    xbx diff ADDR [FILE]             diagnosis + aligned side-by-side (retail left)
    xbx triage [ADDR|FILE...]        the diff header of every unit of the non-matching tier, best first
    xbx try ADDR VARIANTS.txt        variants separated by a line of =====, one line each
    xbx history ADDR                 best variants recorded in the ledger
    xbx exemplars ADDR [FILE]        matched functions containing retail's idiom at each divergent region
    xbx idiom ADDR [FILE]            each retail-only instruction looked up in matched code of the same owner
    xbx search ADDR                  structural beam search over the source mutators (workbench/search.py)

A variant is the full text of a unit file. Nothing in the tree is ever edited:
variants are compiled from <XBX_OUT>/<addr>/ and recorded in <XBX_OUT>/xbx.db.
"""
import argparse, os, re, sys

from .. import project as project_module
from ..toolchain import ToolError
from . import classify, corpus, ledger, metrics, scorer

SEPARATOR = re.compile(r'^={5,}[ \t]*\n?', re.M)


def address(text):
    return int(text, 16)


def fmt(v):
    if 'error' in v:
        lines = v['error'].strip().splitlines() or ['']
        line = next((l for l in lines if ' error ' in l), lines[-1]).strip()
        return 'ERROR ' + re.sub(r'^.*[\\/](?=[^\\/]*\(\d+\))', '', line)[:120]   # file(line), without its directory
    if v['match']:
        return 'MATCH'
    first = f"+{v['first']:#x}" if v['first'] >= 0 else '-'
    return f"lock {v['lock']:4d}  dist {v['dist']:4d}  sem {v['sem']:3d}  frame {v['frame']:+d}  first {first}"


def why(v, indent='    '):
    """The lines under a vector that say why it is not a match."""
    if v.get('match'):
        return []
    if 'error' in v and v.get('problems') == ['does not compile']:
        return [indent + '  ' + l for l in v['error'].strip().splitlines()[:12]]
    return [f'{indent}! {p}' for p in v.get('problems') or ()]


def show(path):
    """A path as the user would type it: relative to where they are when it is below it."""
    rel = os.path.relpath(path)
    return path if rel.startswith('..') else rel


def known(addr):
    project = scorer.session()[0]
    if addr not in project.rows:
        sys.exit(f'error: {addr:08X} is not a function start in {project.relative(project.functions_csv)}')
    return addr


def unit_file(addr):
    project = scorer.session()[0]
    path = scorer.tree_unit(known(addr))
    if path is None:
        sys.exit(f'error: no unit for {addr:08X} in the tree (it would be '
                 f'{project.relative(project.unit_path(addr, "nonmatching"))} while it is open); give a file')
    return path


def source(a):
    """The file a command works on: the one given, else the tree's unit."""
    return a.file if known(a.addr) and a.file else unit_file(a.addr)


def cmd_score(a):
    for f in a.files or [unit_file(a.addr)]:
        v = scorer.score_text(known(a.addr), scorer.read(f), origin='file', note=os.path.basename(f))
        print('\n'.join([f'{fmt(v)}  {show(f)}'] + ([] if a.quiet else why(v))))


def report(addr, path, full=True):
    """(vector, diagnosis or None, lines) for the variant in the file at path."""
    project = scorer.session()[0]
    row = project.rows[addr]
    verdict, v = scorer.examine(addr, scorer.read(path), origin='file', note=os.path.basename(path))
    lines = [f'{addr:08X} {show(path)} size {row["_size"]}  {fmt(v)}']
    if 'error' in v:
        return v, None, lines + (why(v, '  ') if full else [])
    d = classify.diagnose(verdict, project.image.base, row)
    if v['match']:
        return v, d, lines
    lines.append(f'  classes: {" ".join(d["classes"])}   frame retail {d["frame"][0]:#x} ours {d["frame"][1]:#x}'
                 + (f'   REGISTER INPUTS {d["convention"]}' if d['convention'] else ''))
    lines += why(v, '  ')
    if d['missing']:
        lines.append('  retail only: ' + '; '.join(f'{k} x{n}' for k, n in d['missing'][:8]))
    if d['extra']:
        lines.append('  ours only:   ' + '; '.join(f'{k} x{n}' for k, n in d['extra'][:8]))
    if d['reg_map']:
        lines.append('  registers (retail->ours): ' + '  '.join(
            f'{r}->' + '/'.join(f'{o}x{n}' for o, n in c) for r, c in sorted(d['reg_map'].items())))
    for h in d['hints']:
        lines.append('  HINT: ' + h)
    if full:
        lines += classify.side_by_side(d)
    return v, d, lines


def cmd_diff(a):
    _, _, lines = report(a.addr, source(a))
    print('\n'.join(lines))


def cmd_triage(a):
    project = scorer.session()[0]
    targets = []
    for item in a.items:
        if os.path.isfile(item):
            addr = project_module.unit_address(item)
            if addr is None:
                sys.exit(f'error: {item}: not a unit file name (func_XXXXXXXX.cpp)')
            targets.append((addr, item))
        else:
            try:
                addr = address(item)
            except ValueError:
                sys.exit(f'error: {item}: neither a file nor a hex address')
            targets.append((addr, unit_file(addr)))
    if not a.items:
        targets = [(addr, path) for addr, path in project.units('nonmatching')
                   if addr in project.rows and (not a.owner or project.rows[addr]['owner'].startswith(a.owner))]
        if not targets:
            print(f'no units in {project.relative(project.nonmatching_dir)}'
                  + (f' owned by {a.owner}' if a.owner else ''))
    if a.limit:
        targets = targets[:a.limit]
    res = []
    for addr, path in targets:
        if addr not in project.rows:
            res.append((addr, (dict(error='not in the function table'), None,
                               [f'{addr:08X} {show(path)} ERROR not a function start in the function table'])))
            continue
        try:
            res.append((addr, report(addr, path, full=False)))
        except (ToolError, OSError, ValueError) as e:
            res.append((addr, (dict(error=str(e)[-300:]), None,
                               [f'{addr:08X} {show(path)} ERROR {(str(e).strip().splitlines() or [""])[-1][:150]}'])))
    res.sort(key=lambda r: metrics.key(r[1][0]) if 'error' not in r[1][0] else (9,))
    for addr, (v, d, lines) in res:
        print('\n'.join(lines))
        if d and not v.get('match'):
            ledger.set_fact(addr, 'classes', d['classes'])


def cmd_try(a):
    from . import mutators
    base_path = a.start or scorer.tree_unit(known(a.addr))
    base = scorer.read(base_path) if base_path else None
    if base is not None:
        print(f'base: {fmt(scorer.score_text(a.addr, base, origin="file", note=os.path.basename(base_path)))}'
              f'  {show(base_path)}')
    name = os.path.basename(a.files[0])
    for k, var in enumerate(SEPARATOR.split(scorer.read(a.files[0]))):
        if not var.strip():
            continue
        if a.start:                                    # a replacement for the function's body
            body = var.strip('\n')
            if body.lstrip().startswith('{') and body.rstrip().endswith('}'):
                body = body.strip()[1:-1].strip('\n')
            try:
                text = mutators.splice(base, a.addr, '\n' + body + '\n')
            except ValueError as e:
                sys.exit(f'error: {a.start}: {e}')
        else:                                          # a complete unit
            text = var.strip('\n') + '\n'
        v = scorer.score_text(a.addr, text, origin='try', note=f'{name}#{k}')
        print('\n'.join([f'{k:3d}: {fmt(v)}'] + (why(v, '       ') if a.verbose else [])))


def cmd_history(a):
    rows = ledger.history(a.addr, a.top)
    if not rows:
        print(f'no variant of {a.addr:08X} is recorded yet')
    for h, origin, note, match, lock, dist, sem, frame, path, t in rows:
        # a row is keyed by its text and the settings it was scored under (scorer.salt)
        old = bool(path) and os.path.exists(path) and scorer.digest(scorer.read(path)) != h
        print(f'{"MATCH" if match else f"dist {dist:4d} lock {lock:4d} sem {sem:3d}"}  {origin:10} {note or ""}  '
              f'{show(path) if path else h}' + ('  (scored before the flags, headers or tables changed)' if old else ''))


def cmd_exemplars(a):
    project = scorer.session()[0]
    v, d, lines = report(a.addr, source(a), full=False)
    if d is None:
        sys.exit('\n'.join(lines))
    rows, units = project.rows, corpus.matched()
    for level in ('stack', 'loose'):
        print(f'== retail idioms of the divergent regions found in matched functions (level {level})')
        for e in corpus.exemplars(d, level, skip=a.addr):
            if not e['hits']:
                continue
            print(f'+{e["r0"]:#x}: ' + ' ; '.join(e['seq']))
            for addr, pos in e['hits']:
                print(f'      {addr:08X} {show(units[addr])} (instruction {pos})')
    print('== nearest matched functions')
    for sc, addr in corpus.nearest(a.addr, owner=None):
        print(f'  {sc:6.1f} {addr:08X} {show(units[addr])} size {rows[addr]["_size"]}')


def cmd_idiom(a):
    """Each instruction only retail has, looked up in matched code of the same owner."""
    from . import asm
    project = scorer.session()[0]
    v, d, lines = report(a.addr, source(a), full=False)
    if d is None:
        sys.exit('\n'.join(lines))
    rows, units = project.rows, corpus.matched()
    owner = rows[a.addr]['owner']
    seqs, _ = corpus.load('loose')
    x = d['x']
    ours = {asm.mask(i.text, 'loose') for i in d['y']}
    seen = set()
    for k, i in enumerate(x):
        t = asm.mask(i.text, 'loose')
        if t in ours or t in seen:
            continue
        seen.add(t)
        hits = []
        for addr, s in seqs.items():
            if addr == a.addr or not rows[addr]['owner'].startswith(owner):
                continue
            for n, u in enumerate(s):
                if u == t:
                    ctx = s[max(0, n - 2):n + 3]
                    near = [asm.mask(j.text, 'loose') for j in x[max(0, k - 2):k + 3]]
                    hits.append((-len(set(ctx) & set(near)), len(s), addr, ' ; '.join(ctx)))
                    break
        hits.sort()
        print(f'+{i.off:#x} {i.text}   [{t}]  in {len(hits)} matched functions')
        for sc, n, addr, ctx in hits[:a.top]:
            print(f'    {addr:08X} {show(units[addr])} ({n} insns, {-sc} shared): {ctx}')


def cmd_search(a):
    from . import search
    only = set(a.only.split(',')) if a.only else None
    try:
        s = search.Search(known(a.addr), a.start, a.time_limit, a.beam, a.seed, only)
    except ValueError as e:
        sys.exit(f'error: {e}')
    best = s.run()
    if best is not None:
        print(f'best: {search.fmt(best.v)}  (files and log in {show(s.out)})')


def parser():
    p = argparse.ArgumentParser(prog='xbx', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    for name in ('score', 'diff', 'try', 'history', 'exemplars', 'idiom'):
        q = sub.add_parser(name)
        q.add_argument('addr', type=address, metavar='ADDR')
        if name == 'score':
            q.add_argument('files', nargs='*', metavar='FILE', help='variant files (default: the unit in the tree)')
            q.add_argument('-q', '--quiet', action='store_true', help='one line per file')
        elif name == 'try':
            q.add_argument('files', nargs=1, metavar='VARIANTS.txt')
            q.add_argument('--start', metavar='BASE.cpp',
                           help='each variant is a function body, put into this unit (default: each is a complete unit)')
            q.add_argument('-v', '--verbose', action='store_true', help='the problems of each variant')
        elif name == 'history':
            q.add_argument('--top', type=int, default=40, help='how many variants')
        else:
            q.add_argument('file', nargs='?', metavar='FILE', help='a variant file (default: the unit in the tree)')
            if name == 'idiom':
                q.add_argument('--top', type=int, default=5, help='matched functions shown per instruction')
    q = sub.add_parser('triage')
    q.add_argument('items', nargs='*', metavar='ADDR|FILE', help='addresses or unit files (default: the non-matching tier)')
    q.add_argument('--owner', help='owner prefix of the units to triage (e.g. game)')
    q.add_argument('--limit', type=int, default=0, help='only the first N units')
    q = sub.add_parser('search')
    q.add_argument('addr', type=address, metavar='ADDR')
    q.add_argument('--start', metavar='FILE', help='the unit to start from (default: the unit in the tree)')
    q.add_argument('--time-limit', type=float, default=1200, metavar='S', help='seconds (default 1200)')
    q.add_argument('--beam', type=int, default=6, metavar='N')
    q.add_argument('--seed', type=int, default=0)
    q.add_argument('--only', help='comma-separated mutator names (workbench/mutators.py NAMES)')
    return p


def main(argv=None):
    a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        globals()['cmd_' + a.cmd](a)
    except (project_module.ProjectError, ToolError) as e:
        sys.exit(f'error: {e}')
    except BrokenPipeError:
        pass
    except OSError as e:                               # a variant file that is not there
        sys.exit(f'error: {e.filename}: {e.strerror}' if e.filename else f'error: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
