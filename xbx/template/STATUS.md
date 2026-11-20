# Status

## @@DATE@@: scaffolded

| | |
|---|---|
| Target | `default.xbe`: sha256 `@@SHA256@@`, title id `@@TITLE_ID@@`, linked @@LINKED@@, Xbox SDK libraries version @@LIBRARY_VERSION@@ |
| Toolchain | Microsoft @@COMPILER@@ (`cl` @@BANNER@@) under Wine. **Not pinned yet**: the profile is a guess from the library version and the flags in `xbx.toml` are a starting point. |
| Units | 0 matched (`src/funcs/`), 0 non-matching (`src/nonmatching/`) |
| Merging | Steward with five gates (toolcheck, scan, split, match, build). No AI takes part. |

## Function table

`config/functions.csv` lists @@FUNCTION_COUNT@@ functions.

| Owner | Functions | Bytes | Counts toward |
|---|---|---|---|
@@OWNER_TABLE@@

- Kind `eh` rows are compiler-made exception-handling stubs. They are matched together with the function that
  registers them (the `parent` column) and never get a unit of their own.
- @@CONV_COUNT@@ functions have a non-empty `conv`: they read a register no standard calling convention passes
  arguments in, which means link-time code generation gave them a custom calling convention. A per-object
  compile cannot reproduce those (AGENTS.md).

## Before the first pull request

1. **Pin the compiler.** Match about ten small functions of different shapes by hand (`xbx asm --small 30`,
   `xbx match`). Then compile them again under other flag sets and other compiler versions and keep the
   plainest set that matches all of them. Record the result here as a table of flag sets and match counts.
2. **Record the toolchain**: `xbx gate toolcheck --record` writes `toolchain/checksums.sha256` and
   `tests/fixtures/canary.sha256`. Commit both.
3. **Run every gate**: `tools/gate.sh all`.
4. **Tag library code.** `.text` usually holds library code with no section of its own (the C runtime, the
   platform libraries). Until its ranges are tagged in `xbx.toml` (`[[owners.ranges]]`), the table counts it
   as `game`.

## History

| Date | Milestone |
|---|---|
| @@DATE@@ | Project scaffolded with `xbx init`: executable identified, function table generated. |
