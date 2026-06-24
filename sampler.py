#!/usr/bin/env python3
# nvac-evo-observatory: continuous read-only sampler of the NVAC / MCP79
# (GeForce 9400M, Tesla nv50) EVO display state machine, to characterise the
# base-channel fetch-park wedge (the stock MCP79 base bug, Debian #976788).
#
# Reads GPU registers via a READ-ONLY mmap of PCI BAR0. Register reads have no
# side effects on the EVO control/status/pointer registers sampled here, so this
# is safe to run alongside the live nouveau driver.
#
# Baseline: every INTERVAL seconds it logs the key registers to a daily CSV.
# On detecting a channel wedge (PUT advanced, GET frozen behind it for K
# samples, i.e. the channel stopped draining), it captures a dense burst
# snapshot + the dmesg context into events/, once per wedge episode. The next
# clean boot / sample resets the episode.
import mmap, os, struct, time, signal, subprocess, sys

PCI = "/sys/bus/pci/devices/0000:02:00.0/resource0"
ROOT = os.path.dirname(os.path.abspath(__file__))
DATADIR = os.path.join(ROOT, "data")
EVENTDIR = os.path.join(ROOT, "events")
INTERVAL = 5.0          # baseline sampling period (seconds)
STUCK_K = 3             # consecutive frozen-GET samples => wedge

# Register map (offset, short name). Order defines the CSV column order.
REGS = [
    (0x000200, "pmc_enable"),
    (0x610200, "core_ctrl"), (0x610210, "base0_ctrl"), (0x610220, "base1_ctrl"),
    (0x610020, "intr_en0"), (0x610024, "intr_en1"),
    (0x610028, "super0"), (0x61002c, "super1"),
    (0x611014, "disp611014"), (0x61102c, "disp61102c"), (0x611034, "disp611034"),
    (0x61103c, "disp61103c"), (0x611044, "disp611044"),
    (0x640000, "core_put"), (0x640004, "core_get"),
    (0x641000, "base0_put"), (0x641004, "base0_get"),
    (0x642000, "base1_put"), (0x642004, "base1_get"),
]
# Channels watched for the fetch-park: (ctrl, put, get, name)
CHANS = [
    (0x610200, 0x640000, 0x640004, "core"),
    (0x610210, 0x641000, 0x641004, "base0"),
    (0x610220, 0x642000, 0x642004, "base1"),
]

running = True
def _stop(*_):
    global running
    running = False
signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

def main():
    os.makedirs(DATADIR, exist_ok=True)
    os.makedirs(EVENTDIR, exist_ok=True)
    fd = os.open(PCI, os.O_RDONLY)
    n = min(os.fstat(fd).st_size, 0x800000)
    m = mmap.mmap(fd, n, mmap.MAP_SHARED, mmap.PROT_READ)
    rd = lambda o: struct.unpack_from("<I", m, o)[0]

    header = "ts," + ",".join(name for _, name in REGS)
    stuck = {c[3]: 0 for c in CHANS}
    bit31n = {c[3]: 0 for c in CHANS}
    last_get = {c[3]: None for c in CHANS}
    in_wedge = False
    cur_day = None
    f = None

    while running:
        day = time.strftime("%Y-%m-%d")
        if day != cur_day:
            if f:
                f.close()
            cur_day = day
            path = os.path.join(DATADIR, "samples-%s.csv" % day)
            new = not os.path.exists(path)
            f = open(path, "a")
            if new:
                f.write(header + "\n")

        vals = [rd(o) for o, _ in REGS]
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        f.write(ts + "," + ",".join("%08x" % v for v in vals) + "\n")
        f.flush()

        wedged = []
        for ctrl, put, get, name in CHANS:
            c, p, g = rd(ctrl), rd(put), rd(get)
            bit31 = (c & 0x80000000) != 0
            bit31n[name] = bit31n[name] + 1 if bit31 else 0
            if p != g and g == last_get[name]:
                stuck[name] += 1
            else:
                stuck[name] = 0
            last_get[name] = g
            # wedge = the fetch-park ctrl-bit31 latched for >=2 samples (the
            # proven base-park signature), or GET frozen behind PUT for >=K.
            if bit31n[name] >= 2 or stuck[name] >= STUCK_K:
                wedged.append((name, c, p, g, bit31, stuck[name]))

        if wedged and not in_wedge:
            in_wedge = True
            capture_event(rd, wedged, ts)
        elif not wedged:
            in_wedge = False

        time.sleep(INTERVAL)

    if f:
        f.close()

def capture_event(rd, wedged, ts):
    safe = ts.replace(":", "").replace("-", "")
    path = os.path.join(EVENTDIR, "wedge-%s.txt" % safe)
    try:
        dmesg = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5).stdout
        lines = dmesg.splitlines()
        ctx = "\n".join(l for l in lines
                        if any(k in l for k in ("nv50_dmac_wait", "base-1: timeout",
                                                "MMIO read", "nv50_bus_intr", "notifier timeout")))[-4000:]
        full = "\n".join(lines[-300:])   # full tail: do not lose the compositor/modeset context
    except Exception as e:
        ctx = full = "(dmesg failed: %s)" % e
    with open(path, "w") as e:
        e.write("# WEDGE episode begin %s\n" % ts)
        e.write("# wedged channels (PUT advanced, GET frozen >= %d samples):\n" % STUCK_K)
        for name, c, p, g, bit31, st in wedged:
            e.write("#   %-5s ctrl=%08x put=%08x get=%08x bit31=%s stuck=%d\n"
                    % (name, c, p, g, bit31, st))
        e.write("\n# dense burst (full register set, ~100ms cadence):\n")
        for i in range(40):
            line = " ".join("%s=%08x" % (name, rd(o)) for o, name in REGS)
            e.write("+%.1fs %s\n" % (i * 0.1, line))
            time.sleep(0.1)
        e.write("\n# dmesg wedge keyword lines:\n")
        e.write(ctx + "\n")
        e.write("\n# dmesg full tail (last 300 lines, for compositor/modeset correlation):\n")
        e.write(full + "\n")

if __name__ == "__main__":
    main()
