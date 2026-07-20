# `.ptx` content-type coverage tracker

*(The file-header index-pointer record at body offset 20 is excluded — it is not a block; see the spec. Counting it invents one bogus type per session.)*

Measured with the **size-driven block walk** (`body_synth._size_driven_blocks`) over the
**282-session local corpus** — 256 distinct real content-types, 562692 block instances.

**256/256 content-types have a spec entry = 100.0%**, covering 100.00% of block instances.

**Read that number carefully.** It means every type observed in this corpus is
*described* — framing, parent/child, per-session counts, and payload layout where
decodable. It does NOT mean every field's *meaning* is proven: a number of spec entries
explicitly say `purpose UNKNOWN` or mark a field `UNPROVEN`. Those are the remaining
semantic work, left deliberately visible — this file once carried a FALSE 100%, so the
fix is to state exactly what was measured and what was not.

> **Correction (2026-07-20).** This file previously claimed *280/280 = 100.000%* over a
> 26-session corpus, enumerated with the older `0x5A`-scan parser that **undercounts real
> sessions**. Re-measured with the size-driven walk over the full corpus: **256** real types.
> 20 of them occur in EVERY session yet were missing from both this tracker and the spec;
> they have since been dissected and documented. A naive walk also reports ~213 extra
> "types" that are really ONE artifact — the file-header index pointer at body offset 20,
> whose per-session value is misread as a content-type (see the spec). It is excluded here.

## Undocumented types — the remaining work list

Sorted by how many sessions carry them (ubiquity first), then block count.

| content-type | blocks | in #sessions | indexed | 
|---|---:|---:|---:|

## Documented types

256 types are covered by `ptx-format-spec.md`, accounting for 100.00% of block instances.

