"""Structural beam search over one function body (`xbx search ADDR`).

Candidates come from mutators (source techniques as enumerations, plus
permute_lib's and local_mutations' mutations sampled at random); each is
scored through scorer.score_text (ledger-cached, origin = 'search:<mutator>',
parent = the parent's text hash), so the ledger learns which mutators pay off.
Beam: the N best states by metrics.key (match, dist, sem, lock); ties keep
whichever came first, so neutral moves are kept as stepping stones.
Every improvement is ablated back toward the starting body (each changed hunk
reverted alone, kept reverted when the result is no worse) and written to
<project.out>/<addr>/search/best_NNNN.cpp (or flag_NNNN.cpp when it adds a
one-use local and is not a MATCH), with a line in search/log.txt.

Compiles run one at a time. The mutators read a body whose block braces stand
on their own lines, so the starting body is put in that form first (and kept
as written if that changes its score, which it should not).

Derived from the h2m workbench of the halo2-decompiled project (GPL-3.0-or-later).
"""
import collections, difflib, itertools, os, random, re, time
from . import classify, ledger, metrics, mutators, scorer

SEARCH_TOOL_TIMEOUT = 30  # seconds one compiler run may take during a search (XBX_TOOL_TIMEOUT overrides)
NUMBERS = ('match', 'lock', 'dist', 'sem', 'frame', 'first')


class State:
    __slots__ = ('body', 'text', 'hash', 'v', 'key', 'flags', 'path', 'order', 'expanded', 'pending', 'exhausted')

    def __init__(self, body, text, v, flags, path, order):
        self.body, self.text, self.v = body, text, v
        self.hash = scorer.digest(text)
        self.key = metrics.key(v)
        self.flags, self.path, self.order = flags, path, order
        self.expanded, self.pending, self.exhausted = 0, None, False


def fmt(v):
    if 'error' in v:
        return 'ERROR'
    if v.get('match'):
        return 'MATCH'
    return f"lock {v['lock']} dist {v['dist']} sem {v['sem']}"


def ledger_rates():
    """origin -> (tries, improvements over the parent) across every function."""
    q = '''select c.origin, count(*), sum(case when c.ok=1 and p.ok=1 and (c.match > p.match or c.dist < p.dist
           or (c.dist = p.dist and c.sem < p.sem) or (c.dist = p.dist and c.sem = p.sem and c.lock < p.lock))
           then 1 else 0 end) from variant c join variant p on p.va = c.va and p.hash = c.parent
           where c.origin like 'search:%' group by c.origin'''
    with ledger._lock:
        rows = ledger.db().execute(q).fetchall()
    return {o[len('search:'):]: (n, s or 0) for o, n, s in rows}


class Search:
    def __init__(self, addr, start=None, seconds=1200, beam=6, seed=0, only=None, log=print):
        self.addr, self.seconds, self.width = addr, seconds, beam
        self.project, compiler, _ = scorer.session()
        if not os.environ.get('XBX_TOOL_TIMEOUT'):   # a compile takes a second or two: a wedged one must not eat minutes
            compiler.timeout = min(compiler.timeout, SEARCH_TOOL_TIMEOUT)
        self.rng = random.Random(seed)
        self.say = log
        path = start or scorer.tree_unit(addr)
        if path is None:
            raise ValueError(f'no unit for {addr:08X} in the tree (give --start FILE)')
        self.text0 = scorer.read(path)
        self.body0 = mutators.body_of(self.text0, addr)       # ValueError when the unit has no func_XXXXXXXX
        folders = [os.path.dirname(os.path.abspath(path))] + scorer.unit_dirs(self.project) + list(self.project.includes)
        self.cx = mutators.Ctx(addr, self.text0, seed, folders)
        self.mutators = [m for m in mutators.ALL if not only or m.__name__ in only]
        self.out = os.path.join(self.project.workdir(addr), 'search')
        os.makedirs(self.out, exist_ok=True)
        self.logf = open(os.path.join(self.out, 'log.txt'), 'a', encoding='utf-8')
        self.pool, self.seen = {}, set()
        self.counter = itertools.count()
        self.tries = collections.Counter()
        self.wins = collections.Counter()
        self.errors = collections.Counter()
        self.compiles = 0
        self.written = 0
        self.deadline = 0
        self.classes, self.hint_keys = [], set()

    # ---------------------------------------------------------------- text
    def full(self, body):
        """The unit text with this body, and the helpers the body calls before its function."""
        text = mutators.splice(self.text0, self.addr, body)
        need = [n for n in mutators.HELPERS
                if f'{n}_{self.addr:08x}(' in body and f'{n}_{self.addr:08x}(' not in self.text0]
        return mutators.with_helpers(text, self.addr, need)

    def log(self, msg):
        self.say(msg)
        self.logf.write(time.strftime('%H:%M:%S ') + msg + '\n')
        self.logf.flush()

    # ---------------------------------------------------------------- weights
    def weights(self, start):
        try:
            verdict, _ = scorer.examine(self.addr, start.text, origin='search:start', keep=False)
            d = classify.diagnose(verdict, self.project.image.base, self.project.rows.get(self.addr))
            classes, keys = d['classes'], d['hint_keys']
        except Exception as e:  # noqa: BLE001
            classes, keys = [], set()
            self.log(f'diagnosis failed: {str(e)[-200:]}')
        rates = ledger_rates()
        n_all = sum(n for n, _ in rates.values())
        p = (sum(k for _, k in rates.values()) + 1) / (n_all + 50)   # overall improvement rate
        w = {}
        for m in self.mutators:
            n = m.__name__
            base = 0.5 if n.startswith('up_') else 1.0
            for c in classes:
                base += mutators.CLASS_WEIGHTS.get(c, {}).get(n, 1) - 1
            for key, table in mutators.HINT_WEIGHTS.items():
                if key in keys:
                    base *= table.get(n, 1)
            tries, succ = rates.get(n, (0, 0))
            factor = min(3.0, max(0.5, (succ + 20 * p) / (tries + 20) / p))   # shrunk toward the mean
            w[n] = max(0.0, base) * factor
        self.classes, self.hint_keys = classes, keys
        return w

    # ---------------------------------------------------------------- scoring
    def score(self, items):
        """items: [(body, parent State, mutator, note)] -> [(item, text, v)], one compile at a time.
        A compile the toolchain gives up on comes back as an error and is not recorded."""
        out = []
        for it in items:
            body, parent, mut, note = it
            text = self.full(body)
            v = scorer.score_text(self.addr, text, origin=f'search:{mut}', note=note[:300],
                                  parent=parent.hash if parent else None, keep=False)
            self.compiles += 1
            out.append((it, text, v))
        return out

    def admit(self, body, text, v, parent, note):
        _, flags = mutators.policy(body, self.body0)
        st = State(body, text, v, flags, (parent.path if parent else []) + ([note] if note else []), next(self.counter))
        self.pool.setdefault(st.hash, st)
        return self.pool[st.hash]

    # ---------------------------------------------------------------- neighbours
    def neighbours(self, st):
        if st.pending is None:
            st.pending = {}
            for m in self.mutators:
                if self.w.get(m.__name__, 0) <= 0:
                    continue
                try:
                    cands = m(st.body, self.cx)
                except Exception:  # noqa: BLE001  (text the model cannot read)
                    cands = []
                keep = []
                for body, note, _ in cands:
                    if body == st.body:
                        continue
                    reason, _ = mutators.policy(body, self.body0)
                    if reason:
                        continue
                    keep.append((body, note))
                self.rng.shuffle(keep)
                if keep:
                    st.pending[m.__name__] = keep
        return st.pending

    def draw(self, st):
        pend = self.neighbours(st)
        while pend:
            names = list(pend)
            # diminishing returns: a mutator that keeps producing neutral moves gives way
            name = self.rng.choices(names, [self.w[n] / (1 + self.tries[n] / 300) ** 0.5 for n in names])[0]
            body, note = pend[name].pop()
            if not pend[name]:
                del pend[name]
            h = scorer.digest(self.full(body))
            if h in self.seen:
                continue
            self.seen.add(h)
            return body, name, note
        st.exhausted = True
        return None

    def beam(self):
        live = sorted(self.pool.values(), key=lambda s: (s.key, len(s.flags), len(s.path), s.order))
        return [s for s in live if not s.exhausted][:self.width]

    # ---------------------------------------------------------------- ablation
    def ablate(self, st):
        """Revert hunks of st against the start, kept reverted when no worse:
        first the groups of hunks that share a name the start lacks (a local and
        its uses), then each hunk alone; twice over."""
        a, best = self.body0.split('\n'), st
        names0 = set(re.findall(r'[A-Za-z_]\w*', self.body0))
        for _ in range(2):
            changed = False
            b = best.body.split('\n')
            ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != 'equal']
            if len(ops) <= 1:
                return best
            groups = collections.defaultdict(set)
            for k, o in enumerate(ops):
                for n in set(re.findall(r'[A-Za-z_]\w*', '\n'.join(b[o[3]:o[4]]))) - names0:
                    groups[n].add(k)
            trials = sorted({frozenset(g) for g in groups.values() if 1 < len(g) < len(ops)}, key=len, reverse=True)
            trials += [frozenset([k]) for k in range(len(ops))][:24]
            for group in trials:
                if time.time() > self.deadline:
                    return best
                spans = {ops[k][1:3] for k in group}
                b = best.body.split('\n')
                cur = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != 'equal']
                sel = [o for o in cur if o[1:3] in spans]
                if len(sel) != len(spans) or len(cur) <= len(sel):
                    continue
                for _, i1, i2, j1, j2 in sorted(sel, key=lambda o: -o[3]):
                    b = b[:j1] + a[i1:i2] + b[j2:]
                body = '\n'.join(b)
                if mutators.policy(body, self.body0)[0]:
                    continue
                self.seen.add(scorer.digest(self.full(body)))
                what = ', '.join(f'{i1}-{i2}' for _, i1, i2, _, _ in sel)
                [(_, text, v)] = self.score([(body, best, 'ablate', f'revert start lines {what}')])
                if 'error' in v or metrics.key(v) > best.key:
                    continue
                best = self.admit(body, text, v, best, f'ablated: start lines {what} restored')
                changed = True
            if not changed:
                break
        return best

    # ---------------------------------------------------------------- output
    def write(self, st, start, kind=None):
        clean = not st.flags or st.v.get('match')
        self.written += 1
        ext = os.path.splitext(self.project.unit_glob)[1] or '.cpp'
        name = f'{kind or ("best" if clean else "flag")}_{self.written:04d}{ext}'
        path = os.path.join(self.out, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(st.text)
        ledger.set_path(self.addr, st.hash, path)
        diff = [l.rstrip('\n') for l in difflib.unified_diff(self.body0.splitlines(True), st.body.splitlines(True), n=0)
                if not l.startswith(('---', '+++'))]
        self.log(f'{name}: {fmt(st.v)} (start {fmt(start.v)}) after {self.compiles} candidates'
                 + (f'  FLAGS {"; ".join(st.flags)}' if st.flags else ''))
        for p in st.path:
            self.log(f'    step: {p}')
        for l in diff[:40]:
            self.log(f'    {l}')
        return path

    # ---------------------------------------------------------------- main loop
    def begin(self):
        """Scores the start (as written, then with its block braces on their own
        lines, which is the body the search works on when both score the same)."""
        [(_, text, v)] = self.score([(self.body0, None, 'start', 'start')])
        if 'error' in v:
            self.log(f'{self.addr:08X}: the start does not compile: {v["error"][-400:]}')
            return None
        norm = mutators.allman(self.body0)
        if norm != self.body0:
            [(_, text2, v2)] = self.score([(norm, None, 'start', 'start, braces on their own lines')])
            if 'error' not in v2 and all(v2[k] == v[k] for k in NUMBERS):
                self.body0, text, v = norm, text2, v2
            else:
                self.log('   the start scores differently with its braces on their own lines; searching it as written')
        return self.admit(self.body0, text, v, None, '')

    def run(self):
        t0 = time.time()
        self.deadline = t0 + self.seconds
        start = self.begin()
        if start is None:
            return None
        if start.v.get('match'):
            self.log(f'{self.addr:08X}: the start is already a MATCH')
            return start
        if start.v['dist'] == 0 and start.v['lock'] == 0:
            self.log(f'{self.addr:08X}: the code already equals retail up to address fields; what is left is not a '
                     'matter of how the body is written:')
            for p in start.v.get('problems') or ():
                self.log(f'    ! {p}')
            return start
        self.w = self.weights(start)
        self.log(f'== search {self.addr:08X} start {fmt(start.v)} classes {" ".join(self.classes)}; '
                 f'{len(self.mutators)} mutators, beam {self.width}, {self.seconds:.0f}s')
        top = sorted(self.w.items(), key=lambda kv: -kv[1])[:8]
        self.log('   weights: ' + ' '.join(f'{n}={x:.2f}' for n, x in top))
        best = flagged = lowest = start
        self.seen.add(start.hash)
        while time.time() < self.deadline and not best.v.get('match'):
            beam = self.beam()
            if not beam:
                self.log('   neighbourhood exhausted')
                break
            items = []
            ranks = [1.0 / (1 + r) / (1 + s.expanded / 40) for r, s in enumerate(beam)]
            for _ in range(3):
                for _ in range(len(beam) + 2):
                    live = [(s, w) for s, w in zip(beam, ranks) if not s.exhausted]
                    if not live:
                        break
                    st = self.rng.choices([s for s, _ in live], [w for _, w in live])[0]
                    got = self.draw(st)
                    if got:
                        st.expanded += 1
                        items.append((got[0], st, got[1], got[2]))
                        break
            if not items:
                continue
            for (body, parent, mut, note), text, v in self.score(items):
                self.tries[mut] += 1
                if 'error' in v:
                    self.errors[mut] += 1
                    continue
                st = self.admit(body, text, v, parent, f'{mut}: {note}')
                if st.key < parent.key:
                    self.wins[mut] += 1
                if not st.flags and st.v['lock'] < lowest.v['lock'] and st.key >= best.key:
                    lowest = st     # the lockstep count, even when dist is not the best
                    self.write(st, start, 'lock')
                if st.key < best.key and st.key < flagged.key or (not st.flags and st.key < best.key):
                    a = self.ablate(st)
                    if not a.flags or a.v.get('match'):
                        if a.key < best.key:
                            best = a
                            if a.v.get('lock', 0) < lowest.v['lock']:
                                lowest = a
                            self.write(a, start)
                    elif a.key < flagged.key and a.key < best.key:
                        flagged = a
                        self.write(a, start)
        self.log(f'== done {self.addr:08X}: start {fmt(start.v)} -> best {fmt(best.v)} in {time.time() - t0:.0f}s, '
                 f'{self.compiles} candidates scored (ledger hits included), {len(self.pool)} distinct states that compiled')
        self.log('   per mutator (tried/compile errors/improved the parent): ' + ' '.join(
            f'{n}={self.tries[n]}/{self.errors[n]}/{self.wins[n]}' for n in sorted(self.tries, key=lambda n: -self.tries[n])))
        return best
