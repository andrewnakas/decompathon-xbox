# Starting a project

From your own disc to a repository that merges matched functions by itself and shows its progress.
[jsrf-decomp](https://github.com/andrewnakas/jsrf-decomp) was started exactly this way and is the working
example for every step.

## What you need

- **Your own copy of the game**, dumped yourself. Nothing here downloads a game, and no game data ever goes
  into the repository.
- **Your own licensed Microsoft compiler** of the game's era: Visual C++ 7.0 (Visual Studio .NET 2002,
  `cl` 13.00.9466) or 7.1 (Visual Studio .NET 2003, `cl` 13.10.3077). You need its `bin/`, `include/` and
  `lib/` directories. Nothing here downloads a compiler either.
- Linux x86_64 (or WSL2) with Wine, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), git and `gh`.
- Not the Xbox Development Kit. It is confidential material and the projects built with these tools do not
  use it as a source.

## 1. Look at the executable

```sh
uv venv && uv pip install git+https://github.com/andrewnakas/decompathon-xbox
.venv/bin/xbx xiso game.iso                       # list the disc image's files
.venv/bin/xbx xiso game.iso out default.xbe       # extract the executable
.venv/bin/xbx xbe out/default.xbe                 # sections, linked libraries and their versions, kernel imports
.venv/bin/xbx ltcg-probe out/default.xbe          # how much of the code has custom calling conventions
```

The library versions date the build. The probe tells you how much of the game a per-object compile can
reach: a function that takes an argument in a register no standard convention uses was compiled with
link-time code generation, and these tools cannot reproduce that yet. A few percent is fine. A game compiled
that way throughout is a much harder project.

## 2. Scaffold the repository

```sh
.venv/bin/xbx init mygame-decomp --xbe out/default.xbe --repo yourname/mygame-decomp --title "My Game"
```

This writes the whole repository: `xbx.toml` with the executable pinned by sha256, the function table
(`config/functions.csv`), the Steward config, the gate wrapper, the pull request template with its provenance
boxes, the CI workflows, the rules and the guides. It links your executable as `orig/default.xbe`, which is
git-ignored.

It finds the code sections itself (the section flags do not tell code from data, so it follows the calls)
and gives the sections that the platform's libraries bring with them an owner of their own. Check both lists
in `xbx.toml`. Then read the new `README.md`, `AGENTS.md` and `PROVENANCE.md` and make them yours: they are
your project's rules.

The licence it writes is CC0. Replace `LICENSE` and the README's last section if you want another.

## 3. Pin the compiler

`xbx init` guesses the compiler from the library version and starts the flags at `/O2`. Both are guesses
until the game's own code confirms them:

```sh
cd mygame-decomp
ln -s /path/to/your/msvc toolchain/msvc           # bin/ include/ lib/
uv venv && uv pip install -r requirements.txt
.venv/bin/xbx cc --setup                          # creates the Wine prefix
.venv/bin/xbx asm --small 30                      # the smallest functions
```

1. Match about ten small functions of different shapes by hand: a loop, a member function, a virtual call,
   floating point, a function with a string. Write each as `src/funcs/func_XXXXXXXX.cpp` and run
   `.venv/bin/xbx match` on it until it says `MATCH`. `.venv/bin/xbx diff ADDR` shows the retail code next
   to yours.
2. Compile the same ten again under other flag sets (edit `cflags` in `xbx.toml`, run
   `.venv/bin/xbx match --all --no-cache -q`) and, if you have it, under the other compiler version. Keep
   the plainest set that matches all ten, and write the table of what you tried into `STATUS.md`. A wrong
   compiler version usually shows at once: nothing but the most trivial functions match.
3. Record the toolchain and run every gate:

```sh
.venv/bin/xbx gate toolcheck --record             # the compiler's checksums and the canary object
tools/gate.sh all                                 # toolcheck, scan, split, match, build
```

Commit `toolchain/checksums.sha256` and `tests/fixtures/canary.sha256`. From here on, a judge with another
compiler, other flags or another executable fails the first gate.

If the split gate reports a problem, the function table is wrong somewhere: a function start the discovery
missed, or a block it took for a function. Correct it in `xbx.toml` under `[inventory]` (`known_starts`,
`not_starts`, `data_ranges`) and run `xbx inventory` again. Never edit the table by hand.

## 4. Publish

```sh
git init -b main && git add -A && git commit -m "Start the decompilation"
gh repo create yourname/mygame-decomp --private --source . --push
```

Before you make it public, check what you are publishing: no executable or disc image, no compiler files, no
object files, nothing from a development kit. The `lint` workflow refuses the obvious cases on every push.

Two workflows run on GitHub and need neither the game nor the compiler: `lint` (no game data, a valid
Steward config) and `progress`, which builds the progress report from the function table and the unit files
and uploads it as the artifact `<version>_report`.

## 5. Merging: the judge

A match can only be checked on a machine that holds the game and the compiler, so pull requests are judged
there and not on GitHub. The scaffold is set up for
[Decompathon Steward](https://github.com/andrewnakas/decompathon-steward), which watches the repository,
runs the five gates on every pull request from a clean checkout of `main` with only the pull request's unit
files copied in, and merges what passes. See its README for running your own.

What the project side needs:

- A **judge workspace**: a second clone of the repository on that machine, with `orig/default.xbe` and
  `toolchain/msvc` linked the same way and a Wine prefix of its own. `tools/gate.sh all` must pass in it.
- `steward.toml`, which `xbx init` wrote: the gates, the protected paths (everything that decides what
  "matching" means is maintainer-only) and the merge policy.
- The service unit in `tools/linux/`, which runs Steward for this repository.

You can also run the gates any other way you trust. They are one command each, they print
`PROBLEM <file>: <why>` for everything they refuse, and their exit status is the verdict.

## 6. Progress page

Register the repository at [bytematch.dev](https://bytematch.dev/manage/new). It reads the `<version>_report`
artifact of the `progress` workflow's runs on `main`. The README's badges point at it already.

The headline counts the owners listed under `[owners] headline` in `xbx.toml` (`game` by default). Library
code is a separate category. `.text` usually also holds library code with no section of its own (the C
runtime, the platform libraries): tag its ranges with `[[owners.ranges]]` as you identify them, and the
headline's denominator shrinks to the game's own code.

## 7. Agents

The repository is written to be worked by AI agents as well as people: `START_HERE.md` ends with a prompt to
paste, `AGENTS.md` is the contract, and the gates do not care who wrote a unit.

- One clone per agent, each with its own `work/` directory.
- Give each agent its own address range (a lane) in the table in `AGENTS.md`, so that two agents never
  write the same function.
- Smallest functions first: `xbx asm --list 60 --from ADDR --to ADDR` lists a lane's open ones.
- One address has one declaration across the whole tree. `xbx names ADDR` prints the one in use, and the
  match gate refuses a unit that disagrees. This is what keeps hundreds of separately written units
  linkable.
- [WORKBENCH.md](WORKBENCH.md) describes the tools for near misses.

## What is not covered yet

- Functions with a custom calling convention (link-time code generation).
- Linking the matched units into a bootable executable. The build gate proves bytes range by range.
- Identifying library code inside `.text` automatically.
