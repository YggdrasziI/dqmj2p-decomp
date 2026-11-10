#!/usr/bin/env python3
"""Run `dsd lcf`, then keep `.exception` alive in the dead-stripping link and define
the NitroSDK link-time constants.

The link strips unused code (`-dead`), as the original link did: mwccarm emits the
base-object and complete-object variants of every constructor and destructor
(C1/C2, D1/D2), and the ROM holds only the one that is used. dsd's KEEP_SECTION
lists `.init`, `.ctor` and `.exceptix`, but not `.exception`, whose records nothing
refers to by relocation, so the linker would drop them.

The NitroSDK's own link script defines constants such as the IRQ stack size as
absolute symbols. The SDK tests them at run time, so their sources reference them
as symbols, and the ROM's literal pools hold their values (no relocation): they
are defined here with the values found in those pools (ABSOLUTE_SYMBOLS).

Every function of the ROM was linked, so every function of a unit that has a source
is listed in a FORCE_ACTIVE block: `-dead` would otherwise drop one that nothing
calls (an `asm` function ignores `#pragma force_active`), and the C1/C2 variant that
the ROM lacks is not in symbols.txt, so it is still stripped. Functions of units
without a source are in dsd's delinked objects and are kept as they are.

Usage (from the ninja lcf rule):
  python tools/lcf_keep.py <lcf file> <dsd command and arguments...>
"""

import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNC = re.compile(r"^(\S+) kind:function\(\S+\) addr:(0x[0-9a-f]+)")

KEEP = ".exception"

# name -> value, read from the literal pools of the functions that use them.
ABSOLUTE_SYMBOLS: dict[str, int] = {
    "SDK_IRQ_STACKSIZE": 0x1000,
    "SDK_SYS_STACKSIZE": 0x1000,
    "SDK_SECTION_ARENA_DTCM_START": 0x027E0080,
    # The overlay digest table is empty: both bounds are one address
    "SDK_OVERLAY_DIGEST": 0x020CBBFC,
    "SDK_OVERLAY_DIGEST_END": 0x020CBBFC,
    # The init lock word (HW_INIT_LOCK_BUF), reached through a symbol by OS_InitLock
    "data_027ffff0": 0x027FFFF0,
}


def add_absolute_symbols(lcf):
    marker = "SECTIONS {\n"
    if marker not in lcf:
        sys.exit("no SECTIONS block")
    lines = "".join(f"    {name} = {value:#x};\n" for name, value in ABSOLUTE_SYMBOLS.items())
    return lcf.replace(marker, marker + lines, 1)


def sourced_functions(config_yaml):
    """Function names of every delinks.txt unit whose source file exists."""
    names = []
    for delinks in glob.glob(os.path.join(os.path.dirname(config_yaml), "**", "delinks.txt"),
                             recursive=True):
        folder = os.path.dirname(delinks)
        funcs = []
        with open(os.path.join(folder, "symbols.txt"), encoding="utf-8") as f:
            for line in f:
                m = FUNC.match(line)
                if m:
                    funcs.append((int(m[2], 16), m[1]))
        with open(delinks, encoding="utf-8") as f:
            # universal newlines: the text already uses "\n"
            blocks = f.read().split("\n\n")[1:]
        for block in blocks:
            path = block.strip().split("\n")[0].rstrip(":")
            if not os.path.isfile(os.path.join(ROOT, path)):
                continue
            for lo, hi in re.findall(r"\.text\s+start:(0x[0-9a-f]+) end:(0x[0-9a-f]+)", block):
                lo, hi = int(lo, 16), int(hi, 16)
                names += [n for a, n in funcs if lo <= a < hi]
    return names


def add_force_active(lcf, names):
    if not names:
        return lcf
    # comma-separated, like KEEP_SECTION (mwldarm rejects a list without commas)
    block = "FORCE_ACTIVE {\n" + ",\n".join(f"    {n}" for n in names) + "\n}\n\n"
    return lcf.replace("SECTIONS {", block + "SECTIONS {", 1)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    lcf_path = sys.argv[1]
    res = subprocess.run(sys.argv[2:])
    if res.returncode != 0:
        return res.returncode

    config = sys.argv[sys.argv.index("-c") + 1] if "-c" in sys.argv else None
    with open(lcf_path, encoding="utf-8") as f:
        lcf = add_absolute_symbols(f.read())
    if config:
        lcf = add_force_active(lcf, sourced_functions(config))
    if f"({KEEP})" not in lcf:
        with open(lcf_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(lcf)
        return 0
    head, sep, rest = lcf.partition("KEEP_SECTION {")
    if not sep:
        sys.exit(f"{lcf_path}: no KEEP_SECTION block")
    body, end, tail = rest.partition("}")
    names = [name.strip() for name in body.split(",") if name.strip()]
    if KEEP not in names:
        names.append(KEEP)
    body = "\n" + ",\n".join(f"    {name}" for name in names) + "\n"
    with open(lcf_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + sep + body + end + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
