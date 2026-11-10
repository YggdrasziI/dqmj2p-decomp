# Compiler and SDK

Evidence about the compiler, the SDK and the runtime of this ROM. dqmj2-decomp's
`notes/COMPILER.md` holds the analysis for DQMJ2; a fact from there is recorded here
only once checked on this ROM.

## SDK markers (checked 2026-11-10)

- NitroSDK version word `0x04027539` (4.2, relstep 30009), `extract/jpn/arm9/arm9.yaml`.
- `[SDK+NINTENDO:BACKUP]`, `[SDK+UBIQUITOUS:CPS]`,
  `[SDK+NINTENDO:WiFi2.1.30003.0709200229]`, `[SDK+UBIQUITOUS:SSL]`,
  `[SDK+NINTENDO:DWC3.1.30006.20090131.2222_DWC_3_1_PLUS6]` at the same addresses as in
  DQMJ2 (0x02000B8C-0x02000BFC).

Same SDK version and the same library builds as DQMJ2 are therefore likely; whether
the library code is byte-identical is checked function by function in Step 2
(`notes/DIFF_DQMJ2.md`).

## Compiler (not proven yet)

`tools/configure.py` uses DQMJ2's settings: mwccarm 2.0/sp2p2 and DQMJ2's game flags.
Hypothesis until a unit compiles to 100 % against this ROM (ROADMAP Step 3).
