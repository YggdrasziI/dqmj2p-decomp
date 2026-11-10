#!/usr/bin/env python3
"""Compare the objdiff report with a saved baseline and list what changed.

Used as the no-regression gate before a commit.

Usage:
  python tools/report_diff.py --save            # save build/jpn/report.json as the baseline
  python tools/report_diff.py                   # compare the current report with the baseline
  python tools/report_diff.py --base other.json # compare with another report
  python tools/report_diff.py --version jpn     # game version (default jpn)

Exit status 1 when:
  - a function or data section present in both reports lost match percentage;
  - a function or data section with a non-zero match disappeared and no added item
    of the same size in the same unit (a rename) matches at least as well; when the
    unit itself is gone (a renamed file), an added item of the same size anywhere;
  - a total (matched code, complete code, matched data, matched functions) went down.
"""

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_VERSION = "jpn"
MEASURES = ("matched_code", "complete_code", "matched_data", "matched_functions")


def load(path):
    """(measures, [(key, item)]); an item is {pct, unit, size, addr, kind}."""
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    items = []

    def put(key, item):
        items.append((key, item))

    for unit in report["units"]:
        name = unit["name"]
        # Functions are keyed by symbol name, so a function that moves from a dsd
        # gap (_dsd_gap@main_5, renumbered whenever a file is delinked) to a source
        # file is compared, not removed and re-added. Gap functions have no score (0).
        for func in unit.get("functions", []):
            put(("fn", func["name"]), {
                "pct": func.get("fuzzy_match_percent", 0.0), "unit": name, "kind": "fn",
                "size": int(func.get("size", 0)), "addr": func.get("address")})
        # Data sections of gaps are not diffed, and their names repeat across gaps.
        if name.startswith("_dsd_gap@"):
            continue
        for section in unit.get("sections", []):
            if section["name"].startswith(".text"):
                continue
            put(("data", f"{name} {section['name']}"), {
                "pct": section.get("fuzzy_match_percent", 0.0), "unit": name, "kind": "data",
                "size": int(section.get("size", 0)), "addr": section["name"]})
    return report["measures"], items


def keyed(items, repeated):
    """{key: item}. Names are unique in a dsd project; a name repeated in either
    report (two static functions with one name) is keyed by unit in both reports,
    so that neither hides the other and both sides use the same keys."""
    out = {}
    for (kind, name), item in items:
        key = (kind, f"{item['unit']}:{name}") if (kind, name) in repeated else (kind, name)
        if key in out:
            sys.exit(f"{name} repeated in unit {item['unit']}")
        out[key] = item
    return out


def repeats(items):
    seen, rep = set(), set()
    for key, _ in items:
        (rep if key in seen else seen).add(key)
    return rep


def pair_renames(removed, added, cur_units):
    """Pair each removed item with an added one that looks like its new name.

    A candidate has the same kind and size and sits in the same unit (the same
    offset or section first). When the removed item's unit no longer exists (a
    renamed file), a candidate of another unit is accepted. Returns (pairs,
    unpaired removed)."""
    free = list(added)
    pairs, unpaired = [], []
    for key, item in removed:
        cands = [(k, a) for k, a in free if a["kind"] == item["kind"] and a["size"] == item["size"]]
        same = [(k, a) for k, a in cands if a["unit"] == item["unit"]]
        exact = [(k, a) for k, a in same if a["addr"] == item["addr"]]
        moved = cands if item["unit"] not in cur_units else []
        pick = (exact or same or moved or [None])[0]
        if pick is None:
            unpaired.append((key, item))
        else:
            free.remove(pick)
            pairs.append(((key, item), pick))
    return pairs, unpaired


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=DEFAULT_VERSION, type=str.lower,
                        help="game version (default: %(default)s)")
    parser.add_argument("--save", action="store_true", help="save the current report as the baseline")
    parser.add_argument("--base", help="baseline report (default: build/<version>/report_base.json)")
    args = parser.parse_args()
    report = os.path.join(ROOT, "build", args.version, "report.json")
    if args.base is None:
        args.base = os.path.join(ROOT, "build", args.version, "report_base.json")

    if args.save:
        shutil.copyfile(report, args.base)
        print(f"Saved baseline to {args.base}")
        return 0
    if not os.path.exists(args.base):
        print(f"No baseline at {args.base}; run with --save first", file=sys.stderr)
        return 2

    base_measures, base_items = load(args.base)
    cur_measures, cur_items = load(report)
    repeated = repeats(base_items) | repeats(cur_items)
    base, cur = keyed(base_items, repeated), keyed(cur_items, repeated)
    cur_units = {item["unit"] for item in cur.values()}

    def fmt(key, old=None, new=None):
        kind, name = key
        if kind == "data":
            return f"data {name}"
        units = [i["unit"] for i in (old, new) if i]
        where = units[0] if len(set(units)) == 1 else f"{units[0]} -> {units[1]}"
        return f"{where} fn {name}"

    regressions, improvements, removed, added = [], [], [], []
    for key in sorted(set(base) | set(cur)):
        old, new = base.get(key), cur.get(key)
        if old is None:
            added.append((key, new))
        elif new is None:
            removed.append((key, old))
        elif new["pct"] < old["pct"]:
            regressions.append((fmt(key, old, new), old["pct"], new["pct"]))
        elif new["pct"] > old["pct"]:
            improvements.append((fmt(key, old, new), old["pct"], new["pct"]))

    # A renamed item is removed under its old name and added under the new one:
    # compare the two scores. A removed item with a score and no counterpart is lost.
    pairs, unpaired = pair_renames(removed, added, cur_units)
    renamed, lost = [], []
    for (okey, old), (nkey, new) in pairs:
        row = (f"{fmt(okey, old)} -> {nkey[1]}", old["pct"], new["pct"])
        (regressions if new["pct"] < old["pct"] else renamed).append(row)
    for key, old in unpaired:
        if old["pct"] > 0:
            lost.append((fmt(key, old), old["pct"]))
    paired_new = {nkey for _, (nkey, _) in pairs}

    for title, rows in (("Regressions", regressions), ("Improvements", improvements),
                        ("Renamed", renamed)):
        if rows:
            print(f"{title}:")
            for text, old, new in rows:
                print(f"  {text}: {old:.2f} -> {new:.2f}")
    if lost:
        print("Lost (removed with a score, no renamed counterpart):")
        for text, pct in lost:
            print(f"  - {text} ({pct:.2f})")
    others_removed = [(k, i) for k, i in unpaired if i["pct"] == 0]
    others_added = [(k, i) for k, i in added if k not in paired_new]
    if others_removed or others_added:
        print(f"Removed without score: {len(others_removed)}, added: {len(others_added)}")
        for key, item in others_removed:
            print(f"  - {fmt(key, item)}")
        for key, item in others_added:
            print(f"  + {fmt(key, None, item)} ({item['pct']:.2f})")

    dropped = []
    for field in MEASURES:
        old, new = int(base_measures.get(field, 0)), int(cur_measures.get(field, 0))
        if old != new:
            print(f"{field}: {old} -> {new} ({new - old:+d})")
            if new < old:
                dropped.append(field)

    if regressions or lost or dropped:
        print("FAIL: regressions found")
        return 1
    print("OK: no regression")
    return 0


if __name__ == "__main__":
    sys.exit(main())
