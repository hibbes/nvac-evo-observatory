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
import mmap, os, re, struct, time, signal, subprocess, sys

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
    # v2: relabelled per the wedge analysis (the old super0/super1/intr_en names were wrong)
    (0x610020, "intr0"), (0x610024, "intr1"),          # live DISP INTR status (latched, w1c)
    (0x610028, "chan_awaken_en"),                       # per-channel awaken-IRQ enable
    (0x61002c, "intr_en"),                              # DISP INTR enable; bit3 = head1 vblank
    (0x610030, "supervisor"),                           # NEW: real EVO supervisor state register
    (0x610080, "core_err_m"), (0x610084, "core_err_d"),   # NEW: core(chid0) error latch method/data
    (0x610090, "base1_err_m"), (0x610094, "base1_err_d"), # NEW: base1(chid2) error latch (0x610080+chid*8)
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
    frozen = {c[3]: 0 for c in CHANS}   # GET unchanged for N samples, regardless of PUT
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
            frozen[name] = frozen[name] + 1 if g == last_get[name] else 0
            if p != g and g == last_get[name]:
                stuck[name] += 1
            else:
                stuck[name] = 0
            last_get[name] = g
            # wedge = ctrl-bit31 latched AND the GET pointer frozen, both for >=2
            # samples. Requiring frozen GET (not bit31 alone) covers both the
            # GET-behind-PUT park AND the drained-but-latched case (06-28), while
            # EXCLUDING gr-engine-fault false-positives where bit31 blips but GET
            # keeps advancing (the channel is live -- 2/5 old events were this).
            if bit31n[name] >= 2 and frozen[name] >= 2:
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
    # Keyword set INCLUDES the disp-trap line (ERROR/mthd/chid/nv50_disp_intr_error):
    # its presence/absence is THE method-error-vs-fetch-park discriminator and must
    # never be lost to the 300-line tail scrolling off (the bug that risked
    # mis-decoding a real method-error episode as a fetch-park).
    KW = ("nv50_dmac_wait", "base-1: timeout", "base-0: timeout", "core: timeout",
          "MMIO read", "nv50_bus_intr", "notifier timeout",
          "ERROR", "mthd", "chid", "nv50_disp_intr_error")
    # Drop early-boot cert/IMA noise from the full tail: the X.509 module-signing
    # key FINGERPRINT (public, but gitleaks false-positives it as a generic-api-key
    # and blocks the auto-push to the public repo). Irrelevant to the EVO wedge.
    NOISE = ("X.509", "Signing Key", "ima:", "certificate", "blacklist")
    # Scrub host/device identifiers before the dmesg tail lands in the PUBLIC repo:
    # MAC/BSSID addresses, USB SerialNumbers and the login username are irrelevant
    # to the EVO wedge but are personally / hardware identifying. Mask in place so
    # the USB-enumeration context (e.g. the DHT22/NodeMCU reset episodes) survives.
    def scrub(line):
        line = re.sub(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", "xx:xx:xx:xx:xx:xx", line)
        line = re.sub(r"(SerialNumber:\s*)\S+", r"\1[redacted]", line)
        line = re.sub(r"(of user )\S+", r"\1[redacted]", line)
        return line
    def grab():
        try:
            ls = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5).stdout.splitlines()
            ctx = "\n".join(scrub(l) for l in ls if any(k in l for k in KW))[-12000:]
            full = "\n".join(scrub(l) for l in ls[-300:] if not any(n in l for n in NOISE))
            return (ctx, full)
        except Exception as ex:
            return ("(dmesg failed: %s)" % ex, "")
    ctx, full = grab()
    with open(path, "w") as e:
        e.write("# WEDGE episode begin %s\n" % ts)
        e.write("# wedged channels (bit31 latched AND GET frozen >= 2 samples):\n")
        for name, c, p, g, bit31, st in wedged:
            e.write("#   %-5s ctrl=%08x put=%08x get=%08x bit31=%s stuck=%d\n"
                    % (name, c, p, g, bit31, st))
        e.write("\n# dense burst (full register set, ~100ms cadence):\n")
        for i in range(40):
            line = " ".join("%s=%08x" % (name, rd(o)) for o, name in REGS)
            e.write("+%.1fs %s\n" % (i * 0.1, line))
            time.sleep(0.1)
        ctx2, _ = grab()   # bracket the window: a disp trap predates detection, re-grab after the burst too
        e.write("\n# dmesg trap/timeout lines AT DETECTION:\n")
        e.write(ctx + "\n")
        e.write("\n# dmesg trap/timeout lines AFTER burst (bracket):\n")
        e.write(ctx2 + "\n")
        e.write("\n# dmesg full tail (last 300 lines, for compositor/modeset correlation):\n")
        e.write(full + "\n")

if __name__ == "__main__":
    main()
