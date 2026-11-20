"""Index of every MATCHED function's retail code (no compiles): the project's
own source<->code pairs, queried by instruction sequence. The matched
functions are the units of the matched tier (project.units('matched')).

find(seq)      matched functions containing the masked sequence contiguously
nearest(addr)  matched functions sharing the most rare 3-grams with addr
Sequences are instruction texts at level 'stack' (registers, slots and labels
masked; offsets and constants kept), or 'loose' with every number masked.

The index is cached in <project.out>/corpus_<level>.pkl and rebuilt when the
set of matched units or the function table changes.
"""
import collections, math, os, pickle

from .. import project as project_module
from . import asm

N = 3
_cache = {}


def matched():
    """{address: unit path} of the matched tier (functions the table knows)."""
    project = project_module.current()
    return {a: p for a, p in project.units('matched') if a in project.rows}


def _stamp(project, addrs):
    s = os.stat(project.functions_csv)
    return (2, tuple(addrs), s.st_size, s.st_mtime_ns, project.image.base, project.xbe_sha256)


def load(level='stack'):
    """(seqs, grams): {addr: [masked instruction]} and {3-gram: [(addr, position)]}."""
    if level in _cache:
        return _cache[level]
    project = project_module.current()
    addrs = sorted(matched())
    stamp = _stamp(project, addrs)
    path = os.path.join(project.out, f'corpus_{level}.pkl')
    if os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                saved, seqs, grams = pickle.load(f)
            if saved == stamp:
                _cache[level] = (seqs, grams)
                return _cache[level]
        except Exception:  # noqa: BLE001  (an unreadable or older cache is rebuilt)
            pass
    floor = project.image.base
    seqs, grams = {}, collections.defaultdict(list)
    for addr in addrs:
        s = [asm.mask(i.text, level) for i in asm.decode(project.retail(addr), addr, floor=floor)]
        seqs[addr] = s
        for k in range(len(s) - N + 1):
            grams[tuple(s[k:k + N])].append((addr, k))
    os.makedirs(project.out, exist_ok=True)
    scratch = f'{path}.{os.getpid()}.tmp'              # several processes may build the index at once
    with open(scratch, 'wb') as f:
        pickle.dump((stamp, seqs, dict(grams)), f)
    os.replace(scratch, path)
    _cache[level] = (seqs, dict(grams))
    return _cache[level]


def find(seq, level='stack', limit=30, skip=None):
    """[(addr, position)] of matched functions containing seq (already masked); skip: an address to leave out."""
    seqs, grams = load(level)
    if len(seq) < N:
        return []
    keys = [tuple(seq[k:k + N]) for k in range(len(seq) - N + 1)]
    k0 = min(range(len(keys)), key=lambda k: len(grams.get(keys[k], ())))
    out = []
    for addr, pos in grams.get(keys[k0], ()):
        start = pos - k0
        if addr != skip and start >= 0 and seqs[addr][start:start + len(seq)] == seq:
            out.append((addr, start))
    out.sort(key=lambda h: len(seqs[h[0]]))
    return out[:limit]


def rarity(seq, level='stack'):
    """How many matched functions contain each 3-gram of seq (0 = retail idiom seen nowhere)."""
    _, grams = load(level)
    return [len({v for v, _ in grams.get(tuple(seq[k:k + N]), ())}) for k in range(len(seq) - N + 1)]


def nearest(addr, level='stack', limit=8, owner=None):
    project = project_module.current()
    seqs, grams = load(level)
    total = len(seqs)
    s = [asm.mask(i.text, level) for i in asm.decode(project.retail(addr), addr, floor=project.image.base)]
    score = collections.Counter()
    for g in {tuple(s[k:k + N]) for k in range(len(s) - N + 1)}:
        hits = {v for v, _ in grams.get(g, ())}
        if not hits or (total >= 40 and len(hits) > total // 20):
            continue                                   # a 3-gram in more than a twentieth of the corpus says nothing
        w = math.log(total / len(hits)) if total > len(hits) else 0.1
        for v in hits:
            score[v] += w
    rows = project.rows
    out = [(sc / math.sqrt(len(seqs[v]) + 20), v) for v, sc in score.items()
           if v != addr and (not owner or rows[v]['owner'].startswith(owner))]
    return sorted(out, reverse=True)[:limit]


def exemplars(d, level='stack', context=1, limit=5, skip=None):
    """For each divergent region of diagnosis d: matched functions whose retail
    code contains retail's instructions of that region (with a little context)."""
    x, out = d['x'], []
    for op, i1, i2, j1, j2 in d['ops']:
        if op == 'equal' or i2 == i1:
            continue
        lo, hi = max(0, i1 - context), min(len(x), i2 + context)
        while hi - lo < N and (lo > 0 or hi < len(x)):
            lo, hi = max(0, lo - 1), min(len(x), hi + 1)
        seq = [asm.mask(i.text, level) for i in x[lo:hi]]
        hits = find(seq, level, limit, skip)
        if not hits and hi - lo > i2 - i1 >= N:
            seq = [asm.mask(i.text, level) for i in x[i1:i2]]
            hits = find(seq, level, limit, skip)
        out.append(dict(r0=x[i1].off, seq=seq, hits=hits))
    return out
