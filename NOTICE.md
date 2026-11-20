# Notice

decompathon-xbox is free software under the GNU General Public License,
version 3 or (at your option) any later version. See [LICENSE](LICENSE).

## Derived work

Parts of this package are derived from the
[halo2-decompiled](https://github.com/kirklandsig/halo2-decompiled) project,
which is licensed GPL-3.0-or-later:

| Here | From |
|---|---|
| `xbx/xbe.py` | `tools/xbe.py` |
| `xbx/xiso.py` | `tools/xiso_extract.py` |
| `xbx/functions.py` | `tools/functions.py` |
| `xbx/ltcg_probe.py` | `tools/ltcg_probe.py` |
| `xbx/inventory.py` | `tools/inventory.py` (the table and its owners; the exception-table pass and the join/cut pass are new) |
| `xbx/lockstep.py` | `differing_instructions` of `tools/check.py` |
| `xbx/workbench/` | the h2m workbench written for that project (`tools/h2m/`), and the mutations of `tools/permute.py` it uses |

Each file names its origin in its first lines.

`xbx/report.py` is derived from `tools/objdiff_report.py` of the
[Decompathon Steward](https://github.com/andrewnakas/decompathon-steward).

The five gates follow the design of the
[THUG decompilation](https://github.com/andrewnakas/thug-decomp)'s gates
(toolcheck, scan, split, match, build), rewritten for x86, COFF and the XBE.

## What is not included

No game code or data, no Microsoft compiler, header or library, and nothing
from the Xbox Development Kit. The tools read a game executable and run a
compiler that you supply.
