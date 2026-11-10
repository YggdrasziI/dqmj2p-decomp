#!/usr/bin/env python3
"""List small functions of the game code regions that are not delinked yet.

ARM mwccarm units have no __sinit end marker (static initializers live in .init), so
this lists functions instead of units: for each function of a game region not
covered by a file in delinks.txt, its module, size, the calls it makes and its callers
(from the dsd relocs.txt of every module; direct calls only, calls through vtables or
function pointers are not counted). Call-tree leaves (no calls) come first, then by
size. A function whose callers are all close to it most likely shares their unit.

Overlays share one address range, so a call is attributed to the module named by its
relocation (`module:overlay(N)`); a call whose target may be in several overlays
(`module:overlays(...)`) counts for each of them.

Usage:
  python tools/unit_candidates.py              # 30 smallest leaves of every game region
  python tools/unit_candidates.py -n 50 --max-calls 2
  python tools/unit_candidates.py --module ov001
  python tools/unit_candidates.py --module main --start 0x02040000 --end 0x02050000
"""

import argparse
import bisect
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Game code regions per module (hypotheses, notes/SPLITS.md and notes/COMPILER.md).
# Not mapped yet for this ROM: every module's whole .text is a candidate, except ov004
# (DS Protect, left out). Narrow these once the region map exists (ROADMAP Step 5).
GAME_REGIONS = {
    "main": None,
    "ov000": None,
    "ov001": None,
    "ov002": None,
    "ov003": None,
    "ov005": None,
    "ov006": None,
    "ov007": None,
}


def module_dir(cfg, module):
    if module == "main":
        return cfg
    if module.startswith("ov"):
        return os.path.join(cfg, "overlays", module)
    return os.path.join(cfg, module)


def read_symbols(path):
    funcs = []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"(\S+) kind:function\((arm|thumb),size=(0x[0-9a-f]+)\S*\) addr:(0x[0-9a-f]+)", line)
        if m:
            funcs.append((int(m.group(4), 16), int(m.group(3), 16), m.group(1), m.group(2)))
    return sorted(funcs)


def target_modules(field):
    """Module names a relocation's `module:` field may point to."""
    if field.startswith("overlay("):
        return [f"ov{int(field[8:-1]):03d}"]
    if field.startswith("overlays("):
        return [f"ov{int(n):03d}" for n in field[9:-1].split(",")]
    return [field]


def read_calls(path):
    """[(from, to, target modules)] of the direct calls of one relocs.txt."""
    calls = []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"from:(0x[0-9a-f]+) kind:(arm_call|thumb_call)\S* to:(0x[0-9a-f]+) module:(\S+)", line)
        if m:
            calls.append((int(m.group(1), 16), int(m.group(3), 16), target_modules(m.group(4))))
    return calls


def read_text_range(path):
    """[start, end) of the module's .text section (header of delinks.txt)."""
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            break
        m = re.match(r"\s+\.text\s+start:(0x[0-9a-f]+) end:(0x[0-9a-f]+)", line)
        if m:
            return int(m.group(1), 16), int(m.group(2), 16)
    return None


def read_delinked(path):
    """[start, end) ranges of every .text section of a file block in delinks.txt."""
    ranges, in_files = [], False
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            in_files = True
            continue
        m = re.match(r"\s+\.text\s+start:(0x[0-9a-f]+) end:(0x[0-9a-f]+)", line)
        if in_files and m:
            ranges.append((int(m.group(1), 16), int(m.group(2), 16)))
    return ranges


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="jpn")
    parser.add_argument("--module", help="main, itcm, ovNNN (default: every game region)")
    parser.add_argument("-n", type=int, default=30, help="rows to print")
    parser.add_argument("--max-calls", type=int, default=0, help="most outgoing calls (default 0: leaves)")
    parser.add_argument("--min-size", type=lambda x: int(x, 0), default=8,
                        help="smallest size (default 8: skips 4-byte 'bx lr' stubs)")
    parser.add_argument("--start", type=lambda x: int(x, 0),
                        help="with --end and --module: one range instead of the game regions")
    parser.add_argument("--end", type=lambda x: int(x, 0))
    args = parser.parse_args()

    cfg = os.path.join(ROOT, "config", args.version, "arm9")
    if args.module:
        modules = {args.module: [(args.start, args.end)] if args.start is not None and args.end is not None
                   else GAME_REGIONS.get(args.module)}
    elif args.start is not None:
        parser.error("--start/--end need --module")
    else:
        modules = GAME_REGIONS

    # Calls of every module, keyed by the module they come from and the one they reach
    all_calls = []
    for path in [os.path.join(cfg, "relocs.txt"), os.path.join(cfg, "itcm", "relocs.txt"),
                 *sorted(glob.glob(os.path.join(cfg, "overlays", "ov*", "relocs.txt")))]:
        src = os.path.basename(os.path.dirname(path)) if "overlays" in path or "itcm" in path else "main"
        all_calls += [(src, a, b, mods) for a, b, mods in read_calls(path)]

    rows = []
    for module, regions in modules.items():
        mdir = module_dir(cfg, module)
        if regions is None:
            regions = [read_text_range(os.path.join(mdir, "delinks.txt"))]
        funcs = read_symbols(os.path.join(mdir, "symbols.txt"))
        delinked = read_delinked(os.path.join(mdir, "delinks.txt"))
        starts = [f[0] for f in funcs]
        out_calls, callers = {}, {}
        for src, a, b, mods in all_calls:
            if src == module:
                i = bisect.bisect_right(starts, a) - 1
                if i >= 0 and a < funcs[i][0] + funcs[i][1]:
                    out_calls[funcs[i][0]] = out_calls.get(funcs[i][0], 0) + 1
            if module in mods:
                callers.setdefault(b, []).append((src, a))
        for addr, size, name, mode in funcs:
            if not any(s <= addr < e for s, e in regions) or size < args.min_size:
                continue
            if any(s <= addr < e for s, e in delinked):
                continue
            n_calls = out_calls.get(addr, 0)
            if n_calls > args.max_calls:
                continue
            who = callers.get(addr, [])
            near = sum(1 for src, c in who if src == module and abs(c - addr) < 0x400)
            rows.append((n_calls, size, module, addr, name, mode, len(who), near))
    rows.sort()
    print(f"{'module':<6} {'address':<11} {'size':>6} {'mode':<5} {'calls':>5} {'callers':>7} {'near':>4}  name")
    for n_calls, size, module, addr, name, mode, n_callers, near in rows[:args.n]:
        print(f"{module:<6} 0x{addr:08X} {size:>#6x} {mode:<5} {n_calls:>5} {n_callers:>7} {near:>4}  {name}")
    print(f"{len(rows)} functions (calls <= {args.max_calls}) in {', '.join(modules)}, "
          f"not delinked; 'near' = callers in the same module within 0x400 bytes")


if __name__ == "__main__":
    main()
