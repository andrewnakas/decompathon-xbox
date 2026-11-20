# The matching workbench

Eight `xbx` commands for the last part of matching a function: the unit
compiles, the difference is small, and the question is which spelling makes
the compiler emit retail's bytes. The workbench scores variants of a unit
against retail without touching the tree, explains the difference, remembers
every variant it has scored, and can search source rewrites on its own.

`xbx match` and the merge gates stay the judge of what matches. The workbench
compiles and compares through the same code (`xbx.match.check`), so `MATCH`
here means what it means there.

## Requirements

What `xbx match` needs, and nothing else: the project's `xbx.toml`, your own
copy of the retail executable, the pinned compiler (under Wine on Linux), and
capstone. The ledger is SQLite from the standard library.

Run the commands from anywhere inside the project. The settings are the
project's (`xbx/project.py`):

| variable | default | used for |
| --- | --- | --- |
| `XBX_ROOT` | the nearest directory with `xbx.toml` | the tree to work on |
| `XBX_OUT` | `<root>/build/xbx` | variants, the ledger `xbx.db`, the corpus index, search results |
| `XBX_TOOL_TIMEOUT`, `XBX_TOOL_ATTEMPTS` | 120, 3 | seconds per compiler run and how often it is retried (`search` uses 30 seconds unless the variable is set) |

Nothing limits memory or the number of jobs for you. Every command compiles
one variant at a time; one compile takes a second or two and little memory.

## Layout

| file | what |
| --- | --- |
| `xbx/workbench/cli.py` | the eight commands |
| `xbx/workbench/scorer.py` | one path: write the variant, compile and compare it (`match.check`), measure, record |
| `xbx/workbench/asm.py` | one disassembler and one normaliser with masking levels |
| `xbx/lockstep.py` | the lockstep instruction comparison |
| `xbx/workbench/metrics.py` | the metric vector of a variant |
| `xbx/workbench/ledger.py` | the SQLite record of every variant |
| `xbx/workbench/classify.py` | diagnosis: difference classes, register map, observations, side-by-side |
| `xbx/workbench/corpus.py` | index of the retail code of every matched function |
| `xbx/workbench/mutators.py`, `local_mutations.py`, `permute_lib.py` | source rewrites of a function body: the techniques below as enumerations, and random-sampled mutations |
| `xbx/workbench/search.py` | beam search over those rewrites |

## Variants

A variant is the full text of a unit file: the `/* cflags: */` and
`/* sym: */` lines, the declarations, and the one function named
`func_XXXXXXXX`. Write it anywhere outside the source tiers; the convention is
`<XBX_OUT>/<addr>/name.cpp`. The workbench compiles its own copy of the text in
`<XBX_OUT>/<addr>/` with the project's flags and include directories, and
keeps it there as `v_<hash>.cpp`, so the tree is never edited while you
experiment, and several people or agents can work on one checkout.

A header change cannot be tried from a variant file: paste the changed
declarations at the top of the variant instead.

## The numbers

Every variant gets a vector, not one number:

- `MATCH`: `match.check` proves the unit under the gate's rules: every byte of
  the function equals retail's, every relocation resolves to the address
  retail holds, and nothing unverified rides along in the object. It is not a
  byte compare with the address fields masked.
- `lock`: the lockstep count plus the length difference in bytes. Both sides
  are decoded from offset 0 and compared instruction by instruction with no
  alignment, so one instruction inserted early counts everything after it.
- `dist`: aligned edit distance between the two instruction listings,
  registers kept.
- `sem`: multiset difference of the instructions with registers, stack slots
  and labels masked: operations one side has and the other lacks, whatever
  their order.
- `frame`: our prologue's `sub esp` minus retail's.
- `first`: retail offset of the first aligned difference.

Steer by `sem` and `dist`, confirm by `lock`, and let `xbx match` be the
judge. `sem` falling means the right operations are appearing; `dist` falling
with `sem` at 0 means the order and the registers are converging; `lock` can
jump either way while both improve, because it is positional.

All of `lock`, `dist` and `sem` can be 0 without a `MATCH`: the instructions
are retail's and something else is not (a call or a global that resolves to
another address than retail's, a constant, a size, a leftover `/* sym: */`
line). `score` and `diff` print each such problem on a line starting with `!`;
no rewrite of the body fixes those.

## Commands

ADDR is hex, with or without `0x`.

| command | example | what it prints |
| --- | --- | --- |
| `score ADDR [FILE...]` | `xbx score 00011070` | the vector of the tree's unit, or of each variant file given, and the problems under it (`-q`: one line each) |
| `diff ADDR [FILE]` | `xbx diff 00011070 build/xbx/00011070/a.cpp` | vector, difference classes, retail-only and ours-only instructions, the retail-to-ours register map, observations, then the aligned side-by-side (retail left, `!!` = differs) |
| `try ADDR VARIANTS.txt [--start BASE.cpp]` | `xbx try 00011070 build/xbx/00011070/v.txt` | one line per variant. Variants are separated by a line of `=====`. Each is a complete unit text, or, with `--start`, a replacement for the function's body that is put into BASE.cpp (with or without its outer braces). `-v` adds each variant's problems |
| `history ADDR` | `xbx history 00011070` | the best variants in the ledger, with their files |
| `triage [ADDR\|FILE...] [--owner P] [--limit N]` | `xbx triage --owner game` | the `diff` header of every unit of the non-matching tier (or of the addresses and unit files given), best first |
| `idiom ADDR [FILE]` | `xbx idiom 00011070` | each instruction only retail has, looked up in matched functions of the same owner: read their source for the spelling |
| `exemplars ADDR [FILE]` | `xbx exemplars 00011070` | matched functions whose retail code contains retail's sequence at each divergent region, and the nearest matched functions overall |
| `search ADDR [--start F] [--time-limit S] [--beam N] [--seed K] [--only m1,m2]` | `xbx search 00011070 --time-limit 600` | beam search over the source rewrites; improvements are reduced to the hunks that matter and written to `<XBX_OUT>/<addr>/search/` |

`xbx asm ADDR` prints the retail listing with nothing masked and each address named.

The fastest way to use `score` and `try` is to generate a family of variants
with a few lines of Python (`itertools.product` over independent choices) and
score them in one call. The ledger answers a repeated text at once.

### What `diff` prints

    00011070 build/xbx/00011070/a.cpp size 47  lock    1  dist    1  sem   2  frame +0  first +0xc
      classes: semantic   frame retail 0x0 ours 0x0
      ! code differs: 1 bytes, first at +0xc
      retail only: js L x1
      ours only:   jl L x1
       0000 push esi                                       | 0000 push esi
       0001 mov esi, ecx                                   | 0001 mov esi, ecx
       ...
       0007 mov eax, dword ptr [esi + 4]                   | 0007 mov eax, dword ptr [esi + 4]
       000a test eax, eax                                  | 000a test eax, eax
    -- semantic
    !! 000c js L26                                         | 000c jl L26
       000e call ADDR                                      | 000e call ADDR
       0013 mov eax, dword ptr [esi]                       | 0013 mov eax, dword ptr [esi]
       ...

The header is the function, the file, the function's size in the function
table and the vector; `triage` prints the lines down to the `HINT:` lines for
each unit. In the listing an address reads `ADDR` (the target of a call, a
global), a branch target inside the function `L<offset>`; everything else is
as the disassembler prints it. Lines starting with `!` are the problems
`xbx match --strict` reports for the same text.

The classes (a function can carry several):

| class | meaning |
| --- | --- |
| `semantic` | operations one side has and the other lacks |
| `width` | the gap is `movsx`/`movzx`/operand size only |
| `fold` | retail keeps an `fdiv`/`fmul`/`imul`/shift/`lea` that ours folded, or the reverse |
| `stack` | the prologue's frame differs |
| `regs` | the same operations in different registers (the register map says which) |
| `schedule` | the same instructions in a different order |
| `convention` | retail takes a value in a register at entry and ours takes none there |

`REGISTER INPUTS [...]` after the frame lists those registers. `ecx` is `this`
of a member function (ours takes `ecx` when the unit declares its function as
a member; the decorated name in the object says so), `ecx` and `edx` are the
register arguments of `__fastcall`. `eax`, `ebx`, `esi` and `edi` come from the
`conv` column of the function table: registers no standard convention passes
arguments in. A declaration cannot ask for those; the workbench reports them
and does nothing about them.

`HINT:` lines are observations: what the two listings show and the source
shape that has been seen to produce retail's side. They are leads, not
instructions.

### search

`search` takes the unit in the tree (or `--start FILE`), and works on the body
of its `func_XXXXXXXX` only; the rest of the unit is carried along unchanged.
The rewrites read a body whose block braces stand on their own lines, so the
body is first put in that form (the search checks that this does not change
the score, and keeps the body as written if it does). The results are
therefore in that brace style: carry the change into the unit by hand.

- Candidates come from the enumerated techniques (`t1_inplace`, `t2_byref`,
  `t4_decl_move`, `t4_arg_local`, `t4_member_local`, `t5_tail_dup`, `t7_base`,
  `t9_width`, `t11_constref`, `t11_member_alias`, `sse_splat`, `loop_forms`,
  `loop_cursor`: the numbers are those of "Source techniques" below) and from
  mutations sampled at random (`up_*`: operand swaps, statement moves,
  temporaries introduced and inlined, loop and branch forms, bare blocks,
  parentheses). `--only` restricts the search to the mutators named.
- Each candidate is scored through the ledger with origin `search:<mutator>`.
  The mutators are weighted by the classes and observations of the starting
  diff and by how often each has improved its parent across every function in
  the ledger.
- The beam keeps the N best bodies by (match, `dist`, `sem`, `lock`). Every
  improvement is ablated back toward the start (each changed hunk reverted
  alone, kept reverted when the result is no worse) and written to
  `search/best_NNNN.cpp` with a line in `search/log.txt`. `lock_NNNN.cpp` is a
  body with a lower lockstep count that is not the best by distance.
- It stops at a `MATCH`, when the time is up, or when nothing is left to try.
  The time is checked between compiles, and scoring the start counts. It
  stops at once when the start's code already equals retail's up to address
  fields, since no rewrite of the body fixes what is left.

`search` and `try --start` need the function to be named `func_XXXXXXXX` in
the text. A unit whose function is a special member placed by a `/* sym: */`
line can be scored and diffed, not searched.

Left out on purpose: rewrites that change behaviour or that review rejects
(`volatile`, retyping, argument swaps at calls, shared constants hidden behind
a variable). A candidate is dropped before it is compiled when it adds
`volatile`, `noinline`, a `#pragma`, a `goto`, a local declared twice, or a
local that is never read. A result that adds a local read in exactly one place
without reaching a match is written as `flag_NNNN.cpp`, not `best_NNNN.cpp`.

## The ledger

`<XBX_OUT>/xbx.db` holds one row per (function, variant text): the vector,
the problems, where the variant came from (`file`, `try`, `search:<mutator>`),
its parent in a search, and its file. `history` reads it; `search` takes its
mutator success rates from it.

The key of a row is a hash of the variant's text together with everything
else that decides its score: the compiler profile and flags, the executable,
the function and symbol tables, and the names, sizes and modification times
of the headers. A change to any of them starts a new cache; old rows stay in
the file and are simply not found. `history` still lists them, marked
`(scored before the flags, headers or tables changed)`.

A compile that the toolchain gave up on (a wedged Wine, a missing tool) is not
recorded, so the next run tries again. A compiler error is recorded like any
other result.

## Reading a diff

- Find the first divergence and the retail-only instructions, and ask what
  source would make the compiler emit exactly that: an extra load is a
  separate read; an address formed is a reference; a different block order is
  a different statement structure; a different frame slot is a different local
  or scope.
- Register-only differences usually follow from which values are named locals
  and from helper shapes. Reordering statements rarely helps.
- `das`, `add byte ptr [eax], al` and similar at the end of a listing are
  jump tables decoded as code.

## Source techniques

Each line is a source shape that produced a match or a large drop. They were
collected with MSVC 7.1 on another Xbox project; your project's compiler may
be a different version, so read them as things to try, and trust the listing.
The first group is also enumerated by `xbx/workbench/mutators.py`, which is
what `search` runs.

Shapes that point at their source:

1. Retail updates memory in place (`dec dword ptr [reg+N]`) where ours loads,
   changes and stores: `long &size = points.size; --size;`.
2. Retail forms a member's address and reads through it (`add reg, N; mov reg,
   [reg]`): the member is passed by reference to an inline helper. An index
   evaluated before the object is the argument evaluation order of such a
   helper.
3. Reciprocal then multiply (`fdivr [1.0]; fmulp`) where ours divides once: a
   named reciprocal.
4. A call argument read into a local declared at the right place. Sweep which
   reads become locals and where they are declared.
5. Retail puts the shared loop step right after the first branch and later
   branches jump back to it: write the step in each branch and the compiler
   merges them into the first.
6. A loop helper called with a count of one whose retail code has a different
   operand order is a separate single-element helper.
7. A base pointer read once per pass (`T *base = array; base[i] = base[i +
   1];`) fixes `lea [eax+ecx]` against `[ecx+eax]`.
8. A struct with a zeroing constructor gives one zero register for several
   zero stores.
9. Retail without `movsx`: the local is `long`, not `short`.
10. Temporaries and the call using them in a bare `{}` block; a member read
    into a named const local right before its use; `_mm_store_ps` and
    `_mm_load_ps` to force a store or a reload; parenthesised float products; a
    result variable declared before a loop (a dead stack store); a count-first
    index search helper.
11. Retail forms `base + K` early in its own register (`lea esi, [eax+0x30]`)
    where ours folds the offset late: bind the member with a constant reference
    to the pointer, `unsigned char const *const &nodes = tree->nodes;`. A
    by-value local is folded back. Its position among the opening statements
    matters.
12. When only registers or order differ, run `exemplars` and rewrite the body
    statement by statement in the style of a matched function with the same
    retail shape: which values are named, which variables are reused for later
    terms, pointer locals for outputs, operand order of the final adds.
13. A guard that repeats a loop's own entry test, `if (0 < count) { for (i = 0;
    i < count; i++) ... }`, emits no code and can change which values get esi,
    edi and ebx in the whole function. The spelling matters: `0 < count`
    worked; `count > 0`, `count != 0` and `count` added code.

The reference barrier (a local with a reference or pointer bound to it is not
substituted back into the expressions that use it):

14. It keeps two sub-expressions apart that would fold into one tree, sets the
    destination of an SSE operation, keeps a pointer chain in one register,
    and keeps a value's register occupied past its last use so the next value
    goes elsewhere. Helpers taking `long &` fold back.
15. A barrier that is not needed can be what breaks retail's assignment: try
    removing barriers as well as adding them.
16. A float store scheduled before an integer operation that retail does
    first: read the float's bits through a reference bound to the float local.

Helpers, accessors and constructors:

17. A trivial accessor or by-value setter shifts the scratch rotation without
    emitting code. Sweep accessor and setter combinations when only a scratch
    register differs.
18. A loop bound or start taken through a trivial inline accessor that takes a
    const reference fixed scratch registers swapped inside a loop where a
    hundred local spellings did not.
19. An accessor used for the loop bound gives `mov eax, [mem]; cmp eax, ecx`
    instead of `cmp [mem], eax`.
20. A call through a table entry scheduled wrongly: fetch the entry through an
    inline accessor returning the function pointer.
21. An inline helper's parameter order sets the order in which the caller sets
    up its register arguments.
22. A constructor body, not an initialiser list, in store order; a field read
    through the stored member is pinned after that store, one read through the
    parameter is free to move.
23. Retail loads several values into registers before a sum that ours folds:
    a derived struct whose constructor takes them as parameters; the parameter
    order matters.
24. A member address in the wrong register with stores in the wrong order can
    be a derived-to-base conversion: make the member a base class and pass
    `this`.
25. A fresh `xor reg, reg` on a failure path with the result pointer in eax:
    the body is a `__forceinline` member returning a literal, and the outer
    function returns the pointer it was given.
26. Naming a copy-constructor source as a reference (`T const &source =
    *p->q; T copy(source);`) moves the argument load by one scratch register.
27. A global read through an inline accessor is loaded earlier than a direct
    read.
28. `mov rB, rA` right after `lea rB, [..]; mov rA, [rB+N]` is a second source
    read of the same element: write it through the original array expression,
    not through the reference or a local.

Statements, loops and blocks:

29. Moving a loop's final `return` inside an endless loop (`for (;;) { ...; if
    (--tries <= 0) return 0; }`) moves the shared return block.
30. `do { ...; goto outside; } while (--n >= 0)` rewritten as `if (n >= 0) {
    for (; n >= 0; n--) { ...; return 0; } }` gave retail's loop bottom and
    result-block order in every inlined copy.
31. Failures leaving a loop by `break` with one `if (failed != 0) { restart }`
    after the loop place the shared failure block after the restart code;
    `goto restart` from inside does not.
32. `x = (float)a - b;` split into `x = (float)a; x -= b;` moves where an
    integer operation or a cursor step lands.
33. A float sum written as one expression is put in the compiler's own order,
    so permuting its terms does nothing; written as `x = p1; x += p2; ...` it
    keeps the source order.
34. Operand order of commutative intrinsics and float operators is the only
    spelling inside a block that changes code (`_mm_mul_ps(b, scale)` against
    `_mm_mul_ps(scale, b)`). Count the instructions of each block against
    retail first (padding can hide a different count), then enumerate the
    commutations of that block's products and sums. Integer `+ & |` and
    pointer-add commutation never changed code; flipping a comparison did.
35. Replacing an `_mm_store_ps`/`_mm_load_ps` round trip with a plain
    assignment plus `T const &ref = local;` references.
36. Field-wise fill of a local struct followed by a whole-struct assignment
    gives `lea` and `[reg+4]` reads.
37. The emission order of an OR chain of byte loads follows the base the
    offsets are relative to, not source order or tree shape; an `x ^= key;` in
    its own statement turns it into a Horner chain.
38. Shift count `mov ecx, eax` rather than `mov cl, al`: keep the byte as a
    byte variable and widen both the switch value and the count from it.
39. Per-group block scopes with their own locals move stack slots.
40. A commutative x87 product or compare whose two operands come out swapped:
    the order depended on how many initialised locals were defined before the
    local's own definition. Folding one-use locals into their uses fixed it
    where moving the declaration alone did nothing.
41. Two words passed to an inline helper both by reference, assigned in the
    same order in both arms of an `if`/`else`, decided which one took a dead
    argument's home and which a frame cell.

What the compiler could see (a unit holds one function, so every callee that
is declared and not defined in it is opaque to the compiler; these say when
retail's code shows that it was not):

42. Retail reloading pointers after every store where ours caches them: the
    callee was in another translation unit in retail, and ours shows the
    compiler its body.
43. Arguments arriving in eax/ebx with caller cleanup: a `static` function
    whose callers were all in its translation unit. `__fastcall` is not it.
44. A store to an address-taken local stays ahead of later loads through
    pointers read from memory, unless every callee that receives the local's
    address is visible to the compiler.
45. A user-written destructor, even an empty one, stores the class vtable; an
    implicit one does not.
46. In `/GX` code, `throw()` on a callee's declaration tells the caller it
    cannot throw (seen to fix a stack home).

Instruction patterns and the source that produced them:

| retail pattern | source |
| --- | --- |
| `mov r, [m]; dec r; js` | `for (int k = array->size - 1; k >= 0; k--)` with the member read inside the loop's initialiser |
| `dec r; jns` | the close of the same loop |
| `add r, x; jns` | a sum tested as one expression, `if ((steps[k] + value) < 0)` |
| `mov r1, r2; cmp r1, [m]` | a call result compared and then consumed by a `switch` |

## Compiler behaviour seen in the generated code

These are observations of MSVC 7.1 (13.10.3077) output on another project,
made by compiling small sources and many variants and comparing the code.
None is taken from any documentation, a rule that held many times can still
fail, and another compiler version can differ: check each against the
listing.

Callee-saved registers (which long-lived value gets esi, edi, ebx, ebp):

- A value's priority grows with its uses, each weighted by 2 to the power of
  its loop depth. Conditionals do not change the weight of a use, and neither
  does the way a loop is spelled.
- The term a block contributes also scales with the number of register
  candidates referenced in that block. A named local that holds a load result
  and is used across instructions is a candidate. A named sum or size that
  folds into its uses is not. The return value of an inline helper is a
  candidate; parameters that fold are not.
- An `if` placed inside a value's live range, with no use of the value in it,
  lowered that value's priority; the same `if` outside the live range had no
  effect.
- Equal priorities go to the value whose last definition comes first; for
  single-definition values, the one declared first. Prologue push order says
  nothing about it.
- Extra uses do not move a value steadily up through ebx, edi, esi: in one
  function one extra use of a parameter gave ebx, two edi, three esi, while
  one to four extra uses of `this` left it in ebx.
- So to move a value from one callee-saved register to another: give it one
  more weighted use than its rival, take one from the rival (often a temporary
  at loop depth two), or change how many candidates its blocks reference.

A shared constant kept in a register (typically zero):

- A store of the constant to memory counts as a use. Compares against it and
  register copies of it do not.
- When uses minus reloads is not positive, the compiler reloads the constant
  at each use instead of keeping it in a register.
- An unfused compare against zero is one more use and leaves the instructions
  the same. A loop start read through a size accessor that takes a const
  reference produces one.

Values split between a register and memory:

- When a value is reloaded on a control-flow edge together with others, the
  reloads appear in the order in which the pieces came into being. A named
  local declared between two others can flip that order.
- A value retail keeps in a scratch register with a stack home is not reached
  by adding uses: that difference is a split value, not a priority contest.

Scratch registers:

- eax, ecx, edx are handed out in turn, skipping busy ones. A value that needs
  a fixed register (a call receiver in ecx) takes it without advancing the
  turn. A scratch swap at one call therefore often depends on how many scratch
  values were allocated earlier in the function, not on code near the call.
- Of two independent values, the one used last is allocated first; load order
  in the source does not decide.
- XMM: the lowest free register, the right operand of an intrinsic first, the
  result in the left operand's register. A `_mm_set_ss(constant)` named as a
  local is a real operation and its declaration order sets the registers;
  plain loads and named copies move nothing.
- A global 16-byte constant is loaded at the top of its block in every
  spelling; a local in memory is loaded late, at its use.

Stack layout:

- Four-byte cells (address-taken scalars, spills, argument homes) are at the
  bottom, aggregates above them in ascending size. With equal sizes, more
  references in the source text (no loop weighting) means a lower address,
  then the earlier last reference.
- Declaration order, names and first use of uninitialised locals move no slot.
- Locals whose address escapes share a slot only across disjoint `{}` scopes;
  arrays and spills that do not escape share by liveness. Spills reuse dead
  parameters' argument homes first, lowest address first.
- A one-byte struct result used through an inline member is laid out as an
  aggregate; read as a field it is a narrow scalar at the bottom.
- A copy into a `const` local passed by address in two places lets the
  compiler reuse a computed vector across the stores in between.

Folding:

- A test against a sentinel that an inlined helper stored is folded away.
  Retail not folding it means the value was not trackable in that caller. A
  float to double to float round trip inside a bool helper hides a stored
  float constant from that folding.
- Store operand order `[cursor+offset]` against `[offset+cursor]` in
  strength-reduced loops followed function-wide structure (code after the
  loop, grouping of frame locals), never the loop's own spelling.

Transplanting:

- Copying the helper shape of a matched sibling word for word, redundant
  tests included, transplants its code generation into the function that
  inlines the copy. When a sibling with the same retail shape is matched,
  start from its helpers before trying anything else.

## Source rules

The workbench will happily score source your project does not accept. Its
rules for what a unit may contain still hold for anything proposed. The
matches these techniques come from kept to: no `volatile`, no
`__declspec(noinline)`, no pragmas in bodies, no argument swaps at calls, no
dead locals, names that describe behaviour. `search` holds its candidates to
the same (see above), and flags a result that adds a one-use local without
reaching a match.
