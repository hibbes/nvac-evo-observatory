# First captured wedge: 2026-06-24 07:18 (runtime, ~35 min uptime)

The sampler caught the first real base-channel wedge in flight.
Raw: `events/wedge-20260624T071803.txt`.

## Transition (5 s baseline)

```
07:17:53  base1_ctrl=4003001b (bit31 clear)   core_ctrl=490a001b   healthy
07:17:58  base1_ctrl=8e06001b (bit31 LATCHED) core_ctrl=8f0e001b   wedge forms
07:18:03  base1_ctrl=8e07001b (canonical)     core_ctrl=8f0e001b   settled
```

The wedge formed within a <= 5 s window. bit31 latches on the CORE and BASE1
channels **simultaneously** (top byte flips 0x49/0x40 -> 0x8f/0x8e, state nibble
changes 0x0a->0x0e and 0x03->0x06->0x07). base0 stays 4003001b (idle, unaffected).

## dmesg ordering (from the event capture)

```
1906.37s  core notifier timeout    <- CORE fails first
1908.81s  base-1: timeout          <- base park follows ~2.4 s later
```

Notable: the CORE channel notifier times out BEFORE base-1. The prior one-shot
work framed this purely as a base-specific fetch park; this first in-flight
capture suggests the core/supervisor path enters the bad state first and the
base park follows. Supervisor state at the wedge: `super0=01ff0000`,
`super1=00000378`. The other dispatch regs were steady
(`disp611014=00824414`, `disp61102c=002800bf`, etc.).

## After the latch

In the 5 s baseline `base1_get` keeps creeping (088c -> 091c -> 0a3c -> ...),
but in the 100 ms dense burst it is frozen (`get` stuck at 08ac while `put`
advances 091c -> 0964 -> 09ac). So at fine timescale the fetch is parked; the
coarse creep is occasional partial drain. `core_get` is fully frozen (02c8) with
`core_put` ahead (0308).

## Open questions for more captures

- Does the core notifier always precede the base timeout?
- What happened just before 07:17:58 (a modeset, an idle-blank wlopm --off/on
  cycle, a pstate reclock)? Correlate with compositor + dmesg next time.
- Is the core+base1 latch always paired, or can base1 park alone?
- Is the trigger the same for boot wedges vs runtime wedges? (need boot captures)
