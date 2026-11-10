#!/usr/bin/env python3
"""Sync the Ghidra project with the dsd config, without the Ghidra GUI.

dsd-ghidra's SyncDsd.java asks for config.yaml in a file chooser, so it only runs in
the GUI. This script writes a headless copy of the installed script (same code, the
chooser replaced by the first script argument) to a temporary folder and runs it with
Ghidra's analyzeHeadless on the imported ROM. Nothing from dsd-ghidra is stored in
this repository. Needs Ghidra 11.2.1 with the dsd-ghidra v0.7.0 extension installed
(Install Extensions) and the ROM imported once in the GUI as ARM9, without
auto-analysis. The Ghidra install and project folders come from --ghidra and
--project, or from the GHIDRA_INSTALL_DIR and DQMJ2P_GHIDRA_PROJECT environment
variables.

Usage:
  python tools/ghidra_sync.py              # sync config/jpn/arm9/config.yaml
  python tools/ghidra_sync.py --dry-run    # report what would change, change nothing
  python tools/ghidra_sync.py --ghidra <install dir> --project <project dir>
  python tools/ghidra_sync.py --program "<name of the imported ROM in the project>"
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GHIDRA = os.environ.get("GHIDRA_INSTALL_DIR")
DEFAULT_PROJECT = os.environ.get("DQMJ2P_GHIDRA_PROJECT")
PROJECT_NAME = "dqmj2p_jpn"
# Wildcard: the project holds one ROM, whatever name it was imported under (cmd.exe
# breaks analyzeHeadless.bat arguments with parentheses, e.g. "(EU)")
PROGRAM = "*.nds"

CHOOSER = re.compile(
    r"DsdConfigChooser dsdConfigChooser = new DsdConfigChooser\(.*?"
    r"dryRun = dsdConfigChooser\.isDryRun\(\);",
    re.S,
)
HEADLESS = """String[] scriptArgs = getScriptArgs();
        File file = new File(scriptArgs[0]);
        dryRun = scriptArgs.length > 1 && scriptArgs[1].equals("dry");"""


def installed_script(ghidra_dir):
    """SyncDsd.java of the dsd-ghidra extension installed for this Ghidra: user
    extensions live in %APPDATA%/ghidra/<install dir name>/Extensions."""
    appdata = os.environ.get("APPDATA", "")
    install_name = os.path.basename(os.path.normpath(ghidra_dir))
    path = os.path.join(appdata, "ghidra", install_name, "Extensions",
                        "dsd-ghidra", "ghidra_scripts", "SyncDsd.java")
    if not os.path.isfile(path):
        sys.exit(f"dsd-ghidra not installed: {path} not found (install the dsd-ghidra extension)")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="jpn", help="game version (default: %(default)s)")
    parser.add_argument("--ghidra", default=DEFAULT_GHIDRA, help="Ghidra install dir")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Ghidra project dir")
    parser.add_argument("--program", default=PROGRAM,
                        help="program name in the Ghidra project (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="change nothing")
    args = parser.parse_args()
    if not args.ghidra or not args.project:
        parser.error("set --ghidra and --project, or GHIDRA_INSTALL_DIR and DQMJ2P_GHIDRA_PROJECT")

    source = open(installed_script(args.ghidra), encoding="utf-8").read()
    patched, count = CHOOSER.subn(HEADLESS, source)
    if count != 1:
        sys.exit("SyncDsd.java changed: the file chooser block was not found")
    patched = patched.replace("public class SyncDsd ", "public class SyncDsdHeadless ")
    # The chooser's package is not on the temporary script path: drop the import and
    # inline its one constant (the key under which the last config path is saved).
    patched = patched.replace("import dialog.DsdConfigChooser;\n", "")
    patched = patched.replace("DsdConfigChooser.LAST_CONFIG_KEY", '"DsdConfigChooserLastConfig"')
    if "DsdConfigChooser" in patched.replace('"DsdConfigChooserLastConfig"', ""):
        sys.exit("SyncDsd.java changed: another DsdConfigChooser use was found")

    config = os.path.join(ROOT, "config", args.version, "arm9", "config.yaml")
    headless = os.path.join(args.ghidra, "support", "analyzeHeadless.bat")
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "SyncDsdHeadless.java"), "w", encoding="utf-8") as f:
            f.write(patched)
        cmd = [headless, args.project, PROJECT_NAME, "-process", args.program, "-noanalysis",
               "-scriptPath", tmp, "-postScript", "SyncDsdHeadless.java", config]
        if args.dry_run:
            cmd.append("dry")
        out = subprocess.run(cmd, capture_output=True, text=True)
    lines = [line.strip() for line in (out.stdout + out.stderr).splitlines()]
    for line in lines:
        if "SyncDsdHeadless" in line or line.startswith(("ERROR", "INFO  REPORT")):
            print(line)
    # Ghidra log lines start with their level; symbol names may contain "ERROR".
    errors = [line for line in lines if line.startswith("ERROR")]
    programs = [line for line in lines if "REPORT: Processing project file:" in line]
    if len(programs) > 1:
        print(f"WARNING: {len(programs)} programs matched {args.program!r}; each was synced "
              "with this config (use --program)", file=sys.stderr)
    return 0 if out.returncode == 0 and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
