# `.ptx` content-type coverage tracker

*(The file-header index-pointer record at body offset 20 is excluded — it is not a block; see the spec. Counting it invents one bogus type per session.)*

Measured with the **size-driven block walk** (`body_synth._size_driven_blocks`) over the
**832 sessions across every reachable drive** — 482 distinct real content-types, 7267118 block instances.

| corpus | measured | available |
|---|---:|---:|
| `control_files` | 282 | 282 |
| `/Volumes/Samples` | 250 | 250 |
| `/Volumes/Mac 4TB SSD` | 150 | 819 |
| `/Volumes/Hack 2TB SSD Old` | 150 | 1610 |

`control_files` and `/Volumes/Samples` are measured exhaustively; the two SSDs are
deterministically sampled (`Random(7)`), because they hold thousands of multi-megabyte
sessions. **Read the denominators**: `control_files` is ONE machine and `/Volumes/Samples`
is ONE vendor, and rules confirmed on those two alone have been wrong three times — see
the `0x2015` case study in the spec. 150 sampled sessions from one SSD carry more distinct
content-types than all 282 control files.

Also excluded: ~2 files under `/Volumes/Hack 2TB SSD Old` that are NVIDIA CUDA PTX
assembly, not Pro Tools sessions. Different `.ptx` entirely.

**323/482 content-types have a spec entry = 67.0%**, covering 99.93% of block instances.

## Three standards, not one — mentioned, entered, settled

**305 of the 482 observed content types have their OWN `### ` spec entry.** A further
**18 are counted as "documented" only because their hex string appears somewhere in
another type's prose.** That weaker standard is the one this tracker reported alone until
2026-07-27, and it flatters the result: `0x2000` occurs in **every one of the 832 sessions**,
always as a TOP-LEVEL block, and has no entry at all — it qualified on four incidental
mentions. The mention-only list is the honest work queue:

| type | instances | sessions |
|---|---|---|
| `0x203d` | 430 | 44 |
| `0x2a00` | 38 | 38 |
| `0x0c00` | 34 | 34 |
| `0x2002` | 31 | 31 |
| `0x0800` | 31 | 31 |
| `0xe196` | 31 | 31 |
| `0x1900` | 25 | 25 |
| `0x203c` | 101 | 20 |
| `0x0000` | 67 | 19 |
| `0x2039` | 63 | 15 |
| `0x2631` | 22 | 5 |
| `0x0024` | 2 | 2 |
| `0x0033` | 2 | 2 |
| `0x0034` | 2 | 2 |
| `0x0700` | 1 | 1 |
| `0x2757` | 1 | 1 |
| `0x2557` | 1 | 1 |
| `0x001c` | 1 | 1 |

## Described vs settled — two different numbers

**323 types are DESCRIBED** (framing, parent/child, per-session counts, payload layout
where decodable) and **0 real types are undocumented**.

Of the 192 typed sections in the spec, **50 still say the PURPOSE is unproven** —
`purpose UNKNOWN`, `UNPROVEN`, `INFERRED, not proven`, or `cannot be settled`. That is
the remaining semantic work, and it is deliberately visible: most of those entries name
the Pro Tools experiment that would settle them (typically *one session, saved twice,
with exactly one setting changed*), because no amount of further corpus reading can.

Reporting only the first number would repeat this file's original error in a subtler
form — "documented" is not "understood".

**Read that number carefully.** It means every type observed in this corpus is
*described* — framing, parent/child, per-session counts, and payload layout where
decodable. It does NOT mean every field's *meaning* is proven: a number of spec entries
explicitly say `purpose UNKNOWN` or mark a field `UNPROVEN`. Those are the remaining
semantic work, left deliberately visible — this file once carried a FALSE 100%, so the
fix is to state exactly what was measured and what was not.

> **Correction (2026-07-20).** This file previously claimed *280/280 = 100.000%* over a
> 26-session corpus, enumerated with the older `0x5A`-scan parser that **undercounts real
> sessions**. Re-measured with the size-driven walk over the full corpus: **482** real types.
> 20 of them occur in EVERY session yet were missing from both this tracker and the spec;
> they have since been dissected and documented. A naive walk also reports ~213 extra
> "types" that are really ONE artifact — the file-header index pointer at body offset 20,
> whose per-session value is misread as a content-type (see the spec). It is excluded here.

## Phantoms, excluded from the work list

`0x5A` is `'Z'` and occurs freely inside leaf payloads and binary id fields, so the
size-driven walk frames
noise there. **159 of the 159 undocumented types are phantoms** by one of two measured
signatures: ≥50%% of instances nested inside a leaf payload (`0x1038` plug-in state,
`0x2613`/`0x2615`/`0x2616` view blobs, `0x1003`, `0x1017`), or a type whose low byte is
`0x00` that the master index never references — the `256 × k` arithmetic of a string
LENGTH or first letter being read as a type (`0x1600` = 256×22, `0x0c00` = 256×12).

Reporting them as undocumented would hand the next reader a work list that is mostly
garbage — the same mistake, in the other direction, as the FALSE 100%% this file once
carried.

**0 content-types remain genuinely undocumented.**

## Undocumented types — raw, phantoms included

Sorted by how many sessions carry them (ubiquity first), then block count.

| content-type | blocks | in #sessions | indexed | 
|---|---:|---:|---:|
| `0xb300` | 219 | 47 | 0 |
| `0x2f00` | 197 | 47 | 0 |
| `0x7500` | 62 | 31 | 0 |
| `0x2401` | 62 | 31 | 0 |
| `0x7200` | 31 | 31 | 0 |
| `0x2201` | 31 | 31 | 0 |
| `0x8201` | 31 | 31 | 0 |
| `0x7e00` | 31 | 31 | 0 |
| `0xae00` | 31 | 31 | 0 |
| `0x0807` | 31 | 31 | 0 |
| `0x1008` | 31 | 31 | 0 |
| `0x6208` | 31 | 31 | 0 |
| `0x080a` | 31 | 31 | 0 |
| `0x820a` | 31 | 31 | 0 |
| `0x480b` | 31 | 31 | 0 |
| `0x6808` | 31 | 31 | 0 |
| `0xf20e` | 31 | 31 | 0 |
| `0xb406` | 31 | 31 | 0 |
| `0xf407` | 31 | 31 | 0 |
| `0x5410` | 31 | 31 | 0 |
| `0xe00b` | 31 | 31 | 0 |
| `0xf80a` | 31 | 31 | 0 |
| `0x281a` | 31 | 31 | 0 |
| `0x301b` | 31 | 31 | 0 |
| `0xf01c` | 31 | 31 | 0 |
| `0x381b` | 31 | 31 | 0 |
| `0xde1c` | 31 | 31 | 0 |
| `0x121f` | 31 | 31 | 0 |
| `0x3c20` | 31 | 31 | 0 |
| `0xd01e` | 31 | 31 | 0 |
| `0x0422` | 31 | 31 | 0 |
| `0xdf09` | 31 | 31 | 0 |
| `0x2500` | 31 | 31 | 0 |
| `0xde2e` | 31 | 31 | 0 |
| `0xbe03` | 31 | 31 | 0 |
| `0xc600` | 31 | 31 | 0 |
| `0x8600` | 31 | 31 | 0 |
| `0xa436` | 31 | 31 | 0 |
| `0xc238` | 31 | 31 | 0 |
| `0x6700` | 31 | 31 | 0 |
| `0xa63e` | 31 | 31 | 0 |
| `0xde43` | 31 | 31 | 0 |
| `0xf44b` | 31 | 31 | 0 |
| `0xe451` | 31 | 31 | 0 |
| `0x4608` | 31 | 31 | 0 |
| `0x2d00` | 31 | 31 | 0 |
| `0x6d00` | 31 | 31 | 0 |
| `0x0e00` | 31 | 31 | 0 |
| `0x3e7d` | 31 | 31 | 0 |
| `0xef01` | 31 | 31 | 0 |
| `0x3100` | 31 | 31 | 0 |
| `0x0e07` | 31 | 31 | 0 |
| `0xe09d` | 31 | 31 | 0 |
| `0xf303` | 31 | 31 | 0 |
| `0x7aa9` | 31 | 31 | 0 |
| `0x6abf` | 31 | 31 | 0 |
| `0xb904` | 31 | 31 | 0 |
| `0x7b03` | 31 | 31 | 0 |
| `0xdafa` | 31 | 31 | 0 |
| `0x4b00` | 31 | 31 | 0 |

…and 99 more (long tail, each in <32 sessions).


## Documented types

323 types are covered by `ptx-format-spec.md`, accounting for 99.93% of block instances.

