"""Turn a retail/ours pair into a diagnosis: which kind of gap, and where.

Classes (a function can carry several):
  convention   retail takes a value in a register at entry and ours takes none there
  semantic     operations one side has and the other lacks (masked multiset)
  width        the semantic gap is movsx/movzx/operand-size only
  fold         retail keeps an fdiv/fmul/imul/shift pair ours folded (or reverse)
  stack        prologue frame differs
  regs         same operations, different registers (bijection reported)
  schedule     same instructions with registers, different order
Regions are the aligned non-equal blocks, each with its own class.

Register inputs. Which registers retail takes values in comes from the
function's row in the function table: its conv column lists the registers of
eax ebx esi edi that retail reads at entry before writing them, which no
standard convention passes arguments in. ecx and edx are the registers of the
standard conventions (ecx is `this` of a member function; ecx and edx are the
register arguments of __fastcall), so for those retail's entry path is read
directly. Ours takes what its decorated name says (a member function takes
ecx) plus whatever its entry path reads.
"""
import collections, re
from . import asm, metrics

REGS = asm.GPR
_TOK = re.compile(r'\b(eax|ebx|ecx|edx|esi|edi|ebp|ax|bx|cx|dx|si|di|bp|[abcd][lh])\b')
_FAM = {'al': 'eax', 'ah': 'eax', 'ax': 'eax', 'bl': 'ebx', 'bh': 'ebx', 'bx': 'ebx', 'cl': 'ecx', 'ch': 'ecx',
        'cx': 'ecx', 'dl': 'edx', 'dh': 'edx', 'dx': 'edx', 'si': 'esi', 'di': 'edi', 'bp': 'ebp'}
_WIDTH = re.compile(r'^(movsx|movzx|cwde|cdq)\b|\b(word|byte) ptr\b|\bR[wb]\b')
_FOLD = re.compile(r'^(fdivr?p?|fmulp?|fdiv|imul|idiv|div|shl|sar|shr|lea)\b')

# registers each standard convention passes arguments in
STANDARD_REGISTERS = {'cdecl': (), 'stdcall': (), 'thiscall': ('ecx',), 'fastcall': ('ecx', 'edx')}
_CC = {'A': 'cdecl', 'C': 'pascal', 'E': 'thiscall', 'G': 'stdcall', 'I': 'fastcall'}
# ?name@scope@@ (up to the first @@), then Y for a global function or, for a member, its access
# (followed by the const/volatile of `this` unless it is static), then the convention
_CXX_FUNCTION = re.compile(r'^\?(?:[^@]|@(?!@))*@@(?:Y|[ABEFIJMNQRUV][A-D]|[CDKLST])([ACEGI])')


def calling_convention(decorated):
    """The calling convention a decorated name declares ('thiscall' for a
    non-static member function), or None when the name does not say."""
    if not decorated:
        return None
    if decorated.startswith('?'):
        m = _CXX_FUNCTION.match(decorated)
        return _CC[m.group(1)] if m else None
    if decorated.startswith('@') and re.search(r'@\d+$', decorated):
        return 'fastcall'
    if decorated.startswith('_'):
        return 'stdcall' if re.search(r'@\d+$', decorated) else 'cdecl'
    return None


def register_inputs(row, theirs, addr, ours, decorated=''):
    """(registers retail takes values in at entry, registers ours does, our
    convention). row: the function's row of the function table, or None."""
    lr = asm.live_in(theirs, addr, saves=asm.GPR)
    lo = asm.live_in(ours, 0, saves=asm.GPR)
    if row is not None:
        retail = set((row.get('conv') or '').split()) | (lr & {'ecx', 'edx'})
    else:
        retail = lr
    convention = calling_convention(decorated)
    return retail, lo | set(STANDARD_REGISTERS.get(convention, ())), convention


def region_class(xs, ys):
    a = collections.Counter(asm.mask(i.text, 'stack') for i in xs)
    b = collections.Counter(asm.mask(i.text, 'stack') for i in ys)
    if a == b:
        if collections.Counter(i.text for i in xs) == collections.Counter(i.text for i in ys):
            return 'schedule'
        if collections.Counter(asm.mask(i.text, 'regs') for i in xs) != \
                collections.Counter(asm.mask(i.text, 'regs') for i in ys):
            return 'stack'
        return 'regs'
    only = list((a - b).elements()) + list((b - a).elements())
    if all(_WIDTH.search(t) for t in only):
        return 'width'
    return 'semantic'


def reg_map(x, y, ops):
    """retail register -> Counter(ours register) over aligned instruction pairs
    whose masked spelling agrees (equal blocks included)."""
    m = collections.defaultdict(collections.Counter)
    for op, i1, i2, j1, j2 in ops:
        if i2 - i1 != j2 - j1:
            continue
        for k in range(i2 - i1):
            a, b = x[i1 + k].text, y[j1 + k].text
            if a == b or asm.mask(a, 'regs') != asm.mask(b, 'regs'):
                continue
            for ra, rb in zip(_TOK.findall(a), _TOK.findall(b)):
                m[_FAM.get(ra, ra)][_FAM.get(rb, rb)] += 1
    return {r: c.most_common(3) for r, c in m.items() if any(o != r for o, _ in c.items())}


_ADD = re.compile(r'^add (e[abcd]x|e[sd]i|ebp), (0x[0-9a-f]+|\d+)$')
_RMW = re.compile(r'^(inc|dec|add|sub|or|and) (dword|word|byte) ptr \[(?!esp)')


def _byref(ins):
    """Offsets N where `add R, N` is followed by a plain `[R]` read: the address
    of a member is formed first, as an inline helper taking it by reference does."""
    out = collections.Counter()
    for k, i in enumerate(ins):
        m = _ADD.match(i.text)
        if m and any(f'[{m.group(1)}]' in j.text for j in ins[k + 1:k + 4]):
            out[int(m.group(2), 0)] += 1
    return out


def hints(x, y, missing, extra, inputs=None):
    """[(key, text)]: what the two listings show, and the source shape each has
    been seen to come from. The key names the observation (mutators.HINT_WEIGHTS)."""
    out = []
    a, b = _byref(x), _byref(y)
    for off, n in (a - b).items():
        out.append(('byref', f'retail forms the address of the member at +{off:#x} and then reads through it (x{n}), '
                             'ours reads the member directly: seen where the member is passed by reference to an '
                             'inline helper'))
    for off, n in (b - a).items():
        out.append(('direct', f'ours forms the address of the member at +{off:#x} (x{n}) where retail reads it '
                              'directly: seen where a reference or pointer local stands between the object and the read'))
    ra = sum(1 for i in x if _RMW.match(i.text))
    rb = sum(1 for i in y if _RMW.match(i.text))
    if ra > rb:
        out.append(('inplace', f'retail updates memory in place {ra - rb} more time(s) where ours loads, changes and '
                               'stores: seen where the update goes through a reference to the member '
                               '(`T &r = object.member; --r;`)'))
    if rb > ra:
        out.append(('copy', f'ours updates memory in place {rb - ra} more time(s) where retail loads, changes and '
                            'stores: seen where the value is first copied to a local'))
    names = [k for k, _ in missing]
    if any(k.startswith('fdivr') for k in names) and any(k.startswith('fmul') for k in names):
        out.append(('reciprocal', 'retail takes a reciprocal and then multiplies where ours divides once: seen where '
                                  'the reciprocal is a named value (`float const inverse = 1.0f / x;`) or the result '
                                  'of an inline helper'))
    if any(k.startswith(('movsx', 'movzx')) for k, _ in extra) and not any(k.startswith(('movsx', 'movzx')) for k in names):
        out.append(('widen', 'ours widens a value (movsx/movzx) that retail keeps at full width: seen where the local '
                             'is declared `long` or `int` rather than a narrow type'))
    if inputs:
        retail, mine, convention = inputs
        lacking = retail - mine
        custom = sorted(lacking - {'ecx', 'edx'})
        if custom:
            out.append(('custom', f'retail reads {", ".join(custom)} at entry before writing (the function table\'s '
                                  'conv column): arguments in registers no standard convention uses, which the '
                                  'compiler does for a function whose every caller it sees (link-time code '
                                  'generation, or a static function called only from its own file)'))
        if 'ecx' in lacking:
            out.append(('this', 'retail reads ecx at entry and ours takes nothing there: ecx is `this` of a member '
                                'function (or the first argument of a __fastcall function)'))
        if 'edx' in lacking:
            out.append(('fastcall', 'retail reads edx at entry and ours takes nothing there: edx is the second '
                                    'register argument of a __fastcall function'))
    return out


def diagnose(verdict, floor, row=None):
    """The diagnosis of a Verdict (or anything with addr, ours, mask, retail and,
    optionally, defines). floor: the image's base address. row: the function's
    row of the function table (its size and conv columns are used)."""
    addr, data, masked = verdict.addr, verdict.ours, verdict.mask
    theirs = metrics.retail_bytes(verdict, row['_size'] if row else None)
    x, y = asm.decode(theirs, addr, floor=floor), asm.ours(data, masked, floor)
    ops = metrics.aligned(x, y)
    regions = []
    for op, i1, i2, j1, j2 in ops:
        if op == 'equal':
            continue
        xs, ys = x[i1:i2], y[j1:j2]
        regions.append(dict(cls=region_class(xs, ys), r0=xs[0].off if xs else (x[i1].off if i1 < len(x) else len(theirs)),
                            o0=ys[0].off if ys else (y[j1].off if j1 < len(y) else -1),
                            retail=[i.text for i in xs], ours=[i.text for i in ys]))
    a, b = asm.bag(x), asm.bag(y)
    missing, extra = a - b, b - a
    classes = []
    inputs = register_inputs(row, theirs, addr, asm.strip_pad(asm.apply_mask(data, masked)),
                             getattr(verdict, 'defines', ''))
    conv = sorted(inputs[0] - inputs[1])
    if conv:
        classes.append('convention')
    fr, fo = asm.frame(x), asm.frame(y)
    if fr != fo:
        classes.append('stack')
    sem = sum(missing.values()) + sum(extra.values())
    if sem:
        only = list(missing.elements()) + list(extra.elements())
        if all(_WIDTH.search(t) for t in only):
            classes.append('width')
        else:
            classes.append('semantic')
            if any(_WIDTH.search(t) for t in only):
                classes.append('width')
        if any(_FOLD.search(t) for t in only):
            classes.append('fold')
    kinds = collections.Counter(r['cls'] for r in regions)
    if kinds.get('regs'):
        classes.append('regs')
    if kinds.get('schedule') or (not sem and regions and not kinds.get('regs')):
        classes.append('schedule')
    found = hints(x, y, missing.most_common(40), extra.most_common(40), inputs)
    return dict(classes=classes or ['schedule'], regions=regions, missing=missing.most_common(12),
                extra=extra.most_common(12), frame=(fr, fo), inputs=(sorted(inputs[0]), sorted(inputs[1])),
                our_convention=inputs[2], convention=conv, reg_map=reg_map(x, y, ops), x=x, y=y, ops=ops,
                hints=[text for _, text in found], hint_keys={key for key, _ in found})


def side_by_side(d, context=2, width=46):
    x, y, out = d['x'], d['y'], []
    for op, i1, i2, j1, j2 in d['ops']:
        if op == 'equal':
            n = i2 - i1
            show = range(n) if n <= 2 * context else list(range(context)) + [None] + list(range(n - context, n))
            for k in show:
                out.append('   ...' if k is None else f'   {x[i1+k].off:04x} {x[i1+k].text:{width}.{width}} | {y[j1+k].off:04x} {y[j1+k].text}')
            continue
        out.append(f'-- {region_class(x[i1:i2], y[j1:j2])}')
        for k in range(max(i2 - i1, j2 - j1)):
            l = f'{x[i1+k].off:04x} {x[i1+k].text}' if i1 + k < i2 else ''
            r = f'{y[j1+k].off:04x} {y[j1+k].text}' if j1 + k < j2 else ''
            out.append(f'!! {l:{width+5}.{width+5}} | {r}')
    return out
