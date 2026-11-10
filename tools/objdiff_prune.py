#!/usr/bin/env python3
"""Run `dsd objdiff`, then drop the base object of units that have no source file.

Library files are delinked (their bounds and sections in delinks.txt) before anyone
writes their source. objdiff fails on a missing base object, so such a unit keeps
only its target: the report counts it as not decompiled.

Usage (from the ninja objdiff rule):
  python tools/objdiff_prune.py <dsd command and arguments...>
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "objdiff.json")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    res = subprocess.run(sys.argv[1:])
    if res.returncode != 0:
        return res.returncode

    with open(CONFIG, encoding="utf-8") as f:
        config = json.load(f)
    for unit in config["units"]:
        source = unit.get("metadata", {}).get("source_path")
        if "base_path" in unit and source and not os.path.exists(os.path.join(ROOT, source)):
            del unit["base_path"]
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
