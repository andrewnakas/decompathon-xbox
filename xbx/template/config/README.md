# config

| File | What it is |
|---|---|
| `functions.csv` | The function table, generated from the executable by `xbx inventory` and checked by the split gate. Addresses and sizes only. Never edited by hand: a wrong row is fixed in `xbx.toml` (`[inventory]`) or in the tools. |
| `symbols.csv` | Names that cannot carry their own address: the C++ runtime's and the platform's. Everything else is called `func_XXXXXXXX` or `data_XXXXXXXX`. |

## symbols.csv: the evidence for each name

A name is added only with evidence from the executable itself or from public documentation (PROVENANCE.md).
Each row of `symbols.csv` gets a row here.

| Name | Address | Evidence |
|---|---|---|
