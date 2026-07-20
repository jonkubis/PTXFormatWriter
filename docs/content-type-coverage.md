# `.ptx` content-type coverage tracker

Measured with the **size-driven block walk** (`body_synth._size_driven_blocks`) over the
**282-session local corpus** — 469 distinct real content-types, 562974 block instances.

**231/469 documented in `ptx-format-spec.md` = 49.3%** of types; the 238 undocumented types are
**2.68%** of all block instances (15093 of 562974).

> **Correction (2026-07-20).** This file previously claimed *280/280 = 100.000%* over a
> 26-session corpus. That count came from the older `0x5A`-scan enumeration, which
> **undercounts real sessions** (see spec §3). Re-measuring with the size-driven walk over
> the full corpus surfaces 469 types, not 280 — including ~20 that occur in EVERY session
> yet appear in neither this tracker nor the spec. They are unambiguously real: correct
> `5A` framing, sane declared sizes, a matching content-type word, real parents, and
> several are master-indexed. The format is **not** yet fully documented.

## Undocumented types — the remaining work list

Sorted by how many sessions carry them (ubiquity first), then block count.

| content-type | blocks | in #sessions | indexed | 
|---|---:|---:|---:|
| `0x4807` | 3384 | 282 | 0 |
| `0x1048` | 2539 | 282 | 0 |
| `0x4833` | 1410 | 282 | 0 |
| `0x251c` | 1410 | 282 | 1410 |
| `0x4809` | 1128 | 282 | 0 |
| `0x4824` | 564 | 282 | 0 |
| `0x4828` | 564 | 282 | 564 |
| `0x4826` | 296 | 282 | 0 |
| `0x4808` | 282 | 282 | 282 |
| `0x4820` | 282 | 282 | 282 |
| `0x4823` | 282 | 282 | 282 |
| `0x4829` | 282 | 282 | 282 |
| `0x4834` | 282 | 282 | 282 |
| `0x483b` | 282 | 282 | 282 |
| `0x483d` | 282 | 282 | 0 |
| `0x4841` | 282 | 282 | 282 |
| `0x4501` | 282 | 282 | 282 |
| `0x2701` | 282 | 282 | 282 |
| `0x4702` | 282 | 282 | 282 |
| `0x2567` | 282 | 282 | 282 |
| `0x2583` | 41 | 19 | 41 |
| `0x2585` | 41 | 19 | 0 |
| `0x2582` | 22 | 19 | 0 |
| `0x2584` | 19 | 19 | 19 |
| `0x2586` | 19 | 19 | 19 |
| `0x244d` | 6 | 6 | 0 |
| `0x2421` | 4 | 4 | 0 |
| `0x1654` | 4 | 4 | 0 |
| `0x168f` | 4 | 4 | 0 |
| `0x3182` | 4 | 4 | 0 |
| `0x2a15` | 3 | 3 | 0 |
| `0x7c52` | 3 | 3 | 0 |
| `0x2868` | 3 | 3 | 0 |
| `0x20b9` | 3 | 3 | 0 |
| `0x12c2` | 3 | 3 | 0 |
| `0x3cd2` | 3 | 3 | 0 |
| `0x55ce` | 3 | 3 | 0 |
| `0x4827` | 7 | 2 | 0 |
| `0x302a` | 2 | 2 | 0 |
| `0x2828` | 2 | 2 | 0 |
| `0x2a34` | 2 | 2 | 0 |
| `0x1648` | 2 | 2 | 0 |
| `0x3e4c` | 2 | 2 | 0 |
| `0x8a72` | 2 | 2 | 0 |
| `0x5a81` | 2 | 2 | 0 |
| `0x1683` | 2 | 2 | 0 |
| `0x1e86` | 2 | 2 | 0 |
| `0x248f` | 2 | 2 | 0 |
| `0xa498` | 2 | 2 | 0 |
| `0x12b3` | 2 | 2 | 0 |
| `0x28c9` | 2 | 2 | 0 |
| `0x06e0` | 2 | 2 | 0 |
| `0x2ee6` | 2 | 2 | 0 |
| `0x40ec` | 2 | 2 | 0 |
| `0x2f3f` | 2 | 2 | 0 |
| `0xa94b` | 2 | 2 | 0 |
| `0x697b` | 2 | 2 | 0 |
| `0x2fa8` | 2 | 2 | 0 |
| `0xa423` | 2 | 2 | 0 |
| `0x15f1` | 2 | 2 | 0 |

…and 178 more (long tail, each in <2 sessions).


## Documented types

231 types are covered by `ptx-format-spec.md`, accounting for 97.32% of block instances.

