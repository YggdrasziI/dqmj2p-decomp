# Splits

Region map and unit table of this ROM. Empty until Step 2 (port from DQMJ2) and
Step 5 (region map); most regions are expected to follow dqmj2-decomp's
`notes/SPLITS.md` through `notes/DIFF_DQMJ2.md`.

## Modules (dsd analysis, 2026-11-10)

| Module | Address | `.text` end | Functions | Role |
|---|---|---|---|---|
| main | 0x02000000 | 0x020D3FE0 | 6464 | not mapped |
| ITCM | 0x01FF8000 | 0x01FFF894 | 146 | not mapped |
| DTCM | 0x027E0000 | — (data only) | 0 | not mapped |
| ov000 | 0x021D7240 | 0x0222B7E0 | 1948 | unknown |
| ov001 | 0x021D7240 | 0x0222D9A8 | 2246 | unknown |
| ov002 | 0x021D7240 | 0x021DA46C | 76 | unknown |
| ov003 | 0x021D7240 | 0x021E0104 | 199 | unknown |
| ov004 | 0x021D7240 | 0x021D8E9C | 55 | DS Protect 1.28 (dsd extract) |
| ov005 | 0x021D7240 | 0x021E6E24 | 317 | unknown |
| ov006 | 0x021D7240 | 0x02207914 | 2064 | unknown |
| ov007 | 0x021D7240 | 0x021DA06C | 100 | unknown |

## Units

(none yet)
