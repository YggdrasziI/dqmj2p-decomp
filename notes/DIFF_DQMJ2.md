# Differences with DQMJ2

Map of this ROM against Dragon Quest Monsters: Joker 2 (`CJRJ`, dqmj2-decomp). For
each module, its functions are classified as:

- **identical**: same size and bytes with relocations masked (`tools/port_symbols.py`);
  names and units proven in DQMJ2 are ported;
- **modified**: a DQMJ2 counterpart exists (same callers, strings or data) but the
  bytes differ; DQMJ2's source is the starting point;
- **new**: no counterpart; decompiled from scratch.

A classification is evidence of bytes, not of source: an identical function in
another unit or with other data stays to check when its unit is built.

## Modules

| Pro module | DQMJ2 counterpart | Identical | Modified | New | Evidence |
|---|---|---|---|---|---|
| main | main | — | — | — | not compared yet |
| ITCM | ITCM | — | — | — | not compared yet |
| ov000-ov007 | ? | — | — | — | not compared yet |

Known so far (2026-11-10, header and dsd analysis only):

- 8 overlays against 6 in DQMJ2; all load at 0x021D7240 (0x021CB360 in DQMJ2), so the
  main module's `.bss` grew by 0xBEE0 bytes.
- DS Protect is in ov004 here, in ov002 in DQMJ2.
- Same SDK markers at the same addresses (`notes/COMPILER.md`).
- The main module's `.text` ends at 0x020D3FE0 (0x020BCE10 in DQMJ2): 0x171D0 more
  bytes of code.
