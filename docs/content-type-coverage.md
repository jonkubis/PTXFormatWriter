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

**331/482 content-types have a spec entry = 68.7%**, covering 99.94% of block instances.

## The strongest form of the coverage claim — measured per INSTANCE

The phantom signatures classify **types**, on a ≥50% rule. But phantom-ness is a property
of an **instance**: a type can be perfectly real *and* be manufactured as noise inside a
leaf payload, and a type-level rule then writes its real instances off with the noise.

That is not hypothetical. The signatures fire on **14 types that have their own spec
entry**, including `0x1000` (226242 instances), `0x1001` (80991) and `0x1033` (80621). Read
type-level, those would be "phantoms"; they are nothing of the kind.

So the same question is asked per instance instead. An instance is structural noise if it
is nested inside a leaf payload, or is the size-8 field under a `0x1014`, or is a count
field at a payload head (signatures 1, 3, 4 — all per-instance). Signature 2 is
irreducibly type-level and zeroes its type outright.

> **302 of the 482 observed types have at least one structurally clean instance,
> and of those, 0 are undocumented.**

The remaining 180 types have **no** clean instance anywhere in 832 sessions — they exist
only inside other blocks' payloads. This is a considerably harder claim than the
type-level one above, and it is the one worth quoting: not *"the leftovers look like
noise"*, but *"every type with even a single unexplained instance is written down"*.

## Three standards, not one — mentioned, entered, settled

**310 of the 482 observed content types have their OWN `### ` spec entry.** A further
**21 are counted as "documented" only because their hex string appears somewhere in
another type's prose.** That weaker standard is the one this tracker reported alone until
2026-07-27, and it flatters the result: `0x2000` occurs in **every one of the 832 sessions**,
always as a TOP-LEVEL block, and has no entry at all — it qualified on four incidental
mentions.

But a mention-only list is not yet a work list. Running the same phantom signatures used
below over it splits the 21 into **21 phantoms** and **0 real types**. Most of the phantoms
are mentioned *because the spec explains them as phantoms* — the sentence deriving
`0x0c00` from 256×12 is the very reason `0x0c00` counts as mentioned. Listing those as
outstanding work is this file's original error wearing a different hat.

**Real types with no entry of their own — the actual queue:**

*(none — every mention-only type is a phantom)*

**Phantoms that were sitting in the queue** (each traced to the byte that produced it in
the spec's phantom catalogue): `0xb300`, `0x2f00`, `0x2a00`, `0x0c00`, `0x2002`, `0x0800`, `0xe196`, `0x1900`, `0x0000`, `0x3300`, `0xaf00`, `0x5300`, `0x0024`, `0x2ab0`, `0x0033`, `0x0034`, `0x001c`, `0x22b0`, `0x0700`, `0x12c0`, `0x2757`.


## Described vs settled — two different numbers

**331 types are DESCRIBED** (framing, parent/child, per-session counts, payload layout
where decodable) and **0 real types are undocumented**.

Of the 197 typed sections in the spec, **54 still say the PURPOSE is unproven** —
but that counts SECTIONS, and 26 of them cover more than one type: a heading naming ten
types is flagged when ONE is unproven. `0x1056` was counted unsettled while its own row
read *"the MIDI placement wrapper"*. Read per TYPE instead, the flagged sections name
**at most 110 unproven types**, with 10 already settled inside them. Sections carrying an
explicit `Purpose status (per type)` line are counted from it; the rest still contribute
every type they name, so 110 is an UPPER BOUND, not a measurement.

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
noise there. **151 of the 151 undocumented types are phantoms** by one of FOUR measured
signatures: ≥50% of instances nested inside a leaf payload (`0x1038` plug-in state,
`0x2613`/`0x2615`/`0x2616` view blobs, `0x1003`, `0x1017`); a type whose low byte is
`0x00` that the master index never references — the `256 × k` arithmetic of a string
LENGTH or first letter being read as a type (`0x1600` = 256×22, `0x0c00` = 256×12); an
8-byte field under a `0x1014`; or a `u32` COUNT at the head of a container's payload
whose low byte is `0x5A` (`0x2757`, the only one, is the number 346).

The spec's phantom catalogue goes further and traces each one to the byte that produced
it — mostly text the user typed: `0x2a00` is the last letter of a track named `DRUMZ`,
`0x0c00` is the `Z` in `iZotope`. **A phantom's type number is a function of the
corpus's naming habits**, which is the deepest reason an unfiltered coverage count means
nothing.

Reporting them as undocumented would hand the next reader a work list that is mostly
garbage — the same mistake, in the other direction, as the FALSE 100% this file once
carried.

**0 content-types remain genuinely undocumented.**

## Undocumented types — raw, phantoms included

Sorted by how many sessions carry them (ubiquity first), then block count.

| content-type | blocks | in #sessions | indexed | 
|---|---:|---:|---:|
| `0x2401` | 62 | 31 | 0 |
| `0x7500` | 62 | 31 | 0 |
| `0x7e00` | 31 | 31 | 0 |
| `0x7200` | 31 | 31 | 0 |
| `0x2201` | 31 | 31 | 0 |
| `0x8201` | 31 | 31 | 0 |
| `0x7400` | 31 | 31 | 0 |
| `0x3405` | 31 | 31 | 0 |
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
| `0x7b03` | 31 | 31 | 0 |
| `0xdafa` | 31 | 31 | 0 |
| `0x4b00` | 31 | 31 | 0 |
| `0xf102` | 31 | 31 | 0 |
| `0xd102` | 31 | 31 | 0 |
| `0x5903` | 31 | 31 | 0 |

…and 91 more (long tail, each in <32 sessions).


## Documented types

331 types are covered by `ptx-format-spec.md`, accounting for 99.94% of block instances.

