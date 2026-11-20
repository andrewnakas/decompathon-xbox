# decompathon-xbox

Tools for matching decompilation of original Xbox games: reading the XBE, finding
its functions, compiling one function at a time with the game's own Microsoft
compiler (under Wine on Linux), and proving the result against the retail
executable, byte for byte.

One Python package, one command: `xbx`. A game project keeps its own repository
with an `xbx.toml` that describes it; the tools hold nothing about any one game.

It grew out of the tooling written for a Halo 2 decompilation and is the
toolchain of [jsrf-decomp](https://github.com/andrewnakas/jsrf-decomp), a
matching decompilation of Jet Set Radio Future. It is built to be driven by
people and by AI agents alike, and to make a false match unmergeable whoever
wrote it.

No game data, no Microsoft compiler and no Xbox SDK material is in this
repository or downloaded by it. You bring your own disc and your own licensed
copy of the compiler.

## What is here

| Command | What it does |
|---|---|
| `xbx init DIR` | Start a new game project from your own XBE: config, function table, gates, rules and guides |
| `xbx xbe FILE` | Summary of an XBE: sections, linked libraries and their versions, kernel imports |
| `xbx xiso IMAGE` | List or extract the files of an Xbox disc image |
| `xbx ltcg-probe FILE` | How much of the code uses calling conventions only link-time code generation produces |
| `xbx inventory` | The function table: every function's address, size, owner, kind, entry registers and calls |
| `xbx asm ADDR` | A function's retail disassembly, with call targets, data and strings named |
| `xbx match UNIT...` | Compile units and compare them with retail; says exactly what differs and why |
| `xbx names [ADDR]` | The declaration the tree has settled on for an address |
| `xbx cc SRC OBJ` | One compile with the pinned compiler |
| `xbx gate NAME` | The merge gates: `toolcheck`, `scan`, `split`, `match`, `build` |
| `xbx report` | Progress as objdiff's `report.json`, for [bytematch.dev](https://bytematch.dev) and other trackers; needs no game file |
| `xbx score`, `diff`, `try`, `triage`, `history`, `exemplars`, `idiom`, `search` | The matching workbench: see [docs/WORKBENCH.md](docs/WORKBENCH.md) |

## How a match is proven

A unit is one function in one file, named for its retail address:
`src/funcs/func_0012A340.cpp`. Its function is called `func_0012A340`, and
everything else it refers to is declared with a name that carries its own
address, `func_XXXXXXXX` or `data_XXXXXXXX`. The compiler keeps that name in
every decoration it produces, so the tools read each address from the object
itself. Nothing is declared in a side file, and nothing can be misdeclared.

`xbx match` compiles the unit and compares the object with the executable.
**Nothing is masked.**

- The function's bytes are compared at its address.
- Every relocation is resolved. The address retail holds in that field must be
  the address of the symbol our object names there, so a wrong call target or a
  wrong global is a difference, not a blank.
- Everything else the object would put in the image is verified too. String and
  floating-point constants, switch tables, vtables, exception-handling tables
  and funclets are each given the address retail's own reference implies, every
  reference to one must agree on it, and its contents are compared there.
- Anything in the object that cannot be verified this way fails the unit: named
  global data, dynamic initialisers, a second function riding along.
- A number that falls inside the image's address range without a relocation
  has to be declared a number (`/* const: 0x... */`); a hard-coded address used
  as memory is refused outright.
- One address, one declaration: across all units a function or variable has one
  decorated name, so the units can be linked against each other.

`xbx gate build` then proves the same claim the other way round: it links every
matched unit forward, as a linker would, and requires the bytes that come out
to equal the executable.

## The gates

A game project merges a change only when these pass, on a machine that holds
the game and the compiler (the Decompathon Steward's judge, or any CI you run
yourself):

| Gate | Refuses |
|---|---|
| `toolcheck` | A game file, compiler, flag set or tool commit other than the pinned ones (checksums, banner, a canary object) |
| `scan` | Unit text that is not plain C++: inline assembly, `#pragma`, `naked`, token pasting, statements that only steer the compiler, includes outside the project |
| `split` | A function table that is not what the XBE gives, or that leaves a byte of the game's code unaccounted for |
| `match` | Any unit that is not a proven match; units that disagree about a declaration; a non-matching unit with the wrong score |
| `build` | Matched units that do not link to the game's bytes |

## Starting a project

```sh
uv venv && uv pip install git+https://github.com/andrewnakas/decompathon-xbox    # Python 3.11 or newer; Wine on Linux
.venv/bin/xbx xiso game.iso out default.xbe       # the executable, off your own disc image
.venv/bin/xbx xbe out/default.xbe                 # sections, linked libraries and their versions
.venv/bin/xbx ltcg-probe out/default.xbe          # how much of it a per-object compile can reach
.venv/bin/xbx init mygame-decomp --xbe out/default.xbe --repo yourname/mygame-decomp --title "My Game"
```

`xbx init` writes the whole repository: `xbx.toml` with the executable pinned by sha256, its code sections
and their owners; the function table; the Steward config and the gate wrapper; the pull request template with
its provenance boxes; the CI workflows; the rules and the guides for people and agents. It prints what is left
to do, which is mostly one thing: match about ten small functions by hand to pin the compiler and its flags,
then record the toolchain with `xbx gate toolcheck --record`.

[docs/NEW_PROJECT.md](docs/NEW_PROJECT.md) goes through all of it, from the disc to a repository that merges
matched functions by itself and shows its progress on [bytematch.dev](https://bytematch.dev).

## What is not here

- **Link-time code generation.** Functions the retail linker gave a custom
  calling convention (the table's `conv` column) cannot be reproduced by a
  per-object compile. Matching them needs a whole-program backend; one exists
  for one compiler version in the Halo 2 work and is not ported.
- **A functional rebuild.** The build gate proves bytes, range by range. It
  does not yet produce a bootable XBE.
- **Compiler profiles beyond Visual C++ 7.0 and 7.1.** Others should work
  (the harness only needs `cl.exe`), but none has been run.

## Tests

`python -m pytest tests` needs no game and no compiler: the matcher and the
build gate's linker are tested against each other on hand-written objects, and
every rule of the match is checked by changing one byte and requiring a refusal.

## Credits and licence

GPL-3.0-or-later. See [NOTICE.md](NOTICE.md) for what was derived from where:
chiefly the [halo2-decompiled](https://github.com/kirklandsig/halo2-decompiled)
project's XBE and function-discovery tools, the gate design of the THUG
decompilation, and the Decompathon Steward's report writer.

This project is not affiliated with or endorsed by Microsoft or any game
publisher. "Xbox" is a trademark of Microsoft.
