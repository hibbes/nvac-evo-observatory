# nvac-evo-observatory

Continuous, read-only observation of the **NVAC / MCP79** (GeForce 9400M, Tesla
nv50) **EVO display state machine** on a 24/7 machine, to characterise and
ultimately find the trigger condition of the **base-channel fetch-park wedge**
(the stock MCP79 base bug: Debian #976788, RedHat #1898103).

## The bug, in one paragraph

On this integrated GPU the EVO **base** display channel (the DMA pushbuffer that
feeds the primary scanout) intermittently latches into a wedged fetch state:
the hardware GET pointer parks, the channel control register (`0x610220` for
base head-1) latches bit31, and every subsequent `nv50_dmac_wait()` then times
out with `nouveau ... drm: base-1: timeout`. It happens at boot on a large
fraction of cold starts, and occasionally at runtime. Once parked, the channel
never recovers on its own.

## What we already established (one-shot experiments, see analysis/)

A runtime recovery knob (`/sys/module/nouveau/parameters/unwedge`, a local
nouveau patch) that does a full **PMC PDISPLAY power-cycle** (NV_PMC_ENABLE
register `0x000200` bit30) plus the VBIOS `init_io` 0x69 clock replay
(`0x614100`/`0x614900` VPLLs, `0x00e18c` clock gate) was built and tested live:

- It **clocks the engine back up** (post-reset `0x611014` dispatch fabric reads
  a sane value, no MMIO fault storm), which a bare bit30 toggle did not.
- But it **does not raze the base-fetch park**: `0x610220` keeps bit31 set and
  `base-1: timeout` continues. The park sits **deeper than the PDISPLAY clock
  domain**. It is, so far, only cleared by a real cold-start re-POST.

So the open question is no longer "can we reset it" but **"what exactly puts the
base channel into this state, and is there any narrower reset that clears it."**
That needs data, not more guesses, hence this observatory.

## Method

`sampler.py` mmaps PCI BAR0 **read-only** (register reads on the EVO
control/status/pointer registers have no side effects, so this is safe next to
the live nouveau driver) and:

- logs a baseline row of the key registers every 5 s to `data/samples-YYYY-MM-DD.csv`,
- on detecting a wedge (control-register **bit31 latched** for >= 2 samples, the
  proven park signature, or the GET pointer frozen behind PUT for >= 3 samples),
  captures a dense ~4 s burst of the full register set plus the dmesg context
  into `events/wedge-<timestamp>.txt`, once per wedge episode.

It runs as an OpenRC service (`nvac-evo-sampler`) so it is up across reboots and
catches the boot-time wedges.

## Register map sampled

| offset     | name        | meaning                                            |
|------------|-------------|----------------------------------------------------|
| `0x000200` | pmc_enable  | NV_PMC_ENABLE (bit30 = PDISPLAY power)             |
| `0x610200` | core_ctrl   | EVO core channel control/status (chid 0)          |
| `0x610210` | base0_ctrl  | EVO base channel control, head 0 (chid 1)         |
| `0x610220` | base1_ctrl  | EVO base channel control, head 1 (chid 2); **the park latches bit31 here** |
| `0x610020/24` | intr_en  | disp interrupt enables                             |
| `0x610028/2c` | super    | EVO supervisor control                             |
| `0x611014 ...` | disp61.. | display dispatch fabric (faulted in the unclocked v1 failure) |
| `0x640000/04` | core put/get | core pushbuffer pointers                       |
| `0x641000/04` | base0 put/get | base head-0 pushbuffer pointers               |
| `0x642000/04` | base1 put/get | base head-1 pushbuffer pointers               |

Healthy reference (display up, idle): `core_ctrl=2d0b001b`, `base1_ctrl=0c05001b`
(bit31 clear), `pmc_enable=dff3d113`, `disp611014=00824414`.
Wedged reference (from the live v3 test): `base1_ctrl=c202001b` (bit31 set),
`base-1: timeout`.

## VBIOS

`vbios.rom` (58880 bytes, valid 0x55aa, Apple K88 v62.79) is the board VBIOS,
extracted from the PCI ROM. Its devinit display bring-up (`init_io` opcode 0x69
NV50 branch, the per-encoder script[0] at 0xc6da and callees) is the reference
for what a cold POST does to the display engine that a runtime reset cannot.

## Layout

- `sampler.py`    the read-only sampler
- `data/`         daily baseline CSVs (gitignored locally, summarised on commit)
- `events/`       per-episode wedge captures (the payload)
- `analysis/`     notes, correlations, hypotheses
- `vbios.rom`     board VBIOS reference

No personal data is recorded, only GPU register values.
