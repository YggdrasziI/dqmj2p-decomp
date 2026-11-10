#!/usr/bin/env python3
"""Minimal GDB remote client for an emulator's ARM9 GDB stub (dynamic analysis).

Talks the GDB remote serial protocol over TCP (melonDS: enable the GDB stub in the
emulator settings; ARM9 port 3333 by default). Nothing here changes the build: it
reads memory and registers, and counts which functions of symbols.txt run.

Usage:
  python tools/gdb_trace.py ping
  python tools/gdb_trace.py read 0x020c4800 0x40
  python tools/gdb_trace.py calls --match "func_0200" --limit 64 --seconds 10
  python tools/gdb_trace.py calls --addr 0x0207481c --addr 0x02074728 --seconds 30

`calls` puts a breakpoint on each selected function, lets the game run for the
given time, counts the hits (each hit is stepped over, then the breakpoint is put
back), then removes every breakpoint and lets the game go on. Keep the set small:
each hit stops the emulator.

Overlay functions (`func_ov<NNN>_...`) can be selected too, but all overlays share
one address range: a breakpoint on an overlay address fires for whichever overlay is
loaded there at that moment, so a hit only counts for the named overlay when that
overlay is the resident one.
"""

import argparse
import glob
import os
import re
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOL_FILES = [
    os.path.join(ROOT, "config", "jpn", "arm9", "symbols.txt"),
    os.path.join(ROOT, "config", "jpn", "arm9", "itcm", "symbols.txt"),
    os.path.join(ROOT, "config", "jpn", "arm9", "dtcm", "symbols.txt"),
    *sorted(glob.glob(os.path.join(ROOT, "config", "jpn", "arm9", "overlays", "ov*", "symbols.txt"))),
]
SYMBOL_RE = re.compile(r"^(\S+) kind:function\((arm|thumb)[^)]*\) addr:(0x[0-9a-fA-F]+)")


class Gdb:
    def __init__(self, host, port, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    def _recv(self):
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("stub closed the connection")
        self.buf += data

    def packet(self, timeout=None):
        """Read one packet; acknowledge it; return its payload."""
        old = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                start = self.buf.find(b"$")
                if start >= 0:
                    end = self.buf.find(b"#", start)
                    if end >= 0 and len(self.buf) >= end + 3:
                        payload = self.buf[start + 1:end]
                        self.buf = self.buf[end + 3:]
                        self.sock.sendall(b"+")
                        return payload.decode("latin-1")
                self._recv()
        finally:
            self.sock.settimeout(old)

    def send(self, cmd):
        data = cmd.encode("latin-1")
        frame = b"$" + data + b"#" + b"%02x" % (sum(data) & 0xFF)
        self.sock.sendall(frame)

    def command(self, cmd):
        self.send(cmd)
        return self.packet()

    def interrupt(self):
        self.sock.sendall(b"\x03")
        return self.packet()

    def read_memory(self, addr, size):
        reply = self.command(f"m{addr:x},{size:x}")
        if reply.startswith("E"):
            raise RuntimeError(f"read {addr:#x}: {reply}")
        return bytes.fromhex(reply)

    def registers(self):
        reply = self.command("g")
        words = [reply[i:i + 8] for i in range(0, 16 * 8, 8)]
        return [int.from_bytes(bytes.fromhex(w), "little") for w in words]

    def set_break(self, addr, thumb):
        return self.command(f"Z0,{addr:x},{2 if thumb else 4}") == "OK"

    def clear_break(self, addr, thumb):
        return self.command(f"z0,{addr:x},{2 if thumb else 4}") == "OK"


def load_symbols():
    syms = []
    for path in SYMBOL_FILES:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = SYMBOL_RE.match(line)
                if m:
                    syms.append((int(m.group(3), 16), m.group(1), m.group(2) == "thumb"))
    return syms


def hexdump(addr, data):
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        print(f"{addr + i:08x}  {chunk.hex(' ')}")


def cmd_ping(gdb, args):
    print("stop reply:", gdb.command("?"))
    regs = gdb.registers()
    print(" ".join(f"r{i}={v:08x}" for i, v in enumerate(regs)))
    gdb.send("c")


def cmd_read(gdb, args):
    print("stop reply:", gdb.command("?"))
    hexdump(args.addr, gdb.read_memory(args.addr, args.size))
    gdb.send("c")


def cmd_calls(gdb, args):
    syms = load_symbols()
    # Overlays share addresses: keep only the selected symbols, one per address
    by_addr = {}
    for a in args.addr:
        named = [(n, t) for sa, n, t in syms if sa == a]
        if not named:
            sys.exit(f"{a:#x}: no function symbol at this address")
        if len(named) > 1:
            sys.exit(f"{a:#x}: several overlays have a function here "
                     f"({', '.join(n for n, _ in named)}); select one with --match")
        by_addr[a] = named[0]
    if args.match:
        for a, n, t in syms:
            if re.search(args.match, n):
                if a in by_addr and by_addr[a][0] != n:
                    sys.exit(f"{a:#x}: {by_addr[a][0]} and {n} share this address; narrow --match")
                by_addr[a] = (n, t)
    chosen = list(by_addr)
    if not chosen:
        sys.exit("no function selected (--addr or --match)")
    if len(chosen) > args.limit:
        sys.exit(f"{len(chosen)} functions selected, more than --limit {args.limit}")

    print("stop reply:", gdb.command("?"))
    armed = []
    for a in chosen:
        if gdb.set_break(a, by_addr[a][1]):
            armed.append(a)
        else:
            print(f"breakpoint refused at {a:#x} {by_addr[a][0]}")
    print(f"{len(armed)} breakpoints set; running for {args.seconds} s")

    hits = {a: 0 for a in armed}
    callers = {a: set() for a in armed}
    end = time.time() + args.seconds
    gdb.send("c")
    while True:
        left = end - time.time()
        if left <= 0:
            gdb.interrupt()
            break
        try:
            reply = gdb.packet(timeout=left)
        except socket.timeout:
            gdb.interrupt()
            break
        if not reply.startswith(("S", "T")):
            print("unexpected reply:", reply)
            continue
        regs = gdb.registers()
        pc = regs[15] & ~1
        if pc in hits:
            hits[pc] += 1
            callers[pc].add(regs[14] & ~1)
            thumb = by_addr[pc][1]
            gdb.clear_break(pc, thumb)
            gdb.command("s")
            gdb.set_break(pc, thumb)
        gdb.send("c")

    for a in armed:
        gdb.clear_break(a, by_addr[a][1])
    gdb.send("c")

    print(f"{'hits':>8}  {'address':10}  name  (return addresses)")
    for a in sorted(armed, key=lambda a: -hits[a]):
        lrs = " ".join(f"{x:08x}" for x in sorted(callers[a])[:6])
        print(f"{hits[a]:8}  {a:#010x}  {by_addr[a][0]}  ({lrs})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3333)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    r = sub.add_parser("read")
    r.add_argument("addr", type=lambda s: int(s, 0))
    r.add_argument("size", type=lambda s: int(s, 0))
    c = sub.add_parser("calls")
    c.add_argument("--addr", type=lambda s: int(s, 0), action="append", default=[])
    c.add_argument("--match", help="regex on symbol names")
    c.add_argument("--limit", type=int, default=64)
    c.add_argument("--seconds", type=float, default=10)
    args = p.parse_args()

    gdb = Gdb(args.host, args.port, timeout=10)
    {"ping": cmd_ping, "read": cmd_read, "calls": cmd_calls}[args.cmd](gdb, args)


if __name__ == "__main__":
    main()
