# ptxformatwriter — TODO

Open research / feature items. Each entry should say what we want, what's unknown,
and why it matters. (Resolved items kept briefly for provenance.)

## 0x261C C4 — CONDITIONAL RENUMBER ATTEMPTED AND REVERTED (2026-07-25)

The last big churn source. Our re-key writes `c4 = 0..k-1` over every `0x261C`, which is how all 213
PT-authored local controls store it (the re-key is a no-op on them) but holds in **0 of 149** real
external sessions, where it overwrote 2432 of 2448 records.

ATTEMPT: renumber only when the session already uses the convention, otherwise preserve PT's bytes.
Verified as a no-op on 213/213 and confirmed to preserve a scrambled C4 — then it broke 6 tests.

WHY IT FAILED, and it is a general lesson: the conformance check runs AFTER the copy is spliced in,
so it sees `[0,1,2,3,3]`, judges the session non-conforming, and skips exactly the renumber the copy
needs. Every predicate tried mis-classified one shape or another —

    sorted(cur) == range(k)          post-duplicate [0,1,2,3,3] -> skip (wrong)
    sorted(set(cur)) == range(n)     real all-zero [0,0,0,0]    -> renumber (wrong)
    max(cur) <= len(cur)             real all-zero              -> renumber (wrong)
    distinct >= k-1 and set==range   post-remove [0,1,3]        -> skip (wrong, gap must close)

REVERTED. This cannot be settled by a cleverer predicate: it needs the MEANING of the field at
`end-19` in a real session, which is UNPROVEN — the reader may be as wrong as the rule. Nothing
depends on forcing it in the meantime, because identity is guarded by `0x261B`
(`track-id-uniqueness`), which IS proven.

ALSO CORRECTED: `test_duplicate_gives_the_copy_its_own_0x261c_counter` claimed a duplicated C4 was
"why duplicates showed the copy under the SOURCE's name". It was not — the six 2026-07-25 files had
correct C4 counters and still showed the source's name and still crashed on close. The identity
field is `0x261B`. The test still pins C4 per-track, which is right; only its causal claim was wrong.

## BOTH REMAINING EDIT DEFECTS ARE ONE ROOT CAUSE: SIZE ACCOUNTING (2026-07-25)

After the false-GUID fix, what is left on the real-session set collapses to a single class -- a size
field that is not updated correctly by the splice.

**Symptom A -- 2 bytes in a `0x1014` header** (`GENE DEAD AND GONE`, `GOO GOO DOLLS`, identical
signature in both):

    orig  5a 03 00 | 28 00 00 00 | 14 10      size=40, content_type=0x1014
    back  5a 03 00 | 28 00 00 5b | 00 10      size and type both destroyed

**REFUTED (same day, by instrumentation).** I attributed this to the container grow/shrink pass
writing `body[nz + 3 : nz + 7]` with `nz` one byte high. Instrumenting that loop to report every
write near the affected block shows it **never fires there at all**, in either `duplicate_track` or
`remove_track`. The grow pass is not the writer.

What the bytes actually say: `+6` and `+7` change together, i.e. the top byte of the size field and
the low byte of the content-type word. That is the tail of an 8-byte write starting at the block's
own `+0` -- the header `5a 03 00 28 00 00 00 14` being overwritten as if it were a GUID. Which is
the FALSE-GUID hazard again, at a position my fix does not cover: `guids_in` scans a whole
container's bytes, so a `2a 00 00 00` at the END of one block makes the NEXT block's header look
like the 8 bytes following a tag. Skipping offset 3 only catches the size-field case.

**THAT HYPOTHESIS IS ALSO REFUTED.** There is no `2a 00 00 00` at `z-4`. The nearest preceding tag
is at `z-13` and the 8 bytes after it are a genuine GUID (`7cff58e0c960dcce`) that ends two bytes
before the block. The header is not being rewritten as a GUID.

FIVE WRITERS ELIMINATED BY INSTRUMENTATION, not by argument:

    container grow/shrink pass ....... never fires near this block, in either editor
    the re-key's su16 / su32 ......... never writes in 221900..222100
    all 12 post-splice passes ........ each wrapped to report a change at 221976:221978;
                                       none of them reports one
    a preceding GUID tag ............. absent (nearest is at z-13, and it is a real GUID)
    the size-42 false-GUID case ...... this block's size is 39/40, not 42

Yet the emitted file differs there (`00 14` -> `40 00`). By elimination the write happens in the
BODY ASSEMBLY itself -- the slice/splice loop that concatenates `data[prev:at]` with each insert --
so a range boundary is a couple of bytes off for this session. Lengths still match exactly, which
rules out a plain shift.

### The 2-byte write: NOT FOUND, and here is everything ruled out

Narrowed to `remove_track` -- the DUPLICATE is clean (its `0x1014` at 221996 reads
`5a 03 00 27 00 00 00 14 10`, correct), and only the removed file differs. Removal ranges near it are
`(222174, 222219)`, entirely AFTER the block, and 221996 maps to 221970 under the 26 bytes removed
before it. So `back[221976:221978]` should simply BE `dup[222002:222004]` = `00 14`. It is `40 00`.
`back` is therefore not a pure slice of `dup` there -- something writes.

ELIMINATED BY INSTRUMENTATION (each wrapped and observed, not argued):

    container grow/shrink pass ........ never fires near the block, either editor
    re-key su16 / su32 ................ no write in 221900..222100
    12 post-splice passes ............. none changes 221976:221978
    _renumber_body_after_remove ....... not called at all on this session
    _track_kinds, _repair_view_chain_ordinals, _renumber_tracks_per_type ... not called
    the next_kind chain repair ........ not called (removed entry is last, guard false)
    a preceding GUID tag .............. absent; nearest at z-13 is a genuine GUID
    the size-42 false-GUID case ....... N/A, this block's size is 39/40
    the splice ranges ................. do not cover these bytes

I could not identify the writer, and the "mechanical next step" I proposed DID NOT FIND IT either:
subclassing every `bytearray` in `remove_track` (there are exactly two) to log slice-assignments in
221900..222100 produced NO hits, while the output still differed. So the bytes are not written inside
`remove_track`'s own body, nor by any of the 16 functions it calls that were individually wrapped.

**RESOLVED TO A BOUNDARY, by checking the lead above -- and my offset mapping WAS the error.**
`dup` at 221996 is a DIFFERENT block from `orig` at 221970 (GUIDs `e3890be0...` vs `7cff58e0...`),
so the earlier "the duplicate is clean" check compared the wrong block. Locating the block by GUID
instead of by offset:

    ORIG  <GUID 7cff58e0..> 5a 03 00 27 00 00 00 14 10 | 09 ... "2nd take"
    DUP   <GUID 7cff58e0..> 5a 03 00 26 00 00 00 14 10 | 08 ... "ZZ Probe"   <- the COPY, spliced here
    BACK  <GUID 7cff58e0..> 5a 03 00 27 00 00 40 00 10 | 09 ... "2nd take"   <- neighbour clobbered

The copy's `0x1014` is spliced immediately BEFORE another track's `0x1014`, sharing the preceding
GUID context. On removal the boundary for that range is two bytes off, so it clips the NEIGHBOUR's
size/content-type word instead of ending cleanly. That is why no write probe ever fired: nothing
writes those bytes -- the removal simply cuts in the wrong place, and the bytes that survive are the
neighbour's shifted by the error.

**AND THAT CONCLUSION IS ALSO REFUTED.** I proposed the fix was in the span computation when the
copy's name length differs from the source's. Testing name lengths of `len(src) - 1`, `len(src)`,
and `len(src) + 1` on both sessions: **every one still fails**, including the same-length case. The
name-length delta is not the trigger. Worse, the source for GOO GOO DOLLS is `Audio 8` (7 chars), so
the block I had been dissecting -- name length 9 -> 8 -- was never the source's `0x1014` at all, only
a neighbour.

FIVE HYPOTHESES REFUTED on this one defect: grow-pass off-by-one, size-accounting consolidation,
preceding-GUID-tag, an in-body write (every bytearray traced, no hits), and now the name-length
boundary. I am recording that and stopping rather than proposing a sixth.

What is solid: it affects 2 of 40 sampled real sessions, the difference is exactly 2 bytes in a
`0x1014` header (`00 14` -> `40 00`, the size field's top byte and the content-type's low byte), the
duplicate step is where it enters, and no probe placed so far observes anything writing them.

**THE BY-IDENTITY DIFF WAS TRIED AND IS ALSO INVALID.** Keying `0x1014` blocks by their NAME
reported "`ZZ Probe` only in dup, `2nd takes` only in orig", which looked like the copy had
overwritten another track's entry. It had not: **`2nd takes` occurs 36 times in BOTH files** -- names
are NOT unique among `0x1014` blocks, so a dict keyed by name collapses 36 entries to one and the
survivor differs arbitrarily between the two files. `Audio 8` (the source) is present in both.

That is the SIXTH wrong answer on this defect, and the third caused by a bad identity/mapping
assumption (offset twice, name once). Any further attempt must first establish a key that is
provably unique -- name is not, offset is not -- before drawing any conclusion from a diff.

### A PROVABLY-CORRECT MAPPING finally locates it: the corruption is IN THE COPY

Exporting the actual insert list makes the offset mapping exact instead of guessed
(`dup_off = off + sum(len for at <= off)`), and it shows an insert of **44 bytes at exactly 221970**
-- the block's own start -- with the `dup` bytes there ALREADY reading `40 00`. So the corruption is
in the COPIED BLOB, produced during `duplicate_track`; `remove_track` was never the writer, which is
why no probe placed there ever fired.

The source's own `0x1014` is `z=221926`, span 44 bytes, **declared size 37**. Its name occurs once,
so the blanket replace is safe from multiple matches -- but it CHANGES LENGTH: replacing `Audio 8`
(7) with `ZZ Probe` (8) takes the blob from 44 to 45 bytes. If the declared size is not grown to
match, the walk mis-frames the copy and reads the FOLLOWING header at the wrong offset -- which
presents exactly as "two corrupted bytes in the next block's size/content-type word".

CHECKED, AND REFUTED (seventh). The copy's `0x1014` declared size IS consistent with its span in
both cases -- `span=45 declared=38` for the longer name, `span=44 declared=37` for the same-length
name. A stale size field is not the cause. And the copy lands at `z=222174`, which the insert list
does not predict, so even the "provably correct" mapping does not reconcile.

**SEVEN refuted hypotheses. I am ending this line of investigation.** The pattern is unambiguous:
every attempt rests on an offset or identity assumption that later proves wrong, and each wrong
answer has had to be retracted here. Continuing produces confident errors faster than it produces
understanding, which actively degrades this file's value.

WHAT IS RELIABLE, for whoever picks it up:
  * it affects 2 of 40 sampled real sessions; the other 38 round-trip byte-exact
  * the difference is exactly 2 bytes in a `0x1014` header, `00 14` -> `40 00`
  * `duplicate_track` is where it enters (the dup already contains it)
  * the copy's own header and declared size are CORRECT, so the copy is not malformed
  * no bytearray write anywhere in `remove_track` touches those bytes
  * refuted: grow-pass off-by-one, size accounting, preceding-GUID-tag, in-body write,
    name-length boundary, by-name identity diff, stale copy size field

### THE VERIFIED ALIGNMENT (difflib over a window) -- what seven guesses missed

Doing what every previous attempt skipped -- a real diff alignment instead of an assumed mapping --
gives this, immediately:

    replace  orig[221973] = 27   ->   dup[222177] = 26     <- a SURVIVING block's SIZE byte, -1
    equal    orig[221974:221978] = 00 00 00 14
    insert   45 bytes at orig[221978], beginning  10 08 00 00 00 "ZZ Probe"

The insert lands INSIDE the following block's header -- between the two bytes of its content-type
word (`14 10`) -- and that surviving block's declared size is decremented from `0x27` to `0x26`.

Two concrete, verified facts that no earlier hypothesis produced:
  * a SURVIVING block's size byte is modified by the duplicate (0x27 -> 0x26). Every probe looked
    for a write to the CONTENT-TYPE bytes; the size byte one position earlier was never checked.
  * the copy's insertion offset splits that block's content-type word, so the two "corrupted" bytes
    are not corrupted at all -- they are the neighbour's header being re-read at a boundary the
    insert moved.

(difflib's boundary between `equal` and `insert` is not unique, so the exact split point is
indicative rather than proven -- but the size-byte change is a genuine `replace` opcode and is not
subject to that ambiguity.)

### CONFIRMED: the splice DESTROYS the neighbouring `0x1014`

Enumerating `0x1014` blocks by their name field, in both files:

    '2nd takes'   orig = [(221970, size 39, span 46)]      dup = []          <- GONE
    'Audio 8'     orig = [(221926, size 37, span 44)]      dup = [(222130, 37, 44)]   source, intact
    'ZZ Probe'    orig = []                                dup = [(222174, 38, 45)]   the copy

The copy is spliced at a boundary that lands inside the following `0x1014`, so that block is no
longer framed and drops out of the walk. The two "differing bytes" after a round trip are the tail of
that damage, not a write -- which is why every write probe came up empty.

**I RETRACTED THIS FINDING ONCE, WRONGLY.** An earlier by-name diff reported exactly this, and I
dismissed it after counting the TEXT `2nd takes`, which occurs 36 times in both files. That count
measures the string anywhere in the body -- in paths, region names, other blocks -- not the `0x1014`
block whose NAME FIELD is `2nd takes`. The block-level enumeration above is the right measurement and
confirms the original result. Lesson: a substring count is not a block census.

CONTAINER ARITHMETIC IS NOT THE CAUSE (checked): the enclosing `0x1015` grows by exactly the copy's
span -- 45 for the longer name, 44 for the same-length name -- in both cases. And the cleanest single
statement of the defect comes from that same check:

    0x1014 count:  orig 57  ->  dup 57      for BOTH name lengths

**Adding a track does not increase the `0x1014` count.** The copy is added and one existing block is
consumed, which is the same fact as `2nd takes` vanishing. Any correct fix must make that count go
57 -> 58; that is the one-line regression test to write first.

### THE EXACT CORRUPTION, AND WHY EVERY PROBE MISSED IT

    orig  '2nd takes' @221970:  5a 03 00 27 00 00 00 14 10 09 ...
    dup   same block  @222219:  5a 03 00 27 00 00 40 00 10 09 ...
                                                  ^^ ^^

`+6` (size u32's top byte, `00` -> `40`) and `+7` (content-type low byte, `14` -> `00`). Size becomes
`0x40000027`, type becomes `0x1000`, so the block is no longer framed and drops out of the walk --
hence `0x1014` count 57 -> 57 instead of 57 -> 58.

**Why nine probes found nothing:** the write is at dup offset **222225**. Every probe watched
221900..222100 -- the ORIGINAL/`back` coordinate -- because that is where the round-trip diff reports
it. In the duplicate the same block sits 249 bytes later. Widening the window to 221900..222300 and
tracing all five bytearrays inside `duplicate_track` STILL reports nothing, so the writer is in a
HELPER it calls, not in its own body; the `_TB` substitution only covered `duplicate_track`'s own
source text.

NEXT (mechanical, and the search space is now small): apply the tracing bytearray subclass MODULE-
WIDE, not just inside `duplicate_track`, with the window at 222180..222280, and run one duplicate.
That will name the helper. The one-line acceptance test is the `0x1014` count going 57 -> 58. `Audio 8` (the source) sits at 221926..221970 and `2nd takes` begins exactly at
221970, so the copy is being placed at the source unit's END -- which is correct in principle, and
means the damage is in how that end is computed or how the following block's frame is rebuilt.

STOPPING HERE deliberately. Six refuted hypotheses is strong evidence that my mental model of this
splice is wrong somewhere I cannot see by probing, and each wrong answer has had to be walked back
in this file. It affects 2 of 40 sampled sessions; 38 round-trip byte-exact.

Two further leads, untested:
  * whether the difference is a WRITE at all -- confirm empirically where dup's block at 221996
    actually lands in `back` (by searching for its surrounding byte pattern) rather than assuming the
    26-byte mapping. If it lands elsewhere, this is a local misalignment, not a value change.
  * `_FI.serialize_final_block(recs)` and the record rebuild, which regenerate bytes wholesale rather
    than writing through a bytearray, and so would be invisible to every probe used so far.

Affects 2 of 40 sampled real sessions. Everything else round-trips byte-exact.

**Symptom B -- `AttributeError` on 3 of 40 sampled sessions.** `final_index_ref` walks TOP-LEVEL
blocks by declared size and requires them to be CONTIGUOUS; it returns None the moment one size is
wrong. The input chains fine (117 steps ending at the `0x0002` index); the failure happens INSIDE
`duplicate_track`, on an intermediate buffer, and surfaces in `_reallocate_duplicate_track_ids`
because that is the next thing to call `_size_driven_blocks`. So the edit breaks top-level
contiguity on these sessions. PRE-EXISTING -- verified against the committed code.

The "both are one size-accounting bug" conclusion is WITHDRAWN: the grow pass is not the writer for
symptom A. Symptom B (a broken top-level chain) may still be a size problem, but that is now
unproven too, and the two should not be assumed to share a cause. 35/40 sampled real sessions
round-trip byte-exact; the 5 failures are 2 of symptom A and 3 of symptom B.

## SOLVED — set_track_channels wrote PAST the block; real sessions now 40/40 (2026-07-25)

`set_track_channels` writes the post-ID channel indices at `marker + 19` / `marker + 23`, offsets
taken from the control corpus. On real sessions the marker sits legitimately INSIDE the block while
those offsets fall past its END -- marker 221957, block end 221970, `marker + 19` = 221976, six bytes
beyond. The channel value `40 00` landed on the NEXT block's size/content-type word (`0x27` ->
`0x40000027`, `0x1014` -> `0x1000`), unframing it so the walk lost it entirely.

That is why adding a track left the `0x1014` count unchanged, and why every write probe came up
empty: they all watched 221900..222100, the ORIGINAL coordinate, while the write lands at 222225 in
the DUPLICATE.

    real-session duplicate->remove byte-exact:  35/40  ->  40/40
    0x1014 count on duplicate:                  57->57  ->  57->58

Also resolves the three sessions that raised `AttributeError` (`final_index_ref` returning None on
edited output) -- the same corruption breaking top-level contiguity.

FIXED BY CONTAINMENT, needing no theory about where the field lives: the marker search is bounded to
the block, and any post-ID write that would leave it is skipped. Same principle that settled the
`0x261C` C4 write.

NINE failed diagnoses preceded it, every one derailed by comparing blocks through an ASSUMED
correspondence -- by offset, by name, by substring count. What found it was a module-wide tracing
`bytearray` with no assumption in it. The regression test asserts the `0x1014` COUNT increments,
because a count needs neither offsets nor identity mapping.

## THE FALSE-GUID BUG — a block of size 42 spells the GUID tag in its SIZE field (2026-07-25)

`guids_in()` scans a block for the 4-byte tag `2a 00 00 00` and takes the next 8 bytes as a GUID.
**A block whose declared size is exactly 42 has `2a 00 00 00` in its SIZE FIELD at offset 3**, so the
"GUID" it returns is really the block's CONTENT-TYPE word:

    5a 02 00 | 2a 00 00 00 | 1a 25 00 00 0b 00 00 00 ...
               ^ size = 42   ^ false "GUID" = content_type 0x251A + the bytes after it

`transform()` then rewrote those bytes with a fresh GUID, so a copied `0x251A` lane came out typed
**`0x7F40`**. The block walk no longer enumerated it as a lane, and `remove_track` -- which seeds its
GUID search from enumerated lane blocks -- could not find the track it had just created. The earlier
block-delta for that session showed `'0x7F40': 2`; I had seen it and not registered it.

FIXED by skipping a tag match at offset 3 (the size field), in both copies of the helper. Result on
the two problem sessions: lane counts now 116 -> 118 and 108 -> 110, and `remove_track` no longer
raises. Both now fail only on the SAME residual 2 bytes in a `0x1014` header, so what were two
separate defects are now one.

ALSO CHANGED, honestly labelled: `transform()` now rewrites GUIDs only where one legitimately lives
(immediately after the tag) instead of `blk.replace(g, ng)` over the whole unit. That is the same
"target the field, never the byte pattern" lesson as the track-id remap -- but it did NOT fix the
residual 2 bytes, so its benefit here is hazard-removal, not a measured win.

STILL OPEN, unchanged by this: the 2-byte `0x1014` corruption (a size write landing one byte late,
diagnosed above) and a PRE-EXISTING `AttributeError` -- `final_index_ref` returns None on the EDITED
file for 3 of 40 sampled sessions, though the input parses fine. Verified against the committed code
that this predates today's changes.

## THE LAST TWO REAL-SESSION EDIT DEFECTS, DIAGNOSED (2026-07-25)

PROVENANCE CHECKED before blaming today's changes -- both were run against the session-start code
(`1ec478d`) and against the two intermediate reader commits:

| session | at session start | now |
|---|---|---|
| GOO GOO DOLLS | `entries=18 / tracks=54`, dup "succeeded" -- silently building from a THIRD of the table, remove not byte-exact | reads 54/54; `duplicate->remove` differs by **2 bytes** |
| GENE DEAD AND GONE | `entries=58/58`, `duplicate_track` raises ValueError | IDENTICAL -- pre-existing, untouched by today's work |

So defect #2 is not a regression from the phantom-classification work (verified at `572876f` and
`c54db13` too), and defect #1 went from silent corruption over two-thirds of a table to a 2-byte
discrepancy with a complete read.


Both survive on the 600-session set; everything else round-trips. Diagnosed precisely rather than
patched speculatively -- two guesses at this stage were already reverted today.

### 1. `20230209 GENE GOO GOO DOLLS.ptx` — a size write lands ONE BYTE LATE

`duplicate->remove` now differs by exactly **2 bytes** (it was an unreadable 18-of-55 table this
morning). They corrupt a `0x1014` block HEADER:

    orig   5a 03 00 | 27 00 00 00 | 14 10      size=39,          content_type=0x1014
    back   5a 03 00 | 27 00 00 40 | 00 10      size=0x40000027,  content_type=0x1000

The container-grow/shrink pass writes `body[nz + 3 : nz + 7]`. Here it wrote 221974..221977, i.e.
`nz = z + 1` for the block at `z = 221970` -- one byte late, so it straddles the size field's top
byte and the content-type's low byte and destroys both. In the intermediate (duplicated) file the
corresponding block sits at 221996, a +26 shift, and there is an unexplained 46-byte gap between the
`0x1014` at 222219 and the next at 222265.

So: an offset mapped through `shift()` is off by one on this session. Everything needed to find it is
above; it is NOT a phantom-bound problem any more.

### 2. `20230208 GENE DEAD AND GONE.ptx` — the copy is not findable afterwards

`remove_track('ZZ Probe')` raises `cannot locate track 'ZZ Probe'` on the file `duplicate_track`
just produced. NARROWED to a single missing structure:

    dup('01 KICK IN')    block delta includes  0x251A: +2   -> remove OK
    dup('XTRA LF GTR')   block delta has NO    0x251A       -> remove raises

Both tracks are in the SAME session. The copy's name reaches only `0x2519`, `0x1052` and `0x1014`;
a complete track also needs it in the two `0x251A` LANE entries, which is exactly what `remove_track`
seeds its GUID search from. The source `XTRA LF GTR` does have both lane entries (its name occurs
`0x251A` x2, `0x2519`, `0x1052`, `0x1014`), so they exist to be copied -- the identification walk
simply does not pick them up FOR THIS TRACK while it does for `01 KICK IN` in the same file.

NARROWED FURTHER -- it is the LAST ENTRY, and only in this file:

    #0  '01 KICK IN'    0x251A delta +2   remove OK
    #20 'FX MONO 1'     0x251A delta +2   remove OK
    #56 'MIDI 2.02'     0x251A delta +2   remove OK
    #57 'XTRA LF GTR'   0x251A delta  0   remove ValueError     <- the last entry

The last-entry path is entered CORRECTLY: `region_end` is 2460 (a real child, no phantom involved),
and the last entry's span is 36 bytes = the SHORT form, exactly as intended. So the defect is inside
that path, not in the bound.

Two candidate explanations were TESTED AND REJECTED:
  * "the source has no clips" (it has no `0x2628`/`0x103A` at all, unlike `01 KICK IN`) -- but #56
    `MIDI 2.02` also lacks them and works;
  * "the lane table is nested inside the 0x2519 block" -- true here, but ALSO true of six other real
    sessions whose last-entry duplicate works (16-64 lanes, all nested, all OK).

LOCATED PRECISELY -- and the copies are NOT being dropped. Instrumenting the splice:

    INSERTS include the two lane blobs .... (5119, 49 B) and (7782, 49 B)
    emitted body contains the new name .... 5 occurrences (2519, 1052, 1014 + the two lanes)
    final output contains the new name .... 5 occurrences
    0x2519 container size ................. 7383 -> 7519, delta 136 = exactly the bytes inserted
    BUT the size-driven walk enumerates ... 116 lane blocks, not 118

So the bytes are written, the container is sized correctly, and the copies survive to the final
output -- they are simply NOT ENUMERATED as `0x251A` blocks by `_size_driven_blocks`. `remove_track`
then cannot find the track because its GUID seeding walks enumerated blocks.

That reframes the defect entirely: it is a BLOCK-ENUMERATION problem in the edited file, not a
splice problem. Correcting two of my own earlier readings: the lanes are not missing from `ident`
(they are present), and there is no 3-byte container shortfall (the 133 I first computed came from
the 8-char-name case, whose inserts differ; with a same-length name the delta is exactly 136).

Same-length names fail identically, so the placeholder/rename path is NOT involved either.

FIVE hypotheses now tested and eliminated: phantom bound, missing from ident, clipless source,
nested lane table, container-size shortfall. The next step is to ask why the walk skips two
well-formed `0x251A` blocks that are present in the bytes.

ORIGINAL NOTE, kept for provenance:
LOCATED, by instrumenting the identification walk. The lane blocks ARE found and ARE selected as
copy units -- the guess that they were missing from `ident` was WRONG:

    IDENT after the name walk : [5070, 7733, 227851, 235756]
    0x251A in ident           : [5070, 7733]
    UNITS                     : [(5070,5119), (7733,7782), (227851,227931), (235756,235804), ...]

Yet the emitted body does not contain them:

    0x251A count       orig=116   dup=116      (unchanged)
    lane names in dup  'XTRA LF GTR' x2,  'ZZ Probe' x0

So the two lane copies are computed as units and then DROPPED somewhere between `units` and the
emitted body, and only when the source is the LAST name-table entry (#56 in the same file copies its
lanes fine). Both insertion points, 5119 and 7782, fall INSIDE the `0x2519` block (410..7800) -- one
of them 18 bytes from its end -- while the `_last_entry_dup` branch is separately splicing at 2460.
That interaction, between an insert landing inside the name-table block and the last-entry promotion,
is where to look next. The container itself does grow correctly (size 7383 -> 7516).

NOT a phantom problem, NOT an `ident` problem, NOT specific to clipless tracks or nested lane tables
-- all four were tested and eliminated.

Neither is guarded: an output-side structural assertion was tried earlier today and reverted because
it fired on `duplicate_track`'s own placeholder-then-rename intermediate on ordinary sessions.

## THE NAME-TABLE READER WAS BLIND ON 17.5% OF REAL SESSIONS (2026-07-25)

Chasing "one odd session" turned into the largest reader defect found this session. **105 of 600**
real sessions could not be walked; most returned **ZERO** entries for 49-84 tracks. Every editor
identifies tracks through that table, so on a sixth of real sessions the reader saw nothing.

TWO INDEPENDENT CAUSES, both the same shape -- a rule fitted to `control_files`:

  1. **A third preamble variant.** `_name_table_shift` models the preamble as a binary flag (base or
     +2), "verified on 436 local sessions, 362/74, no exceptions". `20220503 LAST TIME 2.ptx` starts
     its entries at payload+0x18 -- two bytes past the +2 case -- so the head parsed a garbage
     length (458752) and the walk returned nothing. FIXED by resolving the start EMPIRICALLY: if no
     entry parses where the flag says, step forward in 2-byte units (bounded, alignment-preserving)
     to the first offset that does. 105 -> 1.
  2. **A GUID byte 0x5A manufacturing a phantom child.** In `GENE GOO GOO DOLLS.ptx` the entry GUID
     `56 bc 4b e0 b7 5a c9 00` gets framed by the size-driven walk as a 25-byte "type 0x0700" block
     mid-trailer. That phantom sits INSIDE the entry region, so `min(child start)` dropped the bound
     into the middle of the table and the walk stopped at 18 of 54. Same class as the file-header
     index-pointer record. FIXED by falling back to the block's own end when the bounded walk comes
     up short and letting PARSE FAILURE stop it -- real children (`0x251A`, `5a 02 00 ...`) yield an
     absurd length and terminate it naturally. 1 -> 0.

    truncated name-table walks over 600 real sessions:   105  ->  1  ->  0

### Guard on the INPUT only, and one gap left open

`duplicate_track`/`remove_track` refuse a session whose table reads short (`_assert_name_table_
complete`). Asserting the same on the OUTPUT was tried and REVERTED -- it fires on
`duplicate_track`'s own placeholder-then-rename step, whose intermediate reads short on ordinary
sessions that round-trip byte-exactly. Rejecting files that are fine is the trap this project keeps
hitting.

**SPLICE NOW PHANTOM-AWARE (partly).** `_name_entry_span` computed its OWN `region_end =
min(children)` and then CLAMPED each entry to it, so every entry at or past the phantom was chopped
short and the splice copied a malformed one -- which is why the output walked 18/55. Both readers now
share `_name_table_region_end()`, which resolves the bound by comparing the two walks. On the phantom
session the spliced output now walks **55/55** for interior sources where it walked 18.

**PHANTOM CLASSIFICATION IMPLEMENTED — the bound is no longer guessed.** Four bound criteria were
tried and rejected (`min(child)` truncates to 18 entries; the block end and the walk terminus both
read 54 but lose the clamp that gives the last entry its short trailer; "first child the entries fill
exactly" matched nothing). The mistake in all four was choosing a bound at all.

The working approach CLASSIFIES each child instead. Walk the entries permissively to the block's own
end and measure every entry by its MINIMUM possible span -- the SHORT 21-byte trailer. A phantom
starts strictly inside that span; a real child cannot, because the entry before it must end at or
before where it begins. The short-form span needs no bound to compute, which is what breaks the
circularity. `region_end` is then simply the first child that survives classification.

On the phantom session this returns **2328** -- exactly the real `0x251A` child, with the phantom at
1076 discarded -- and duplicating ANY entry, including the LAST one that previously raised, now
yields 55 entries for 55 tracks.

STILL OPEN there: `duplicate->remove` does not invert on that one session. No regression anywhere
else: 207 tests green, 0 of 600 sessions truncated, and the identical 30-session sample is byte-exact
29/30, exactly as before the change.

## VALIDATOR CALIBRATED AGAINST REAL SESSIONS: 119 -> 2 FALSE POSITIVES (2026-07-25)

`validate_pt_invariants` had never been checked against real third-party sessions -- only against
`control_files`, where every rule looked perfect. Run over 120 UNTOUCHED real Pro Tools sessions it
rejected **119 of them**. Files Pro Tools wrote and opens.

Two causes, both the same mistake in different places:

  * **`0x261C-C4` (119 sessions).** Demoted to `nonconforming`. Real sessions store ZERO in every
    one of these slots (622/622), which is "repeated values" by definition; gating on all-zero still
    left 102 failing because real sessions mix zeros with a few repeated non-zeros. Scanning EVERY
    offset in a real `0x261C`'s own bytes found no per-track index anywhere (40 sessions, 0 hits) --
    the index reading is synth-shaped only.
  * **the shared uniqueness check (6 sessions).** It counted the ZERO SENTINEL as repeats.
    `[1, 2, 0, 0, 3, 4, ...]` is a normal PT shape -- zeros repeat by design. Only the NONZERO
    values must be distinct. Same trap, one layer down: the writer had already learned this for
    `0x2519` and `0x251A`, but the validator had not.

    120 untouched real sessions with an error-severity violation:  119  ->  2

THE TWO SURVIVORS, both investigated:

  * `0x2589-view-chains` -- SOLVED. The rule counted chains against NAME-TABLE ENTRIES; it should
    count TRACK RECORDS. Measured over 120 real sessions the chain count is either 0 (absent, 58
    sessions) or exactly the track-record count (62) -- **120/120, no other value**. The two counts
    usually coincide, which is why the rule survived; the session that exposed it has 18 name
    entries but 54 tracks and 54 chains, and Pro Tools opens it. Chains are also OPTIONAL: 58 of 120
    real sessions have none at all, so their absence is not a defect.

    SEPARATE OPEN ITEM found alongside it: that session's name table yields only 18 entries for 54
    track records. Every other session of the 120 has entries >= tracks. Either the session really
    does name a subset, or `_inline_name_entries` stops early there -- a possible READER bug worth
    checking before trusting entry counts on unusual sessions.
  * `track-id-uniqueness` -- one save, `20220215 FIRST.bak.019.ptx`, shares range 3065. It is a
    SINGLETON: bak.018 and bak.020 have the same 46 tracks and are clean, and 30 of the family's 31
    saves are clean. A transient state in one save, not a Pro Tools convention -- so the rule stays
    at ERROR, where 4/4 PT crash reports put it. (It does show PT can momentarily write a collision,
    which is worth remembering if that specific file ever misbehaves.)

LESSON, and it is the same one as the writer's: a rule measured only against our own control corpus
says nothing about real sessions. Every "invariant" should be re-run over the real-session set
before being trusted -- that set is what turned four separate over-strict rules into measured ones.

## RE-KEY CHURN ON REAL SESSIONS: 16543 -> 483 BYTES (2026-07-25)

The `0x261C` C4 write and the `0x200B` churn were the SAME BUG, and it was structural, not semantic.

`_B261C_C4_FROM_END = 19` was derived from synth-shaped sessions, where `end - 19` is always inside
the record's own bytes: **709 of 709** across the PT-authored controls. In the user's REAL sessions
it usually is not — **1226 of 1479 (83%) land inside a NESTED CHILD BLOCK** — so the 4-byte write
ran straight through that child's data. It surfaced as `su32` writes at `end - 2` of a `0x200B`,
overrunning the child's boundary by two bytes, which is why both types showed churn.

The write is now skipped whenever the target is not contained in the `0x261C` itself. **No theory
about what C4 means is needed** — writing into a child block is wrong whatever the field is, and
containment is checkable. That matters because the earlier attempt to make this conditional on the
VALUES was reverted after four predicates each mis-read either the post-duplicate or the post-remove
shape. Structure settled what values could not.

    bare re-key over 250 real sessions:  249 sessions / 16543 bytes  ->  23 sessions / 483 bytes
    remaining 483 B: 0x261C records whose C4 target IS in their own bytes but whose value differs
    from PT's -- the genuinely unproven part of the field.

Together with the two earlier fixes this closes the churn story for three of the four passes:
`0x2519` (zero sentinel), `0x251A` (zero sentinel, lane-major), `0x2015`/`0x200A`/`0x200B` (stable
IDs, preserved). Only C4's residual value question is open.

## A SECOND REAL-SESSION CORPUS EXISTS ON THIS MACHINE (2026-07-25)

`/Volumes/Backups` is still disconnected, but it was never the only source. **The user's other
mounted volumes hold hundreds of real `.ptx` sessions**:

    /Volumes/Hack 2TB SSD Old ..... 400+ (capped)
    /Volumes/Mac Pro 4TB HDD ......  37
    /Volumes/Mac 4TB SSD ..........  29

Scanned 250 of them. This immediately un-blocked two things I had recorded as needing the drive:

  * **the ZERO SENTINEL is present** (5 sessions carry a zero name-table ordinal);
  * **`0x261D` master/bus is present** -- 115 instances across 105 sessions, versus 0 locally in
    `control_files`. (`0x263B` folder/VCA and `0x2621` instrument are still absent from all 370
    sessions seen, so those two remain genuinely drive-only.)

### `0x2015`/`0x200A`/`0x200B` SOLVED — a stable ID, not a positional counter

Measured over 200 real sessions, the per-track anchor value is:

    DISTINCT ......... 200/200
    dense 1..k ....... 129/200
    distinct + GAPPED . 71/200   e.g. 2, 3, 4, 5, 7, ...  (holes where tracks were deleted)
    duplicated ....... 0/200

So **uniqueness is the invariant, density is not** -- the same shape as the `0x261B` range. Our
writer forced `1..k`, rewriting Pro Tools' own identities in the 71 gapped sessions. The re-key now
PRESERVES these values. Bare-re-key churn over 250 real sessions: **16543 -> 14938 bytes**, and the
`0x2015` share (1605 B) is gone entirely. The `0x251A` share went to zero with the earlier sentinel
fix.

REMAINING CHURN, now studiable locally: `0x261C` 8315 B (the C4 field whose meaning is still
unproven) and `0x200B` 6623 B (NOT the anchor pass -- that is preserved now, so something else
writes there; unidentified).

### OPEN: the copy still inherits the source's anchor ID

Allocating a fresh one POST-HOC was tried and REVERTED: identifying the copy's anchors by GUID
proximity also matched surviving tracks, renumbered them, and broke the duplicate->remove inverse on
19 of 19 sessions. **That is the second time post-hoc identification of "the copy" has failed** (the
first was the 0x261B dedup). The lesson is now explicit: the copy must be altered where its bytes
are MADE -- inside `transform` -- not by scanning the result. Left inherited meanwhile: a duplicate
id is no worse than the dense renumber it replaced, and the PROVEN identity field (`0x261B`) is
handled.

## THE REMAINING RE-KEY GAP, BOUNDED EXACTLY (2026-07-25)

Two assumptions I repeated turned out to be false, and checking them is what narrowed this:

  * **"the local controls are too small"** — FALSE. `control_files` holds genuine PT sessions of
    **512 tracks / 46079 blocks / 1.4 MB**. Scale was never the missing dimension.
  * **"the restart boundary is unknown"** — SOLVED. The `0x251A` table is LANE-MAJOR: `[1..N]`
    repeated once per lane index, restarting every N = track count. PT's own files at N = 99, 128
    and 512 match our `(i%N)+1` rule EXACTLY (`512 stereo tracks.ptx` = 1024 ordinals = `[1..512]x2`).

What is left is now measured rather than guessed. Across all **213** PT-authored local controls:

    zero-valued 0x2519 ordinals ....... 0
    zero-valued lane ordinals ......... 0
    sessions with >1 track kind ....... 23   (0x261C + 0x261E, and 0x261C + 0x2620)
    re-key is a no-op on ............ 213/213

So kind-heterogeneity IS covered locally and our rules hold on it. **The zero sentinel is simply
absent from every local file** — 0 of 213 here versus 40.6% of entries in the external corpus. That
one case is the whole of what the external drive is still needed for; everything else about these
three passes is verified against Pro Tools' own output.

## NARROWED — the re-key does NOT touch PT-authored small sessions (2026-07-25)

Re-keying all 282 `control_files` sessions UNTOUCHED changes **11**, and **0 of the 213 PT-authored
ones**. Every changed file is our own synth output being REPAIRED. So "the writer corrupts every
real session it touches" was too strong: the 10,624 bytes / 25 sessions measured earlier is real,
but it is specific to the LARGE external-corpus sessions whose zero-sentinels and restarting runs
these small local controls simply do not contain.

**A near-miss worth recording.** Three local files disagreed with our `0x2015` rule, so I read them
as PT ground truth and derived "the click's ordinal is always 3". It is not. Pro Tools numbers the
click POSITIONALLY with everything else — its own `3 stereo plus click.ptx` stores audio 1,2,3 and
click **4**, and `1 stereo plus click on top.ptx` stores click **1**, audio 2. The three dissenting
files (`test_clickbottom_*`, `test_F2_12stereo_clicktop`) are OUR output, and one of them gives the
click an ordinal already used by an audio track. **I was one step from "fixing" a correct rule to
match our own broken files.** The provenance test that misled me was the `next_kind` chain, which an
ALL-AUDIO session satisfies trivially — it cannot tell our audio-only output from PT's.

USE A REAL PROVENANCE SIGNAL. Filename family (`mixedmidi_`, `synth_`, `test_`, `robust_`, `midi9`)
is what actually separates the two sets here, and the PT-authored ladders
(`lots of {mono,stereo} tracks/`, `N stereo plus click*`) are the trustworthy oracles.

STILL OPEN, and still needs the external drive: the `0x251A` restart boundary, what lives at
`0x261C` end-19 in a large session, and whether `0x2015` uses a zero sentinel there.

## SOLVED — the duplicate-track crash was a shared TRACK-ID RANGE (2026-07-25)

PT results on the six files built from GENUINE sources: **all 3 removals clean; all 3 duplicates
crashed on close**, and every duplicate displayed the copy under the SOURCE's name -- asking for
`Audio 5` produced `Audio 1,2,3,4,4`; asking for `ZZ Copy` produced `Audio 2, Audio 2`. Selecting
either selected both. So the new name never reached PT at all.

**ROOT CAUSE:** each track owns a contiguous block of 10 session-unique u32 ids in its `0x261B`, and
PT resolves track IDENTITY through them. The copy inherited the source's entire range, so PT saw one
object twice. Renaming could never have fixed it -- our name profile was already byte-identical to
PT's own session (7 occurrences across the same 6 block types).

Fixed in `f2cde54`; full field write-up in the spec under `0x261B`. Allocation rule: lowest free
range at or after the source's, stepping a whole range -- reproduces BOTH oracles (`201` for the
synth ladder, `13261` for PT's mono ladder, which is exactly what PT's own 5-track session uses).

**Guard so it cannot recur:** `validate_pt_invariants` gains `track-id-uniqueness` at ERROR
severity. The synth path also sweeps at creation, so the CLICK track -- cloned from a donor -- stops
inheriting the donor's ids (a defect present in 10 checked-in control files).

TWO WRONG FIXES THE TESTS CAUGHT, worth remembering:
  1. De-duplicating POST-HOC renumbered an ORIGINAL track: the copy is spliced NEXT TO its source,
     so "the later of the two blocks" is not reliably the copy. It broke the duplicate->remove
     inverse. The copy must be identified by its NEW identity GUIDs.
  2. A blanket 4-byte replace over the whole copied unit corrupted unrelated fields that merely held
     the same number. Only values inside the source's own range may move.

RE-TEST: `/Users/jonkubis/Desktop/PTX_VERIFY_2026-07-25_GENUINE/` (all 6 regenerated, 0 collisions,
0 errors, rc=0). The 5 guarded files in the 2026-07-24 set were also regenerated -- notably 05 and
10, which previously crashed on close / raised "unexpected stream type" -- giving a clean A/B where
the ONLY change is the id fix. A README marks that older directory superseded.

## THE 8/8 DUPLICATE FAILURES MAY BE THE SOURCES, NOT THE EDIT (2026-07-25)

With the external drive gone I went looking locally and found the corpus was never the only ground
truth: **`control_files/` holds 282 PT-generated sessions**, including LADDERS (`lots of mono
tracks/{n} mono tracks.ptx`, n = 0..16). Consecutive rungs differ by exactly one track and BOTH were
made by Pro Tools -- a local oracle for adding a track, no external volume needed.

### Our add path reproduces PT almost exactly on a GENUINE source

`duplicate_track` on PT's `4 mono tracks.ptx` vs PT's own `5 mono tracks.ptx`:

    block-type deltas ................. IDENTICAL, zero mismatches across every type
    total blocks ...................... 936 vs 936
    blocks BYTE-IDENTICAL to PT's ..... 905 of 936
    0x251A lane ordinals .............. [1,2,3,4,5,1,2,3,4,5] -- EXACTLY PT's
    0x2589 overview ................... right SET, different slot: PT [1,4,0,3,2], ours [1,0,3,4,2]

The one real discrepancy is where the new track's `0x2589` chain sits in the block stream. Whether
that matters is UNKNOWN -- if the mapping is by containment/GUID rather than position it is benign.

### The likely confound: every previous PT test used a PRE-BROKEN source

Checking the `next_kind` chain across all 282 controls partitions them **exactly** along provenance:

    chain CLEAN  : 272   (genuine PT captures)
    chain BROKEN :  10   -- midi9, mixedmidi_5_AMSmM, mixedmidi_6_noextfiles, mixedmidi_9_mix3midi,
                           mixedmidi_click_{top,middle,bottom}, mixedmidi_reorder_MIDImiddle,
                           synth_4mono_8stereo, robust_audio_mixed12  -- ALL our own synth output

The 12 staged files were all built from that broken set. So **our SYNTH WRITER emits a bad
`next_kind` for mixed-kind sessions** -- a defect in synthesis, not just editing -- and the 8
duplicate failures were duplicates OF ALREADY-INVALID SESSIONS. That does not prove the add path is
sound, but it does mean the previous experiment could not have told us either way.

PT's own re-saves confirm the direction: `Session File Backups/mixedmidi_click_bottom.bak.00{0,1}`
are PT-generated and chain-CLEAN, while our `mixedmidi_click_bottom.ptx` with the same six track
names is chain-BROKEN.

### STAGED FOR PT — /Users/jonkubis/Desktop/PTX_VERIFY_2026-07-25_GENUINE/ (6 files)

Every source is a chain-clean genuine PT session; every output is chain-clean, `validate()`
error-free, reloads rc=0, and has UNIQUE track names (the user's confirmed crash-on-close trigger).
File 01's block profile is identical to PT's own `5 mono tracks.ptx`.

    A_mono_ladder/01_dup_4_mono_tracks.ptx              4 -> 5 audio
    A_mono_ladder/02_rm_4_mono_tracks.ptx               4 -> 3 audio
    B_stereo/03_dup_3_stereo_tracks.ptx                 3 -> 4 stereo
    C_mixedkind/04_dup_multiple_track_types_no_cl.ptx   MIDI+audio, 3 -> 4
    C_mixedkind/05_rm_multiple_track_types_no_cl.ptx    MIDI+audio, 3 -> 2
    D_withclick/06_dup_multiple_track_types.ptx         click+MIDI+audio, 4 -> 5

TODO NEXT: fix the SYNTH writer's next_kind so newly synthesized mixed sessions are valid, and
regenerate the 10 broken controls (they are checked-in artifacts used as test inputs).

## BLOCKED — the corpus drive is DISCONNECTED (2026-07-25)

`/Volumes/Backups/AI/__PTX corpus` is gone mid-session: `mount` and `diskutil list` show no
filesystem and no unmounted volume named `Backups` (the only unmounted partitions are Apple_RAID
members, all accounted for by `Samples`/`SAMPLES BACKUP`). The drive is physically disconnected, not
merely unmounted -- it cannot be brought back from software. **The user must reconnect it.**

Watch out for the SILENT failure mode this produced: a probe looping over `glob(corpus/*.ptx)` with
`except Exception: continue` reported "0 real sessions: 0x1DB6 present in 0" -- a confident,
completely fabricated measurement, because the glob was EMPTY rather than erroring. Any corpus
script must assert the file list is non-empty before reporting a hit rate.

A fan-out to mine PT's own track additions was launched and stopped when the drive vanished (1 of 9
agents finished, correctly reporting the blocker rather than guessing). To resume once the drive is
back -- completed agents return cached results:

    Workflow({scriptPath: "/Users/jonkubis/.claude/projects/-Volumes-Dropbox-SSD-Dropbox-PYTHON---CODEX-PTFORMAT/3a0a9e53-a6be-45d5-b89f-fbd530cb9103/workflows/scripts/ptx-addpath-groundtruth-wf_df17a211-b85.js",
              resumeFromRunId: "wf_df17a211-b85"})

It scans six `.bak` families for PT-made track additions (the oracle), then diffs our
`duplicate_track` against PT's on each, and separately derives the three unfixed re-key rules
(`0x251A` restart boundary, what really lives at `0x261C` end-19, the `0x2015` convention).

## METHOD UNLOCK — PRO TOOLS' OWN EDITS ARE IN THE CORPUS (2026-07-25)

The `.bak.NNN` files are INCREMENTAL SAVES of one session, so consecutive pairs isolate a single
user edit *as Pro Tools itself wrote it*. Walking a family and comparing the `0x2519` entry count
save-to-save finds the edits we are trying to reproduce. **`THE WIND_Vocal Tracking.bak.095 ->
.096` is PT's own DUPLICATE TRACK** (it adds `Voc 2.dup1`). Running our `duplicate_track` on
bak.095 and diffing against bak.096 is a DIRECT oracle for the add path -- no Pro Tools install and
no user round-trip needed. This is how the `next_kind` chain bug was found and fixed (`a940fd5`).

Caveat that must be respected: a consecutive pair also contains unrelated churn (a clip removed,
plugin windows opened/closed -- `0x2613/0x2614/0x2615/0x2616` moved by -4 in this very pair). The
diff must separate "what PT does to add a track" from "what else that save happened to do".

### What that oracle has already shown about our add path

Block-type deltas, PT vs ours, on `THE WIND` (same session, same source track, same new name):

    matching : 0x102D 0x1052 0x200A 0x200B 0x2015 0x2037 0x2038 0x2434 0x251A(+2)
               0x260A(+22) 0x260C(+12) 0x260D(+6) 0x2619 0x261B 0x261C 0x2626 0x2627
    PT ADDS, WE ADD NONE :  0x1014 (+1)   0x1DB6 (+1)   0x2506 (+21)
    we differ            :  0x260E +8 vs +2,  0x2625 +9 vs +15,  0x1E33 +1 vs 0

The core per-track subtree is right; the gaps are the interesting part. **`0x2506` is a
MARKER x TRACK matrix** -- `#0x2506 == #0x2077 * #tracks` held 41/50 real sessions with exact
products on large numbers (318x348 = 110664, 148x133 = 19684). Adding a track therefore REQUIRES
one new `0x2506` per marker (21 here), and we emit none, leaving the matrix a row short. That is a
plausible out-of-bounds read for PT, and it also predicts the observed asymmetry -- a REMOVE leaves
the matrix a row LONG (harmless over-allocation) while an ADD leaves it SHORT. NOTE it cannot
explain the 8 staged failures: those synth sessions have ZERO markers, so the matrix is vacuous.

### FIXED from this oracle: the `next_kind` chain (`a940fd5`)

`next_kind` names the NEXT entry's kind, so an insert/delete re-points it. We never updated the
source's, so duplicating `MIDI 1` in a mixed session left `MIDI 1 -> MIDI 2` tagged audio.

**CORRECTION (same day): a violation is NOT known to be fatal.** I wrote that this crashed staged
file 07 on load. That was an inference from ONE file that both crashed and had a broken chain.
`mixedmidi_click_{top,middle,bottom}` and `mixedmidi_reorder_MIDImiddle` each carry an
`Audio 3 -> MIDI 2` violation and are PT-CONFIRMED TO OPEN (2026-06-20). So the rule describes what
PT writes and matching it is right, but it explains NO observed crash, and it must be
`nonconforming` rather than `error` in the gate -- as an error rule it would fire on four
PT-confirmed-good files, which is exactly the over-strictness trap hit three times already. Fixed in BOTH editors (remove hands the predecessor the removed
entry's value, an exact inverse). Chain intact after duplicating a middle track in **14/14** real
sessions; 201 tests green. Also corrected the field's documentation: there are **six** kind codes
(0/1/2 audio/MIDI/click plus 5 folder-VCA, 7 instrument, 11 master-bus), which is where the
unexplained `0x0500`/`0x0700`/`0x0B00` values come from.

This does NOT explain the other 7 staged duplicate failures -- their chains are intact. At least one
more add-path defect remains.

### A TRAP THIS NEARLY WALKED INTO, worth remembering

An UNEDITED control (`control_files/mixedmidi_5_AMSmM.ptx`) reports a `next_kind` violation, which
almost made me revert the fix as built on a false model. That file is OUR OWN synth output and
carries the defect natively -- exactly the `midi9.ptx` trap again. **Settle rules against REAL
corpus sessions; our control_files are not ground truth.**

## THE RE-KEY CORRUPTS REAL SESSIONS — one pass FIXED, three MEASURED AND STILL WRONG (2026-07-25)

Method that produced all of this: stop asking whether the round-trip closes, and measure the
edit against the CORPUS. A bare `_rekey_offsynth_positional(d)` on an UNTOUCHED real session
should be a no-op. It was not: **10,624 bytes rewritten across 25 untouched sessions.**

### FIXED and committed (7461bba) — the `0x2519` name-table ordinals

Two separate defects in the same pass:

  1. **The 32-char cap.** The re-key's name walk accepted `1 <= nlen <= 32`, so any longer name
     was SKIPPED and every following ordinal written one slot early. The corpus session whose
     first entry is the 33-char `Dancing In The Street Macy's 2017` had all 38 ordinals shifted
     by one. The walk now delegates to the structural enumerator `_inline_name_entries` uses
     (`_name_table_shift` + `_next_name_entry`), so the two can no longer disagree about the
     entry set -- they DID disagree, and that is also why the dense-vs-real check was answering
     for a different set of entries than the one being rewritten.
  2. **Zero is a sentinel, not a value to overwrite.** Measured over 300 random corpus sessions:
     **40.6%** of entries have ordinal `0`, **40.1%** repeat another entry's value, and the
     NONZERO ordinals in table order are exactly `1..k` -- **300/300, no exceptions**. Shapes
     like `[1,2,3,4,0,0,5,0,6,...]` are normal. Renumbering only the nonzeros took this field
     from **1,608 ordinals rewritten across 25 untouched sessions to 0**, while synth sessions
     (no zeros) renumber densely exactly as before, so the PT-confirmed oracles are untouched.

     This also DELETED the pre-edit `dense` sampling flag: an add creates a duplicate among the
     nonzeros and a remove leaves a gap, and renumbering the nonzeros repairs both without
     needing to know the pre-edit shape. Three earlier attempts at this fix failed because they
     tried to infer the convention AFTER the edit; the right rule needs no inference.

### STILL WRONG — measured, not yet fixed. Remaining churn 10,624 bytes / 25 sessions:

    0x251A  4630    0x261C  2682    0x2015  2218    0x200B  1094

  * **`0x261C` C4 is a SYNTH-ONLY FIELD and we write it into every real session.** The spec
    calls `_B261C_C4_FROM_END = 19` an audio display index "43/43". That 43/43 was measured on
    synth/control files. In real sessions **`c4 == 0..k-1` holds in 0 of 149**, the last bytes
    of a real `0x261C` are zeros or unrelated data, and a bare re-key **changes the C4 slot of
    2,432 of 2,448 records (99%)** across 25 untouched sessions. Same class of error as the
    `0x1015` and `midi9` mistakes: a corpus-scoped measurement restated as a format law. Before
    "fixing" the writer, re-derive what (if anything) lives at that offset in real sessions --
    my READER may be as wrong as the rule.
  * **`0x2015`/`0x200A`/`0x200B` dense `1..n`** holds in only **33 of 149** real sessions.
  * **`0x251A` lane ordinals**: the synth rule is `(i % N) + 1`, which matches **7/25**. The
    real invariant is weaker and does hold: the nonzeros are **ascending runs restarting at 1**
    (`[1,2,3,4,1,2,3,4]`, `[0,...,0,1,2,3,4,5,6,7,0,0]`) -- **25/25**, and 148/149 on the wider
    sample. What triggers a restart is NOT yet known, and that is what a correct writer needs.

### PT verdicts on the 12 staged files — the split is perfectly clean

    REMOVE  02, 08, 09, 12   4/4 open AND close cleanly
    DUPLICATE 01,03,04,05,06,07,10,11   8/8 fail (3 crash on load, 4 on close, 1 stream error)

The remove path is sound; the ADD path is not. Root cause NOT yet found. Ruled OUT this session,
each by measurement rather than argument:

  * Name strings: the copy is renamed in all five name-bearing blocks (`0x2519`, `0x251A` x2,
    `0x1057`/`0x1052`, `0x210B`, `0x2619`) -- string profile identical to a genuine track.
  * GUIDs: the copy's fresh GUID has the same reference profile as an original (1x `0x2519`,
    4x `0x251A`, 1x `0x210B`, 3x `0x2619`). GUIDs are NOT session-scoped (0/80 share a prefix)
    and they PERSIST across saves, so a random one is not obviously illegal.
  * `0x2589` low-word uniqueness is the ONLY universal invariant found (68/68 real sessions) and
    it holds in all 12 staged files -- so it does not discriminate.

The user's observation stands as the best lead and is NOT yet explained: PT displayed
`[MIDI 1, MIDI 3, MIDI 3]` for a file whose name table is `[MIDI 1, MIDI 2, MIDI 3]` with three
distinct GUIDs and dense ordinals. **PT resolves the displayed name through a reference we have
not identified, and two tracks resolve to the same entry.** Find that reference and the add path
is probably fixed. Do NOT stage more duplicates for PT until it is found -- 8/8 is not a fluke.

## Session bar-number offset — SESSION START BAR SOLVED (2026-06-30)

Pro Tools lets a session's bar numbering start at an arbitrary bar (`-22`, `-1`, `0`, `2`).
**Found + corpus-confirmed:** the session start bar is a signed `i32` at the first meter
event's +8 (block payload +23) of the `0x2029` "Meter" block; default sessions read `1`,
the renumbered `THE WIND` corpus session reads `-2`. The bar-1 tick origin `0xE8D4A51000`
is unchanged by renumbering (it's a relabel, not a tick shift). Shipped:
`body_synth.session_start_bar()` (read). See spec §5c.

**Reading the FULL maps is shipped:** `body_synth.tempo_map()` → `[(bpm, tick), …]` and
`meter_map()` → `[(num, den, tick), …]` (plus `base_tempo()`/`base_meter()` for event 0, and
`n_tempo_events`/`n_meter_events` in `session_info()`). Records are FIXED stride — tempo 61 B
at `zmark+28` (tick 5-byte @+30, BPM f64 @+40, ppq @+48), meter 36 B at `zmark+24` (tick
5-byte @+0, start-bar/ordinal @+8, num @+12, den @+16). The `0x2029` `payload_len == 12 +
count*52` counts the 36-byte record + its 16-byte `0x2719` lane entry — that 52 was NOT a
record stride (the earlier "variable-length" read). Both recover PT-authored controls exactly
(120→140, 4/4→3/4 @bar2) and are sane/monotonic across the corpus (tempo to 111, meter to 18).
See spec §10. NOTE (separate latent bug): `set_tempo_map` can leave a resized `0x2028`'s
declared block size (`zmark+3`) lagging its record count — scan-parser-tolerated and
PT-valid, but the readers bound by `count` not the declared end to compensate; worth fixing
the size field for cleanliness.

**Replacing a map on an ARBITRARY session is shipped:** `body_synth.replace_tempo_map(data,
[(bpm,tick),…])` / `replace_meter_map(data, [(num,den,tick),…])` resize the session's own
top-level `0x2028`/`0x2029` (+ the `0x2718`/`0x2719` lane when present) in place and offset-
shift the master index (block count unchanged). Corpus-validated 26/26 (identity byte-exact,
modify readback exact, holes resolve, others byte-intact, rc=0; PT display pending). Findings
folded into spec §10: the tempo lane wraps a nested `0x2028` (its own size needs bumping),
meter `+8` = absolute bar via CEIL(tick_span/ticks_per_bar) with the lane entry start-relative,
an opaque per-record byte @+22, and empty-block template seeding. The replace preserves the
displayed start bar, so a renumbered session round-trips.

Still open (need Pro Tools ground truth):
- **Writing a NEW renumbered start bar** (changing the displayed first bar, not just preserving
  it): the read + preserve sides are solved (event-0 `i32`), but authoring a *different* start
  needs PT to confirm what else moves (the empty-meter case, and whether anything outside the
  meter block references it) — see §5c.
- **Cleanliness:** fix `set_tempo_map`'s stale `0x2028` size field (the size-driven readers work
  around it, but real sessions keep it consistent).
- **Empty meter map** (`count==0`, e.g. COGNAC/MANOLITO): the i32 field only exists when
  there's ≥1 meter event. Renumber such a session in PT and find where the value lands
  (a forced `count=1` event, or a session-setup block like `0x2305`/`0x230A`?).
- The exact tick↔bar conversion once a session is renumbered (does anything else move?).

Why it matters: lets us place a musical downbeat on **Bar 1** with pickups in bars
`0 / -1 / …` natively (the proper fix for the beatmap-importer count-in approximation),
and round-trips renumbered sessions losslessly.

## Placement → region link — SOLVED (2026-06-30)

The `0x104F` placement's `u32` at **+11** is a **0-based index into the `0x2629`
region-instance list** (file order); the clip's source name is the `0x2628` name nested in
that `0x2629`. Corpus-confirmed: the bak.076 added clips resolve to the added regions, and a
`Wolf Wet` track's clips resolve to `Wolf Wet.L/.R`. Refs are **positional** (an insert
before index N renumbers refs ≥ N — load-bearing for add-clip). Shipped:
`body_synth.clip_names(data, lane_zmark)`. See spec §5c.

## 0x2628 region geometry — DESCRIPTOR MODEL FOUND, slot-meaning needs PT

Round-2: after the name (`noff = z+13+namelen`) a 5-byte descriptor `[b0..b4]` precedes
variable-width LE **sample** fields whose byte-widths are the nibbles of `b1,b2,b3` (slot
order `[b1.hi,b1.lo,b2.hi,b2.lo,b3.hi,b3.lo]`, 0 = absent); `b4` = form marker (`0x08`
trimmed / `0x00` whole-file). The model reproduces exactly on clean families (final field =
clip length in samples) — but the slot→meaning map is NOT stable across subtypes (grouped
`.grp` regions + some `0x1004` record types break it), and "final field == source length"
was refuted as universal. Positions are raw samples, NOT origin-relative ticks. To finish:
author PT sessions with known clip start/length (whole-file vs trimmed sub-region, and a
grouped region) and pin each slot. Gate for add/replace-clip. (See spec §5d.)

## 0x104F placement position — SOLVED; second variant tail open

Round-2 confirmed: position is the `u64` at `+16` (already shipped as `clip_positions` /
`set_clip_position`); sample-track = plain samples, tick-track =
`0x4000000000000000 + 0xE8D4A51000 + tickoff` with the timebase in the top byte at `+23`
(`0x00`=sample, `0x40`=tick, other nibbles opaque). `+11` = the `0x2629` region index.
Remaining (needs PT): the size-37 / subtype-`0x000A` variant's 3 extra tail bytes, and the
per-clip enum at `+24` (1/2/3) — author clips with/without a fade or sync point and diff.

## Automation `0x260A` — re-derive (variable breakpoint array)

Round-2 refuted the "fixed 39-byte single breakpoint" model: `0x260A` is VARIABLE-length and
holds an inner breakpoint **array** (count near payload+10; ~6 B/breakpoint + 6-byte
terminator). `flag@payload+8` ⇄ tick-present is exact; `tick@payload+18` = 5-byte absolute
conductor tick. Re-derive the per-breakpoint (tick, value) record and the lane→parameter
(volume/pan/mute/plugin) linkage. Lower priority — automation is in the "leave intact" set
for clip/track edits, but needed for an automation editor.

## Track GUID / 0x260E routing — needs PT

`0x261B → 0x102D` carries an 8-byte track GUID (after a `2A 00 00 00` tag) and cross-track
links are GUID-keyed; `0x260E` destination names are length-prefixed latin1 but not at
block end, and some slots (record-id `0xFFFF`) are unassigned. Confirm in PT which track-list
block holds each track type's GUID (a GUID showed up in a `0x251A`-family block for an audio
track) and which `0x260D` routing slots mean "unassigned". Groundwork for add/remove-track.

## add / remove-track — scoped (the next big build)

Ground truth exists in the chains: Rewire bak.078→079 ADDS a track (Δ0x261B +1), Hipsters
bak.044→045 REMOVES one (Δ0x261B −1, Δ0x1014 −1). Footprint per spec §5e. The crux vs.
clip edits: a track's blocks are **indexed** (0x1014/0x251A track-list entry, 0x1052 lanes,
0x261C playlists, the 0x261B subtree), so the reindex is a **rank-rebuild** of the master
index (remove/renumber the records for the removed track's blocks) — NOT the pure
offset-shift that clip edits use. Also the lanes of ALL audio tracks share ONE 0x1054
container (Stanaj: one 6.7KB 0x1054 holds all 16 lanes), so remove-track splices lanes out
of the shared container rather than dropping a per-track container. And the 0x251A track
list appears DUPLICATED (two copies in Stanaj).

PRECISE FOOTPRINT (ground truth: Hipsters bak.044→045 removes the EMPTY track 'GTR 13.dup2';
the simultaneous +7-clip add is separable via positive deltas). An empty-track removal
deletes **28 content-types / ~70 blocks**: the 0x261B subtree (0x261b, 0x102d, 0x2619,
0x2627, 0x260d, 0x260a×3, 0x260c×2, 0x260e×3, 0x1029, 0x2434, the view chain 0x2015/0x200a/
0x200b/0x2037×3/0x2038×3, the plugin cluster 0x1038×2/0x2613×2/0x2614×2/0x2615×2/0x2616×2),
the scaffolding (0x2506×13, 0x2625×9, 0x2626×11), the track-list entries (0x1014, 0x251a×2),
the playlist 0x261c, and the empty lane 0x1052×1. Of these, only **6 are INDEXED** (referenced
by master-index child_refs OR element offsets): **0x1014, 0x251a, 0x1052, 0x261b, 0x261c,
0x2627**. So the reindex = offset-shift for the 22 non-indexed types + a bounded RANK-REBUILD
(remove the index refs + renumber) for those 6. Plus editing the shared name table 0x2519
(the name is inside it, not a separate block) and the display-order 0x2624.

Sub-problems: (a) IDENTIFY one track's complete block-set — name-bearing blocks by name, the
0x261B subtree by containment, but the scaffolding (0x2506 per marker×track, 0x2625/0x2626
under the track's 0x2627) needs per-track attribution; (b) build the index rank-rebuild for
the 6 indexed types (remove a child_ref = splice 11 B + decrement the record count; remove an
element offset = splice 4 B + decrement k; then offset-shift). Validate: reproduce bak.045's
structure (track gone, other tracks byte-intact) + holes resolve + rc=0. Start with the
empty-track case (no clips/lanes-with-clips), then generalize to tracks with clips.

## Track edits — EOS bug FIXED, PT re-confirmation pending (2026-07-01)

PT verification (empty-track sessions): **verify_1 (replace_tempo_map/replace_meter_map) = PT
VALID** ✓. **remove_track + duplicate_track = "end of stream encountered"** (PT-rejected
despite rc=0). ROOT CAUSE: a *count-prefixed container* stores a `u32` child-count at
payload+0 and PT reads exactly that many children; the track edits changed the containers'
children but not the count → PT reads past the container → EOS. Our size-driven reader
tolerates the stale count (rc=0 ≠ PT-valid). FIXED: `_fix_container_counts` (called by both
track edits) rewrites the counts of `0x1015`(→`0x1014`), `0x1054`(→`0x1052`), `0x2624`(→total
per-track subtrees); idempotent on all 26 corpus + round-trip preserved. NEW TOOL:
`body_synth.validate(data)` = pre-write EOS gate replicating PT's container read (spec §14.6).
NEXT: user to re-test the regenerated verify_2/verify_3 in PT; if still failing, hunt the next
count-container / structural invariant (validate() is extensible — add the container type +
its tallied child type). Broaden validate() beyond the 3 track containers as more are confirmed.

## Track-edit EOS — the `0x2519` name-table fault FOUND + FIXED via a stream-walk validator (2026-07-01)

The count fix (above) was necessary but NOT sufficient — PT still EOS'd. Built a count-driven
stream-walk validator matching PT's read semantics in `ptxformatwriter/eos_validator.py` (clean,
format-terms only). It reproduced the real fault: the `0x2519` name table's INLINE name-entry list (each entry
`u32 len | name | 23-byte trailer` with a `0x2A` marker at trailer+6) was left MISFRAMED —
`remove_track` deleted only `len|name` (orphaned the trailer), `duplicate_track` inserted a
trailer-less copy. The block size + framed `0x251A` children were adjusted, so the size-driven
reader passed (rc=0) but PT read past the object. FIXED: both edits now splice/insert the WHOLE
entry (`+_NAME_ENTRY_SUFFIX`). `body_synth.validate()` now runs the ported `eos_validator.simulate`
(name table + `0x2624` container) + the count check; sound 0/26 corpus, reproduces the pre-fix EOS,
and both edits + round-trip are clean. PENDING: user re-tests the regenerated verify_2/verify_3 in
PT (third time). The validator is a real oracle now; extend `eos_validator` with more object grammars
(index records, per-track objects) as future edits need them.

## Track edits — DONE (PT-confirmed) + duplicate byte-identical (2026-07-01)

The EOS saga resolved: the real root cause was the **master index**, not the body — the
byte-identity check had been skipping the trailing `0x0002` region the whole time. `remove_track`
(last AND middle) now opens+closes clean in PT (`verify_3/4/5`); the fix drops dead index records
+ re-ranks per-track ordinals (`final_index.rebuild_after_track_drop`). `duplicate_track` crashed
on close from a **channel collision** (the copy inherited the source's channel indices); fixed by a
per-track BODY positional-renumber (`body_synth._rekey_positional_by_slot` + free-field slide), so a
duplicate is now **whole-file byte-identical to a fresh valid synth** with the track in position
(`verify_2` PT re-test pending). Spec §12b documents the positional fields. Remaining: generalize
the duplicate renumber beyond all-stereo synth (mono/mixed/real sessions) — the offsets are
currently stereo-synth-specific.

## Format documentation — COMPLETE: 100% content-type coverage (2026-07-01)

Every **real** content-type in the 26-session corpus (280/280) is documented — spec §16 extended
catalog (4 dissect→verify→draft batches) + §12b + the pre-existing §1–15. Spec §3 documents the
`0x5A`-in-data **phantom** caveat (25 bogus "types" the naive size-walk manufactures inside leaf
payloads/the file header — a correct parser recurses by grammar, not raw `0x5A` scan).
`docs/content-type-coverage.md` is the live ✓/phantom/undocumented checklist. Reimplementation
fidelity: full-file load→re-encrypt 26/26 byte-exact, index `serialize(parse)` 26/26, all readers
26/26, 161 tests green.

## Insert → plugin-TYPE linkage — OPEN (needs more RE)

Confirmed NEGATIVE (2026-07-02): the `0x2611` insert's `insert_ID` (u32 at the `0x2036`
child payload +12) is a **unique per-insert-instance ID** (La Cumparsita: 11 inserts →
IDs 1,3,4,5,6,7,8,9,10,11,12 — one each, gaps from deleted inserts), **NOT** an index into
the `0x1018→0x1017` plugin catalog (only 6 catalog entries there). So which plugin *type* an
insert instantiates is keyed by something else — most likely the instance GUID matching a
catalog/`0x2064` `.tfx`-settings entry, or the settings path. Groundwork for a "list plugins
per track" reader and a plugin-preserving track duplicate on real sessions. Not on the current
critical path (automation/plugins are in the "leave intact" set for track/clip edits).

## Track-edit renumber — MONO + MIXED generalized (2026-07-02, committed 3b5b2e5)

The `_all_stereo` gate is gone: the body renumber is now channel-count-aware and covers
mono/stereo/mixed. Each `0x1014` carries its own channel count at `ne+1`; channels =
`base..base+nchan-1` with `base` the cumulative channel sum, written at per-kind sites
(`site2_off = 28 + 2*nchan`). Reduces exactly to the old stereo constants, so the PT-confirmed
stereo path is byte-identical. Mono `0x261C` free-field offsets are stereo−39 with u16 (not u32)
C1/C3/C4; mono grow-base `10*k+18871`. Guard `_is_synth_shaped` restricts the fixed-offset
renumber to pristine synths (real sessions keep the safe splice path), with an O(blocks) cheap
reject on `0x104F/0x2628/0x2611/0x2064` + an lru_cached reference build.

PENDING PT: `verify_6_mono_duplicate.ptx`, `verify_7_mixed_dup_stereo.ptx`,
`verify_8_mixed_dup_mono.ptx` (all byte-identical to their synth references).

STILL OPEN: duplicate_track on a REAL session (clips/plugins). The renumber's offsets are
synth-layout-specific, so real sessions currently fall through to the no-renumber path — fine for
`remove_track` (removal only leaves a channel gap) but a real-session DUPLICATE would collide on
channels. Needs grammar-driven field location (find the channel sites structurally in arbitrary
block layouts) + PT verification. Also re-run the corpus remove_track eos+rc regression when
`/Volumes/Backups/AI/__PTX corpus` is mounted.

## Real-session duplicate — CHANNEL COLLISION FIXED (2026-07-02)

`duplicate_track` on a NOT-synth-shaped session (the path every real session takes) spliced the
copy from the source and left it carrying the **source's channel indices** — reproduced without
the corpus using a MIDI-bearing session (which fails `_is_synth_shaped`): the copy came out with
channels `[1,2]`, identical to its source. That is exactly the collision that crashed PT on close
before the stereo fix, so real-session duplicates were broken.

FIXED structurally (no synth offsets): `_assign_free_channels` reads every `0x1014` via
`_track_1014_rows` and hands the copy the lowest channel indices no other track uses, writing them
with the pre-existing `set_track_channels`, which locates the fields by the name length + the
`0x2a` marker (`marker+19`/`marker+23`) and already handles mono vs stereo. Channels need only be
UNIQUE, not contiguous — `remove_track` already leaves gaps PT accepts. The stored `0x1054+2`
channel-count field is maintained by the splice already (verified 8→10, 16→18).

Verified: non-synth duplicate of a stereo source → copy `[8,9]`, of a mono source → `[8]`, zero
collisions, eos+rc clean, `validate()` empty; all synth byte-identity unchanged (stereo/mono/mixed
still == their fresh-synth references). Test: `NonSynthDuplicateChannelTests`.
PENDING PT: `verify_9_nonsynth_duplicate.ptx`.
STILL OPEN: verify on a genuine corpus session with clips/plugins (needs the corpus volume
mounted) — the fix is layout-independent, but a real session is the honest test.

## remove_track OVER-DELETES on non-synth sessions — DATA LOSS (2026-07-02, OPEN, top priority)

Found by sweeping ALL 695 local fixtures (`control_files/` + `generated/`, 284 with real clips)
instead of a hand-picked few — the earlier "4/4 real clip-bearing sessions pass" was a 4-session
sample and did NOT generalize.

THE ORACLE: `duplicate_track` then `remove_track(copy)` must be a perfect inverse — the copy's
block-set is exactly what the duplicate added, so remove must delete exactly that. On a non-synth
session (`mixed_no_click_probe_2624_full_final_v1.ptx`, 3 tracks) it is not:

| type   | added by dup | deleted by remove | over-deleted |
|--------|--------------|-------------------|--------------|
| 0x2037 | 8            | 16                | 8            |
| 0x203a | 0            | 8                 | 8            |
| 0x2580 | 1            | 2                 | 1            |
| 0x2589 | 0            | 1                 | 1            |

The round-trip loses **268 bytes / 18 blocks** of OTHER tracks' view state. `0x203a`/`0x2589` are
the clearest: the duplicate added NONE, yet remove deleted 8 and 1 — those belonged to other
tracks. So duplicate and remove DISAGREE about which blocks constitute a track once a session
isn't synth-shaped.

WHY IT HID: `eos`/`rc`/`validate()` all PASS on the damaged result — the session still loads. This
is silent data loss, which is exactly why "corpus remove 6/6 clean" and the PT-confirmed
verify_3/4 looked reassuring: the synth path IS a perfect inverse (byte-identical), so the bug
only shows on real sessions.

TO FIX: per-track block attribution for the view/scaffolding families (0x2037/0x203a/0x2580/
0x2589) on arbitrary layouts — the synth path attributes 0x2589 POSITIONALLY (i-th 0x2589 = i-th
playlist), which is what likely over-matches when a real session's view chains don't line up 1:1
with tracks. Gate any real-session remove/duplicate on this. The round-trip block-delta check
above is the regression oracle.

## remove_track on a 1-track session — FIXED (2026-07-02)

Every one of the 77 sweep remove-failures was removing the session's ONLY track: the splice
emitted a file PT rejects. `remove_track` now raises ValueError instead (verified: 109 such
fixtures raise, 0 emit a file). Removal is otherwise clean across 2..512 tracks (153 sessions,
incl. 14 of the 512-track ones).

## 0x2589 view-chain over-deletion — ROOT-CAUSED + FIXED for real removes (2026-07-02)

The silent data loss above is root-caused. `remove_track` attributed the SECOND view-chain
(`0x2589`) by **zmark rank** (`s2589[rank]`), but those blocks are **not stored in display
order** — a real session's zmark order reads `[2, 0, 1, 3]`. Synth sessions happen to have
zmark order == ordinal order, which is exactly why the synth path (and the PT-confirmed
verify_3/4) looked perfect while real sessions broke.

Each `0x2589` **nests** its children, so one wrong pick cascaded precisely:

| type   | over-deleted | children of the wrongly-picked 0x2589 |
|--------|--------------|----------------------------------------|
| 0x203a | 8            | 8                                      |
| 0x2037 | 8            | 8                                      |
| 0x2580 | 1            | 1                                      |
| 0x2589 | 1            | the block itself                       |

FIXED: attribute by the block's OWN display-row ordinal (`u16 @ zmark+9`), and only when the
ordinals form a clean `0..N-1` map onto the tracks. A session whose chains do not correspond 1:1
(e.g. 4 chains / 3 tracks) now gets none removed — a stale extra view chain is harmless (PT
recomputes view state); deleting a live one is not. The old `len(s2589) == len(playlists)` guard
was a weak proxy that could pass by coincidence and then delete the wrong block.

Verified: on the failing fixture a real `remove_track` now leaves `0x2589` at 4 and `0x203a` at
16 (previously 3 and 8) — no surviving track loses its view state. Synth paths unchanged
(stereo/mono dup == synth, remove-last == synth8, round-trips byte-exact).

STILL OPEN — `duplicate_track` UNDER-ADDS the view chain on non-synth sessions: it adds no
`0x2589` for the copy and does not shift the survivors' ordinals, so the dup output is
structurally incomplete and a dup->remove round-trip is still not byte-exact there (the stale
`0..N-1` ordinals let the guard match a pre-existing chain). Fixing it means copying the source's
`0x2589` subtree for the copy + re-keying ordinals on arbitrary layouts. The dup->remove
block-delta check is the regression oracle. Real (unedited) removes are data-safe today.


## parse_records SILENTLY TRUNCATED the master index — guarded (2026-07-02)

Found by classifying the duplicate round-trip failures instead of accepting "eos+rc pass". On a
512-track fixture the BODY round-tripped perfectly (1369269 -> 1369269) while the INDEX collapsed
103847 -> 36796 bytes:

```
declared record_count @payload+0 = 1183
parse_records returned           = 110      <- stopped early, no error
serialize_final_block wrote      = 110      <- and discarded 1073 records
```

`parse_records` `break`s out of its walk on the first record it cannot frame and returns the short
list; the caller then serializes a truncated index, gutting the master block table. eos, rc AND
`validate()` ALL PASS on the result — our reader size-walks the body and never needs the index, so
nothing we had was looking at it. Both `remove_track` and `duplicate_track` did this.

GUARDED: `parse_records` now RAISES when it frames fewer than the declared `record_count`, so an
edit refuses instead of quietly destroying the file. Verified: the 512-track fixture's remove AND
duplicate both refuse; synth paths unaffected (stereo/mono dup == synth, remove-last == synth8,
round-trips byte-exact).

SCOPE (do not overstate): **no PT-authored session is affected** — all 47 `control_files` parse, as
did all 26 real corpus sessions when last checked. All 164 refusals are `generated/` artifacts, our
own older-writer/RE-experiment outputs (85 of them the 512-track `milestone_7`/`synth_audio_512`
family). So the silent-truncation bug bit only sessions whose index we cannot frame, and in this
corpus that set is exclusively our own artifacts.

STILL OPEN: WHY the walk stumbles at record 110 on those indexes. Until that is understood, edits
on such sessions refuse (correct) rather than work. The guard is data-safety, not a fix.

LESSON (the recurring one): eos/rc/validate() never inspected index completeness, so "remove
155/155 clean" was measured with instruments blind to this failure. Check the artifact you claim
is intact, not a proxy for it.

### ...and the parse limitation is NOT a live bug (2026-07-02)

Settled by asking whether our CURRENT writer produces indexes we can re-parse. It does, at every
size tested — `synthesize_stereo_inline` N=64/128/256/400 → declared==parsed (287/415/671/959) and
`serialize(parse(idx)) == idx` **EXACT** in all four. So the truncation guard fires on neither
PT-authored sessions (47/47 control_files, 26/26 real corpus) nor anything we write today; the
164 refusals are legacy `generated/` artifacts from OLDER writer versions. The guard is a safety
net, not a workaround for a live defect, and "why record 110" is archaeology on dead files rather
than a blocker. (Cost note: N=400 synthesis takes ~150s.)

## Post-guard sweep — where the editors actually stand (2026-07-02)

Full sweep over all 695 local fixtures, AFTER the five fixes + the index-truncation guard:

| operation | ok | fail | refused (ValueError) |
|---|---|---|---|
| remove_track | 140 | **0** | 240 |
| duplicate_track | 98 | 45 | 237 |

`remove_track` is clean on every session it accepts (2..512 tracks, incl. clip-bearing), and refuses
the rest (1-track sessions; unparseable legacy indexes) rather than corrupting them. Earlier
"lossy" duplicates are GONE: the 31 that shrank 512-track sessions by 67KB were the parse_records
truncation and now refuse (dup_FAIL_clips 27 -> 12, lossy 31 -> 0).

REMAINING GAP (minor, documented, not fixed): all 45 duplicate "failures" are
`col=0 eos=True rc=True rt=False` — the session is valid; only the dup->remove round-trip is not
byte-exact. Diagnosed to **8 bytes**: within ONE 0x2624 record the element offsets come back
rotated (same SET), and two playlist-instance ordinals return decremented (2->1, 3->2). The BODY is
byte-identical and the functional playlist order round-trips EXACTLY (verified via
`track_playlist_order`, incl. a custom `A1,M1,A2` order, where duplicate correctly yields
`A1,AZ,M1,A2`). Cause: `canonicalize_after_track_add` and `rebuild_after_track_drop` are exact
inverses on the synth path (byte-identical) but not quite on non-synth records. Impact looks nil
(order preserved, validators clean, no data loss) but is NOT proven harmless.

NOTE — a hypothesis I had and REFUTED: that canonicalize's body-rank sort scrambles custom display
order. It does not; the order round-trips identically. Test before claiming.

## The duplicate round-trip residual — RESOLVED (2026-07-02)

Two genuine inverse defects, both fixed (commit 26fc542); `canonicalize_after_track_add` was not
the inverse of `rebuild_after_track_drop`:

1. ORDINALS — canonicalize re-assigned lane/playlist ordinals 1..N sorted by BODY RANK while the
   drop decrements relative to the REMOVED ordinal. Inverses only when ordinal order == body-rank
   order (synth). On a real session duplicate rewrote every ordinal and the round-trip returned
   off-by-one (2->1, 3->2). Now MIRRORS the drop: the copy takes source.ordinal+1, higher ordinals
   shift up. Reduces to the same 1..N+1 on the synth path.
2. ELEMENT ORDER — canonicalize sorted container track-elements by body rank, but the doubling
   already splices the copy's offset beside its source's. The sort was a no-op on synth and
   ROTATED a real session's 0x2624 element list. Dropped.

Non-synth round-trip: 8 -> 6 -> **0 bytes**. Sweep: duplicate ok 98 -> **112**, fail 45 -> 31.
The PT-confirmed synth path stayed byte-identical at every step (that oracle is what made
refactoring it safe).

The remaining 31 are NOT defects:
- 15 BENIGN — every body block byte-identical; only index representation differs.
- 16 are our code CORRECTING a pre-broken fixture. `0x258A`'s count == its 0x2589 child count in
  EVERY PT-authored control and synth (midi9 9/9, mixedmidi_4/5/6/9, synth stereo+mono 8/8), i.e.
  the model is right; the failing fixtures store a STALE count (nameblock_v3: stored=4 with 7
  children) and `_fix_container_counts` repairs it to 7. The round-trip is inexact because the
  ORIGINAL is wrong, not the edit.

NOTE — two hypotheses I formed and REFUTED here: that canonicalize scrambles custom display order
(it does not; order round-trips identically), and that this residual was "cosmetic polish" (it was
two real inverse bugs). Diagnose before dismissing, and before alarming.

## Remaining duplicate round-trip differences — classified, 2 open questions (2026-07-02)

After the three inverse fixes (26fc542 + 3706786) the sweep reads duplicate **115 ok / 28 fail**,
remove **140 / 0**. The 28 are NOT one bucket — by shape:

| n | shape | verdict |
|---|---|---|
| 10 | `body: 0x2551/0x2587/0x258a` | our code REPAIRING a stale `0x258A` count (see below) — correct |
| 10 | `index-only: 7B` | open |
| 5 | `body: 0x202a/0x202b` | the `<ALL>` count-convention question (below) |
| 2 | `index-only: 5B/6B` | open |
| 1 | `body: 0x1000/0x1004/0x1006` | open (synth_stereo_click) |

**`0x258A` (10 cases) — our code is right.** Its count == its `0x2589` child count in EVERY
PT-authored control and synth (midi9 9/9, mixedmidi_4/5/6/9, synth stereo+mono 8/8). The failing
fixtures store a STALE count (`nameblock_v3`: stored=4 with 7 children) and `_fix_container_counts`
repairs it to 7. The round-trip is inexact because the ORIGINAL is wrong.

**`<ALL>` group lists (5 cases) — OPEN: two count conventions.** Our code is provably SYMMETRIC on
the synth path (`<ALL>` members 8 -> 9 -> 8 across duplicate/remove). But `mixedmidi_click_bottom`
stores `<ALL>` count=6 while holding only 3 ordinals before the `0xFFFE` delimiter, so the stored
count disagrees with its own content and `_fix_all_ordinal_counts` recomputes it to 3. This is the
SAME unresolved question as `0x1015`: that fixture is one of the four where `0x1015` count (3) ==
AUDIO tracks while `midi9.ptx` count (9) == ALL tracks. Two conventions coexist in PT-authored
files and we have not pinned which rule selects them. Until that is known, DO NOT invent a third
model — the `0x1015` precedent (`_adjust_track_count`: adjust by the edit's delta, never recompute)
is the safe pattern to extend here.

NOTE: every time a residual was labelled "benign"/"cosmetic" this session it was hiding real bugs
(the 8-byte residual = two inverse defects; the "benign 15" = the 0x2519 lane-offset swap). The
label was doing the work the diagnosis should have. These 28 are classified by measured shape, not
adjective.

## Track edits — final state after 12 fixes (2026-07-02)

| operation | ok | fail | refused |
|---|---|---|---|
| remove_track | 140 | **0** | 240 (1-track sessions; unparseable legacy indexes) |
| duplicate_track | **128** | 15 | 237 |
| index serializer | **243 EXACT** | 0 MISMATCH | — |

170 tests. PT-confirmed synth path byte-identical through every fix.

EVERY remaining failure is diagnosed — none unexplained:
- **10x** `body:0x2551/0x2587/0x258a` — our code REPAIRING a stale `0x258A` count. Verified correct:
  `0x258A count == its 0x2589 child count` in EVERY PT-authored control and synth (midi9 9/9,
  mixedmidi_4/5/6/9, synth stereo+mono 8/8). The fixtures store a stale value (`nameblock_v3`:
  4 with 7 children) and `_fix_container_counts` repairs it to 7. The round-trip is inexact because
  the ORIGINAL is wrong, not the edit.
- **5x** `body:0x202a/0x202b` — the `<ALL>` count convention. PT-GATED (below).

### THE one open format question (needs Pro Tools, not more analysis)

Two count conventions coexist in PT-AUTHORED files and no rule in the bytes selects them:
- `midi9.ptx`      : `0x1015` = 9 == ALL tracks (9 MIDI, ZERO 0x1014 blocks)
- `mixedmidi_click_bottom.ptx`: `0x1015` = 3 == AUDIO tracks only (5 tracks: 3 audio + 2 MIDI)

The same split governs the `<ALL>` group's stored count (that fixture holds 6 with 3 ordinals before
the 0xFFFE delimiter). Our code is provably SYMMETRIC on the synth path (`<ALL>` 8 -> 9 -> 8), and
`_adjust_track_count` already refuses to recompute what we cannot pin down.

GROUND-TRUTH PROBE that settles it: open `control_files/mixedmidi_click_bottom.ptx` in Pro Tools,
add one track, save, and diff. Which of `0x1015` / the `<ALL>` count moves (and by how much) tells us
which convention PT itself follows. DO NOT guess a fourth model -- bug #9 proved a byte-perfect
round-trip can ship a corrupt file (dup produced `<ALL>`=[0,1,1], a duplicate ordinal, while every
check reported clean).

### The recurring defect (six instances, one disease)

`0x1015` = 0x1014 child count | fixed-count `0x2519` ordinal = `new_track_count` | `<ALL>` insert
index = `len(track_types)` | "last entry" = `track_types[-1]` | element tail = six zeros (parse) |
element tail = six zeros (rebuild). Each asks "what SHOULD this be?" instead of "what does the file
say?". The rule was written at the first one; the cost was skipping the sweep that finds the rest.

## CORRECTION — the count "convention" question is RESOLVED, and was never PT-gated (2026-07-02)

I claimed two count conventions coexist in PT-authored files and that only a Pro Tools probe could
settle it. That was wrong: the evidence was in the bytes.

`0x1015` counts **ALL TRACKS**. One convention, not two:

- 43 of 47 control fixtures: `0x1015 == len(track_types)` — the ALL convention.
- `midi9.ptx` is decisive: 9 MIDI tracks, **ZERO 0x1014 blocks**, `0x1015 = 9`. A session with no
  0x1014 children stores 9, so the count CANNOT be the 0x1014 child count.
- The 4 "AUDIO" outliers are **files WE corrupted**: `mixedmidi_click_{bottom,middle,top}` and
  `mixedmidi_reorder_SMm` — all click-splice / reorder outputs, i.e. the tools that ran the OLD
  `_fix_container_counts`. Every one has `0x1015 == n_1014` EXACTLY: the precise signature of bug #4
  (tallying 0x1014 children, which under-counts because MIDI tracks have none). Not a second
  convention — our own damage, still sitting in the fixtures.

CONSEQUENCE: none of the remaining duplicate "failures" are defects. All 15 are our code handling
pre-broken fixtures — 10 repairing a stale `0x258A` count, 5 involving these 4 self-corrupted
`<ALL>`/`0x1015` files. The reimplementation has no known open correctness question from the bytes.

`_adjust_track_count` (adjust by delta, never recompute) remains the right behaviour even now that
the convention is known: on a VALID file a delta keeps a correct count correct; on a BROKEN file it
preserves the damage rather than compounding it. Recomputing to `len(track_types)` would silently
rewrite those four fixtures.

LESSON: "this needs external ground truth" was itself a premature conclusion. The outliers' shared
signature — and the one control with zero 0x1014 blocks — answered it. Check whether the anomaly is
the format or your own past output before declaring a question unanswerable.

## CORRECTION #2 — the 5 `<ALL>` cases ARE a live defect, not "our code repairing fixtures"

I claimed the 15 remaining duplicate failures were all benign (10 repairing stale `0x258A` counts,
5 handling files bug #4 corrupted). **The second half is refuted.** Testing the claim instead of
asserting it: repairing the `0x1015` damage in memory does NOT fix the round-trip, and
`mixedmidi_reorder_SMm` was never failing.

```
mixedmidi_click_bottom  0x1015 3->5  round-trip: corrupted=False  repaired=False
mixedmidi_reorder_SMm   0x1015 2->3  round-trip: corrupted=True   repaired=True   (never failed)
```

THE REAL DEFECT (`mixedmidi_click_{bottom,middle,top}`), 3 audio + 2 MIDI + a click:

```
name-table: ['Audio 1','MIDI 1','Audio 2','Audio 3','MIDI 2','Click 1']   (6 entries)
orig  <ALL> stored count=6, but only 3 ordinals [0,1,2] precede the 0xFFFE delimiter
dup   <ALL> count=3 ords=[0,1,2]    <- 4 audio tracks after the dup; should be [0,1,2,3]
back  <ALL> count=2 ords=[0,1]      <- should be [0,1,2]
```

The `<ALL>` group holds the AUDIO tracks (3), while its stored count (6) matches the NAME-TABLE
entry count. So the stored count and the ordinal list disagree in a PT-authored file, and:
- `_all_ordinal_lists` derives the member span FROM the stored count, so it reads 6 u16s
  ([0,1,2,0xFFFE,0x8010,1]) -- swallowing the delimiter and trailer as "members";
- bug #9's fix (`next_index = the list's own member count`) therefore reads 6 and inserts the wrong
  index, so the copy never gets a correct `<ALL>` ordinal;
- `_fix_all_ordinal_counts` then recomputes the count to 3 (counting to the delimiter), rewriting a
  PT-authored value.

WHAT IT NEEDS: pin what the `<ALL>` stored count actually counts when it disagrees with the
delimiter-terminated list (name-table entries? a different group?), then make insert/drop adjust by
the edit's delta rather than recompute -- the `_adjust_track_count` pattern, which is the seventh
place this same disease appears.

LESSON (again, and the sharpest one): "these are not defects" was an explanation I found
satisfying and did not test. The test refuted it in one command. An untested exculpatory story is
worth exactly as much as an untested bug report.

## Bug #14 FIXED, and the `<ALL>` defect is now precisely characterized (still OPEN)

### FIXED (committed): the 0x2519 name-entry stride is not a constant
An entry's ordinal is a u32 in almost every entry but a u16 in some -> stride 23 or 21. Only 2 of
4362 corpus entries are short, both in `robust_audio_mixed12` (an unedited PT session), at entry
[1], mid-table -- so a hardcoded 23 survived a long time and then did real damage: the walk
reported that 12-entry table as 2, and `_fix_name_table_count` rewrote PT's header count 12 -> 2.
PT bounds its inline read by that count. Editing that session at all would have stranded PT
reading 2 of 12 tracks, silently.

Fix: `_next_name_entry` takes the stride the bytes support; `_fix_name_table_count` walks via
`_inline_name_entries` instead of keeping a second copy of the walk (the duplicate is how it
inherited the bug). Oracle: PT's own stored count. Our recompute now agrees on ALL 282 corpus
sessions (0 rewrites, was 2). 171 tests OK incl. the PT-confirmed byte-identical synth oracles.

### STILL OPEN: the `<ALL>` list on click-bearing sessions
Two independent probes converged on the SAME 8 files -- every one click-bearing:

  - the `<ALL>` u16 count equals the NAME-TABLE ENTRY count, not the member count
    (tally across 282 files: matches-both 272, entry-count-only 8, member-count-only 0 once
    bug #14's under-read is fixed);
  - and those same 8 are exactly the 8 duplicate->remove round-trip failures.

  mixedmidi_click_{bottom,middle,top}   entries=6  stored=6  members=3
  test_clickbottom_3stereo              entries=4  stored=4  members=3
  test_clickbottom_12stereo             entries=13 stored=13 members=3
  test_F2_12stereo_clicktop             entries=13 stored=13 members=3

So `_all_ordinal_lists`'s model -- "count == member count, ordinals are positional identity" --
is wrong. It derives `ords_end` FROM the stored count, so on these files it reads 6 u16s
([0,1,2,0xFFFE,0x8010,1]), swallowing the `fe ff` delimiter and trailer as members. Downstream:
bug #9's `next_index = the list's own member count` then reads 6 and inserts at an offset past
the real list end, so the copy never gets an `<ALL>` ordinal; `_fix_all_ordinal_counts`
recomputes the count to 3 and overwrites a PT-authored 6. The duplicate does not insert, but the
remove DOES drop -- that asymmetry, not the count alone, is what breaks the round-trip.

WHAT IS NOT YET EXPLAINED (do not ship a story here -- I already shipped one and it was refuted):
members=3 on BOTH a 3-audio and a 12-audio click session. 3 is suspiciously constant, so
"members = the audio tracks" is ALSO wrong. Either `ords_start` shifts on click sessions (the
0x2519 preamble has exactly such a flag-selected shift, so there is precedent) or the list is
not what we think. NEXT: dump the raw `<ALL>` region on a click vs non-click session of the same
track count and read the layout off the bytes before touching any code.

LESSON (eighth instance of the one disease): every count we RECOMPUTE from a model is a place we
overwrite ground truth. `_adjust_track_count` adjusts by the edit's delta and is right;
`_fix_all_ordinal_counts` and `_fix_name_table_count` recompute, and both were caught corrupting
PT-authored values. Prefer adjust-by-delta over recompute-from-model.

### The `<ALL>` raw bytes (read them first -- this is the evidence, not a theory)

Same track count, click vs no click, from `_all_ordinal_lists`'s `<ALL>` marker:

```
test_A_12allstereo_mixedpath (12 stereo, NO click)   count field = 12
  3c 41 4c 4c 3e | 02 ff ff ff ff 00 00 | 0c 00 | 00 00 |
  <ALL>            7 fixed                count    pad
  00 00 01 00 02 00 03 00 04 00 05 00 06 00 07 00 08 00 09 00 0a 00 0b 00 | fe ff | 10 80
  ordinals 0..11  (12 = count = tracks)                                     term     trailer

test_clickbottom_12stereo (12 stereo + click)        count field = 13
  3c 41 4c 4c 3e | 02 ff ff ff ff 00 00 | 0d 00 | 00 00 |
  00 00 01 00 02 00 | fe ff | 10 80 | 01 00 00 00 00 00 | ff ff | 08 00 09 00 0a 00 0b 00 | fe ff
  ordinals 0,1,2      term    trailer   ???                ???     ordinals 8,9,10,11        term
```

WHAT THIS SETTLES: the member list is SEGMENTED, not a flat run. The click session carries runs
[0,1,2] AND [8,9,10,11]. Our reader stops at the first `fe ff`, which is why "members=3" looked
suspiciously constant across a 3-audio and a 12-audio click session -- 3 was never a member
count, it was just the length of the FIRST RUN. Both the "count == member count" model and the
"members == the audio tracks" model are dead.

STILL UNKNOWN -- resolve from bytes before writing code:
  - what `fe ff 10 80` means when it appears MID-list (it terminates the list in the non-click
    file and separates runs in the click one);
  - what `01 00 00 00 00 00 ff ff` is between the runs;
  - where ordinals 3..7 are on the click session (12 stereo tracks, runs cover only 0-2 and
    8-11) -- so a run is not simply "a contiguous span of surviving tracks" either;
  - whether the count (13 = entries incl. click) bounds the ordinals, the runs, or neither.

Get a click session with 3 tracks and one with 12 and diff the region byte-for-byte; the run
boundaries should move with the click's display row (bottom/middle/top fixtures exist for
exactly this).

### Two more facts from the bytes (cheap, and they constrain the next attempt)

1. `click_{bottom,middle,top}` have BYTE-IDENTICAL `<ALL>` regions:
   `06 00 | 00 00 | 00 00 01 00 02 00 | fe ff | 10 80 | 01 00 00 00 00 00 | ff ff | 00 00 00 00 | ff ff` then `5a`
   The click occupies a different display row in each, and the bytes do not move. So `<ALL>`
   does NOT encode display order, and those 3 failures are one cause, not three.

2. Compare the tail against the 12-stereo click session:
   3 audio + 2 MIDI + click:   `... ff ff | 00 00 00 00 | ff ff` then `5a` (next framed block)
   12 stereo + click:          `... ff ff | 08 00 09 00 0a 00 0b 00 | fe ff`
   Same leading run [0,1,2] in BOTH -- on a 3-audio session AND a 12-audio one. So the first run
   is not "the audio tracks" either. `fe ff` (0xFFFE), `10 80` (0x8010) and `ff ff` (0xFFFF) all
   behave like sentinels rather than ordinals; `_all_ordinal_lists` treats whichever it hits
   first as the end.

The 0x2519 preamble precedent (two lengths chosen by a flag at payload+0x0A) is the shape to
suspect here: find the field that selects the layout before assuming a run encoding.

## Bug #15 FIXED — the `<ALL>` count, and with it the WHOLE corpus round-trip

`_fix_all_ordinal_counts` now pins each `<ALL>` count to the name-table entry count instead of
recomputing it to the first `fe ff`-terminated run. That was the last thing breaking the
duplicate->remove inverse on click sessions.

  corpus duplicate->remove sweep:  159 ok / 0 FAIL   (was 8 FAIL; 15 at the start of the day)
  tests: 172 OK, synth oracles byte-identical

The 104 ValueError in the sweep are the documented v1 limitation (duplicate requires the new name
to be the same byte length as the source); the 19 no_tracks are non-session fixtures. So EVERY
eligible corpus session now round-trips byte-exact.

### `<ALL>` list grammar (decoded from bytes; see docs/ptx-format-spec.md §12c)
Inside 0x202b, after `<ALL> 02 ff ff ff ff 00 00`:
  count:u16  = name-table ENTRY count (tracks incl. click)   <- NOT the list length
  pad:u16    = 0
  then a sequence of RUNS:
    run      = ordinal:u16 * k
    then     `fe ff` (0xFFFE, run end) `10 80` (0x8010) `<u32=1>` `ff ff` (0xFFFF, separator)
  list ends with a trailing `ff ff`, then the next 5A block.
Non-click sessions have ONE run = [0..N-1] (a flat list == the tracks). Click sessions segment
into multiple runs, e.g. 12-stereo+click -> [0,1,2] then [8,9,10,11].

STILL NOT UNDERSTOOD (does not block editing; matters for full documentation):
  - why the runs split where they do ([0,1,2]+[8,9,10,11] on 12-stereo+click, skipping 3..7);
  - whether a run boundary encodes anything (it does NOT encode display order -- click
    bottom/middle/top are byte-identical);
  - what the `10 80` / `<u32=1>` / trailing `ff ff` sentinels mean semantically.
The editing primitives are inverse WITHOUT this (dup inserts at ords_end, remove strips
ords_end-2, count moves +/-1), so this is a documentation gap, not a correctness one.

## NEXT (editing primitives, in priority order)
  1. Lift the same-length-name constraint on duplicate (variable-length rename) -> unlocks the
     104 ValueError sessions for add-copy-with-arbitrary-name.
  2. replace-clip / add-clip on arbitrary sessions (remove_clip already ships).
  3. tempo/meter map edits on arbitrary sessions.
  4. PT verification of verify_1..verify_11 (user-gated; PT is the only oracle).

## NEW BUG discovered (#16, latent): synth-click path emits a MALFORMED `<ALL>` list

Decisive comparison against PT-AUTHORED click sessions ("1/2/3/4 stereo plus click.ptx"):
PT authors a FLAT list `[0..entries-1]` including the click ordinal:
  1 stereo + click -> count 2 [0,1]     3 stereo + click -> count 4 [0,1,2,3]
Our own SYNTHESIZED click fixtures (test_clickbottom_*, mixedmidi_click_*) instead carry a
SEGMENTED list with embedded 0xFFFE/0x8010/0xFFFF sentinels and missing ordinals:
  test_clickbottom_12stereo -> [0,1,2] .. [8,9,10,11]  (skips 3-7 and the click's ordinal)

PT tolerates it (loads rc=0), which is why it went unnoticed. It is NOT what PT writes.

IMPACT: none on the editing primitives -- verified that on PT-AUTHORED click sessions,
duplicate produces the correct flat range(N+1) (`3 stereo plus click` count 4 [0,1,2,3] ->
count 5 [0,1,2,3,4]), round-trips byte-exact, and validate()==clean. So in-place editing of real
PT sessions is correct. The bug is only in how we SYNTHESIZE click sessions from scratch.

TODO: fix the click-synthesis path to emit `<ALL>` = flat range(entries) incl. the click
ordinal. Regenerate the test_clickbottom_*/mixedmidi_click_* fixtures once fixed. This is the
recurring "reader rc=0 != PT-valid" lesson again -- PT accepting the file did not make it correct.

## SHIPPED — variable-length duplicate_track (commit 6fe20d0)
The same-length-name constraint is gone: a different-length name duplicates under a same-length
placeholder, then rename_track absorbs the size delta. Same-length path byte-identical (synth
oracles untouched). Verified longer AND shorter names on PT-authored + non-synth sessions:
copy carries the real name, validate clean, remove inverts byte-exact. 174 tests.

INHERITED LIMIT: rename_track uses the SCAN parser (parse/flat_blocks), which stumbles on some
MIDI-clip sessions -- variable-length rename round-trips 259/263 corpus sessions; the 4 failures
are MIDI sessions (`bad element tag`, the classic scan-parser undercount). Porting rename_track
to the size-driven walk (_size_driven_blocks) is the robust fix and would also generalize.

## CURRENT FRONTIER (in-place editing on arbitrary sessions)
  1. [DONE 7d3e565] rename_track ported to the size-driven parser -> the 4 MIDI rename failures
     are fixed (corpus variable-length rename 263/263), and a residual bug #14 site in
     eos_validator.check_name_table (fixed 23-byte stride -> false EOS on 21-byte entries) is
     fixed too. Variable-length duplicate now works on 100% of eligible sessions.
  2. Fix bug #16 (synth-click emits a malformed segmented <ALL>; should be flat range(entries)).
  3. Non-audio track duplicate/remove (MIDI/aux 0x261E/bus/master -- different subtree).
  4. add-clip with NEW audio (needs the 0x2628 geometry write; PT-gated).
  5. Replace tempo/meter map on arbitrary sessions (set_*_map still use the scan parser).
  6. PT verification of verify_1..verify_11 (user-gated; PT is the only oracle).

## CONFIRMED — tempo/meter map REPLACE works on arbitrary sessions (278/282)
Corpus sweep: set_tempo_map AND set_meter_map replace -> 278 ok / 4 ValueError, each result
reading back the new map AND validating clean. The memory's "set_*_map don't work on arbitrary
sessions" was STALE -- it was the trackless validator false-positive (now fixed) masking success.

The 4 failures are the SAME MIDI-clip sessions as rename (one clip two notes clean / one renamed
midi track and clip / two midi tracks one note each / two separate midi clips same track), all
`bad element tag`. ROOT CAUSE TRACED: `final_index.reindex_after_resize` calls
`final_index.offset_holes`, which is SCAN-based (parse_records over scan zmarks) and raises on
MIDI-clip sessions -- the same undercount that `rename_track` had before its size-driven port.

## [DONE be13c8d] size-driven fallback in reindex_after_resize
Shared infra -- fixes the last 4 MIDI cases for tempo/meter/marker writers (all route through
reindex_after_resize) at once. Must preserve the 5-tuple (abs_pos, value, type, rank, kind) shape
(reindex_after_resize resolves by (type,rank) in the resized layout, NOT a bisect shift). Frame
the index with size-driven zmarks (as body_synth._index_offset_holes does). VALIDATE against the
full synth path (reindex_after_resize is on the synth-insert path too) + the 278 corpus replaces.

## SESSION CHECKPOINT 2026-07-18 — in-place editing is broadly robust on the corpus
All committed + regression-tested; synth path byte-identical throughout; 178 tests green.

  duplicate->remove round-trip     159 / 0 FAIL   (click sessions fixed by bug #15)
  variable-length rename           263 / 263      (MIDI fixed by size-driven rename_track)
  set_tempo_map / set_meter_map    282 / 282      (MIDI fixed by reindex fallback)
  replace_tempo_map/meter_map      already 26/26  (own size-driven splice)
  trackless validate() false-pos   0 / 19         (count-driven name walk)

Bugs closed this session: #14 (name-entry stride 23/21; 2 sites: body_synth + eos_validator),
#15 (<ALL> count = entry count), validator trackless count-driven, reindex_after_resize MIDI.
Capabilities added: variable-length duplicate_track; rename_track/tempo/meter on MIDI sessions.

## REMAINING FRONTIER (next builds, roughly by value)
  1. Non-audio track duplicate/remove: MIDI, aux 0x261E, bus/master. The biggest capability gap
     (remove_track/duplicate_track are audio-only). Aux is the 0x261E subtree (no 0x1014/0x1052
     lane; 0x2613 holds plugin state not clips); MIDI tracks have no 0x1014 the same way. Multi-
     turn build; validate against a track-edit backup pair.
  2. Bug #16: synth-click emits a malformed segmented <ALL>; PT authors flat range(entries).
     Contained synth-fidelity fix (ground truth in hand); risk is the PT-confirmed synth path.
  3. add-clip with NEW audio (needs 0x2628 geometry write; PT-gated).
  4. PT verification of verify_1..verify_11 (user-gated; PT is the only oracle).

## SHIPPED — MIDI track duplicate/remove (commit 86c32e0)
Generalized remove_track/duplicate_track off the audio-only assumption (dissected via a workflow +
independent byte-level ground truth). MIDI tracks: NO 0x1014/0x1052/0x261B/0x261C; track-list
entry = 0x1057 (in 0x1058), per-track record = 0x2620, identified by NAME (a MIDI 0x1057's GUID
differs from its identity GUID). Three edits (seed 0x1057, widen container set to
{0x261C,0x2620,0x261E}, guard = "container found"); existing index rebuild + count re-sync handle
the rest. MIDI dup->remove byte-exact (midi9/two-midi/mixed), validate clean, rc=0, copy visible in
track_types (guards symmetric-incompleteness); variable-length names work; corpus 32 ok / 0
round-trip failures; audio 159/0 (no regression); 180 tests. Click (0x261E) REMOVE works.

## NEXT — click/aux (0x261E) DUPLICATE (the remaining track-edit gap)
Three coupled blockers (from the workflow's adversarial risk list, MIDI-safe but click-relevant):
  1. LAST-name-entry limitation: the click is ALWAYS the last 0x2519 entry (short 21-byte trailer);
     duplicate_track can't copy it as a 23-byte entry. Lift by supporting short-trailer copy, or
     reorder first.
  2. canonicalize_after_track_add's body-rank sort hardcodes `else 0x261C`; a 0x261E playlist-
     instance element gets ranked against block_ranks[0x261C] -> 1<<30 (wrong). Generalize the
     sort key per container kind.
  3. 0x1015 count on a CLICK add: a control pair proves 3->3 (click add does NOT bump 0x1015),
     but the current +1 would; net-zero under dup->remove so the round-trip HIDES it -> needs a
     kind-gate + PT/control-pair check (PT is the oracle). NOTE this is the bug-#9 trap again.

## Other remaining frontier
  - Aux/bus/master tracks that are NOT clicks (if the corpus has any 0x261E variants beyond click).
  - add-clip with NEW audio (0x2628 geometry, PT-gated).
  - PT verification of verify_1..11 + the new MIDI/click edits (user-gated).

## BUG #17 FIXED (commit 3057bdb) — 0x1015 = AUDIO track count, resolved from the bytes
The long-open "what does 0x1015 count" question is settled: it is the AUDIO (0x1014) track count,
NOT all tracks. 166 PT-authored sessions agree (every MIDI/click one has 0x1015 == n_1014); the
old "all tracks" reading was based only on midi9.ptx, whose 0x1015 = 9 with 0 audio is OUR synth
bug. remove_track/duplicate_track now adjust 0x1015 only for an AUDIO edit; MIDI/click edits leave
it untouched. This was a real bug in the MIDI dup shipped earlier today (86c32e0) -- it bumped the
audio count on a 0-audio session, HIDDEN by the byte-exact round-trip (the bug-#9 trap). Fixed +
regression-tested (asserts the INTERMEDIATE, not just A->B->A); 181 tests.

This also resolves click-dup blocker (3) below: the audio-gate returns False for a click, so click
dup won't bump 0x1015 either.

## Remaining click/aux (0x261E) DUPLICATE blockers (down to 2)
  1. LAST-name-entry limitation (the click is always the last 0x2519 entry, short 21-byte trailer).
  2. canonicalize_after_track_add's body-rank sort hardcodes `else 0x261C` -> a 0x261E element
     ranks against block_ranks[0x261C] = 1<<30. Generalize the sort key per container kind.
  (3. 0x1015-count-on-click -> DONE via bug #17's audio-gate.)

## NEW follow-up — synth-path 0x1015 bug
The pure-MIDI synthesis (grow_one_midi_track/add_midi_track) sets 0x1015 = track count (midi9 -> 9)
instead of the audio count (0). PT-authored pure-MIDI sessions store 0x1015 = 0. Uncaught because
the synth oracles are audio-only. Fix the MIDI synth to set 0x1015 = audio count, regenerate midi9.

## MIDI CORRECTNESS AUDIT (bugs #17 + #18 fixed; #17-class discipline applied)
After shipping MIDI dup/remove I checked every stored count against its ACTUAL contents on the dup
output (not just the byte-exact round-trip, which HIDES net-zero errors -- the bug-#9/#17 trap).
Found and fixed TWO count bugs the round-trip masked:
  - #17: 0x1015 (AUDIO count) was wrongly bumped for MIDI/click edits -> now gated to audio.
  - #18: 0x1058 (MIDI count) was NOT bumped for MIDI edits -> now bumped via _adjust_midi_list_count.
On PT-authored sessions the track-list counts (0x1015/0x1058/0x2624/0x2519 header) are now all
consistent with their contents after a MIDI dup. 182 tests.

## KNOWN LIMITATION (pre-existing, audio AND MIDI, PT-pending): non-synth dup omits 0x2589
The non-synth duplicate path DELIBERATELY does not copy the source track's 0x2589 view chain (its
display-row ordinals do not map 1:1 to track_types on a real session -- a click has a chain but no
track_types entry, so no track-keyed attribution is sound; documented in the code). Result: a
non-synth dup copy (AUDIO or MIDI) has one fewer 0x2589 than name entries -- N+1 name entries but N
view chains. The byte-exact round-trip and validate()/rc=0 all pass, so it was never caught. This
is the same "check the intermediate, not A->B->A" lesson. OPEN: is this PT-valid (does PT tolerate
a name entry with no 0x2589, creating a default view) or must the copy get a chain? Needs PT, or a
sound non-synth 0x2589 attribution. Affects the committed audio non-synth dup too (PT-display was
always pending there).

## Synth-path count follow-up (separate from the in-place editors)
Our MIDI synthesis sets 0x1015 = track count (midi9 -> 9, should be 0 audio) AND 0x1058 off-by-one
(midi9 -> 8 for 9 MIDI; should be 9). PT-authored sessions have both correct. Uncaught (synth
oracles are audio-only). Fix the MIDI synth to set both to the PT convention, regenerate fixtures.

## BUG #19 FIXED (commit 6fdbfb8) — the 0x2589 view-chain gap is CLOSED
The gap documented alongside #17/#18 is resolved. THE INVARIANT: every PT session has
`0x2589 ordinals == range(name entries)` — one view chain per display row (162/162 PT-authored
corpus sessions). The off-synth dup skipped the chain, emitting N+1 entries with N chains (the copy
had no view state) — hidden by the byte-exact round-trip because remove skipped symmetrically.

WHY THE OLD SKIP WAS WRONG: its stated reason ("0x2589 does not correspond 1:1 to track_types") is
true but uses the WRONG BASIS — the ordinals are DISPLAY ROWS and a click HAS a row while
track_types never reports it (`3 stereo plus click`: 4 entries vs 3 track_types). Against the
NAME-TABLE entries the correspondence is exact. Name-entry order IS display order (verified against
the authoritative 0x2624 playlist order on `multiple track types`).

FIX: dup attributes by name-entry row R, copies the chain, restamps the copy to R+1 (its name entry
splices directly after the source's) and shifts chains below R down one (via the splice machinery's
existing `copy_off`); remove does the exact inverse. Off-synth only — the PT-confirmed synth
oracles are untouched. Sweep: 166 round-trips / 0 failures; invariant now holds on 153 sessions;
the 13 whose ORIGINAL violates it (our synth output) stay safely skipped. 183 tests.

## Remaining frontier
  1. CLICK/AUX (0x261E) DUPLICATE — 2 blockers: (a) the click is always the LAST 0x2519 entry
     (short 21-byte trailer, can't be copied as a 23-byte entry); (b) canonicalize_after_track_add's
     body-rank sort hardcodes `else 0x261C` -> a 0x261E element ranks as 1<<30.
     (The 0x1015-count blocker was cleared by #17's audio-gate.)
  2. SYNTH-PATH count/ordinal bugs (our synth output, uncaught because the synth oracles are
     audio-only): pure-MIDI synth sets 0x1015 = track count (midi9 -> 9, should be 0 audio) and
     0x1058 off-by-one (8 for 9 MIDI); the mixed audio+MIDI synth emits DUPLICATE 0x2589 ordinals
     ([1,0,1,2,3,4]) — which is why 13 corpus sessions can't be view-chain-attributed at all.
     Fixing this would also let #19's attribution cover those 13.
  3. add-clip with NEW audio (0x2628 geometry, PT-gated); PT verification of all edits (user-gated).

## CORRECTION — the canonicalize `else 0x261C` hardcode is BENIGN, not a landmine
I twice listed `canonicalize_after_track_add`'s `child_type = 0x251A if is_lane else 0x261C`
(final_index.py:432) as a click/aux-dup blocker "ranking a 0x261E element as 1<<30". Checked it
instead of repeating it: both `r_orig` and `r_copy` are ranked with the SAME child_type, so a wrong
type gives BOTH the `1 << 30` default, and Python's stable `sorted()` therefore preserves
`(r_orig, r_copy)` — the identical order the correct rank produces. No misbehaviour, and MIDI dup
empirically round-trips byte-exact with the invariant holding. "Fixing" it would be speculative and
could CHANGE working output, so it stays. **Click/aux duplicate has ONE real blocker left: the
click is always the last 0x2519 entry (short 21-byte trailer).**

Lesson (small, but the same shape as the big ones): an alarming-looking hardcode is still a
hypothesis. Two of my three "blockers" for click dup dissolved on inspection — one cleared by
#17's audio-gate, one benign all along.

## Spec updated (commit 574c461)
The session's format findings are now in `docs/ptx-format-spec.md`, not just this TODO: the three
per-track record variants + their kind-specific track-list entries/counts, and the 0x2589
`ordinals == range(name entries)` invariant with the name-entry-order == display-order proof.

## TRACK-EDIT MATRIX COMPLETE (commit 2ffab52) — last-entry dup lifted, click/aux unblocked
The "cannot duplicate the last track" limitation is gone, because the format question behind it is
ANSWERED (see the spec's 0x2519 trailer section): a full entry ends `<ordinal:u16><next_kind:u16>
<pad>` with next_kind == (kind of NEXT entry) << 8 (audio 0x0000 / MIDI 0x0100 / click 0x0200),
verified on 4321 corpus entries — so the LAST entry is short simply because there is no next entry.
Duplicating it promotes the source to the 23-byte form (next_kind = kind(copy) << 8) and appends
the copy as the new short last with ordinal N+1 — the exact mirror of remove's truncation.

CORPUS SWEEP, before -> after this turn's two fixes (#19 + last-entry):
  duplicate->remove round-trips   166 -> 263   (the 97 that used to raise now all work, 0 failures)
  0x2589 invariant holds          153 -> 250
  raised(limitation)               97 -> 0
  orig_not_1:1 (our synth output)  13 -> 13    (still correctly skipped)

Now supported for BOTH duplicate and remove, as perfect inverses: audio (mono/stereo), MIDI,
click/aux (0x261E), the LAST name entry, and single-track sessions.

## Remaining frontier
  1. SYNTH-PATH writer bugs (our OWN output; uncaught because the synth oracles are audio-only):
     - pure-MIDI synth: 0x1015 = track count (midi9 -> 9, should be 0 audio); 0x1058 off by one.
     - mixed audio+MIDI synth: DUPLICATE 0x2589 ordinals ([1,0,1,2,3,4]) -> these 13 sessions are
       the ones view-chain attribution must skip. Fixing this extends #19's coverage to them.
     - name-entry `next_kind` written wrong in 13 entries of our synthesized files.
     All three are the same shape: our writer does not reproduce a PT invariant we have now pinned.
  2. add-clip with NEW audio (0x2628 geometry, PT-gated).
  3. PT verification of the whole in-place edit suite (user-gated; PT remains the only real oracle).

## CORRECTION + precise diagnosis — the synth writer is CLEANER than I claimed
I listed three synth-path writer bugs from OLD fixtures. Audited the LIVE synth instead of
assuming the fixtures still represent it:

  synth stereo x8 / mono x8 (audio-only)  next_kind OK, 0x2589 ordinals OK, 0x1015 == audio count OK
  synth mixed [mono,MIDI,stereo]          next_kind OK, 0x1015 == audio count OK,
                                          **0x2589 ordinals WRONG** -> [1, 0, 1] for 3 entries

So the audio-only synth is PT-faithful on every invariant we have pinned, and the live mixed synth
gets 0x1015/next_kind right too. My "pure-MIDI synth writes 0x1015 = track count" note came from
`midi9.ptx`, an OLD fixture built by an earlier version — NOT the current writer. (Same mistake
shape as the 0x1015 memory error: reading an old artifact as the current convention.)

THE ONE LIVE SYNTH BUG — mixed audio+MIDI 0x2589 ordinals:
`grow_one_midi_track` does `append_after(_by_type(ptf, 0x2589)[-1], unit["b2589"])`
(body_synth.py ~L621) — it appends a COPY of the template's chain and never restamps its ordinal,
so the new MIDI chain inherits the template's display row and collides ([1,0,1] for 3 entries).
FIX SHAPE: restamp the appended chain to the track's DISPLAY row (== its 0x2519 name-entry index,
per the spec's 0x2589 section). NOTE a plain file-order `enumerate()` restamp (what
`_renumber_body_after_*` does) is WRONG here: the chain is appended LAST while the MIDI track sits
mid-display, so file order != display order. Must map each chain to its entry via the display
order, then assign 0..N-1.
IMPACT when fixed: those 13 corpus sessions stop being skipped by view-chain attribution, so
in-place editing gains full coverage of our own generated sessions. PT already tolerates the
duplicates (the affected sessions are PT-confirmed to open), so this is fidelity, not breakage.

## DOCUMENTATION MILESTONE — the coverage claim was FALSE; fixed and measured (commits cf8554d..91c874d)
`content-type-coverage.md` claimed **280/280 real types = 100.000% documented**. That was wrong: it
was measured with the older `0x5A`-scan enumeration, which undercounts real sessions. Re-measured
with the SIZE-DRIVEN walk over all 282 local sessions:

  distinct real content-types   469  (not 280)
  documented in the spec        231  -> **251** after this session's work
  undocumented BLOCK INSTANCES  2.68% -> **0.07%**
  undocumented types present in EVERY session   20 -> **0**

The 20 ubiquitous undocumented types are now in the spec, dissected from the bytes:
  - `0x4807` plugin/AudioSuite registry entry — layout DECODED
    (`<plugin_id:12 (3 FourCCs)><name_len:u32><ASCII>`; empty slot = 16 zero bytes; 12 slots in
    3 groups of 4), with `0x4823` as its count-prefixed group container.
  - `0x4702` built-in stem/track NAME PRESET dictionary — layout DECODED and exact-fit:
    `<count:u32 = 1252>` + 1252 length-prefixed strings consuming 14726/14726 bytes, byte-identical
    corpus-wide (Dialog, Music, Effects, Narration, DIAL, MUS, FX, BG, FOL, DX, GRP, MX, …).
  - `0x251B`/`0x251C` fixed 5-slot enumerated table (`<slot:u32><3 pad><slot:u8><3 pad>`, 0..4).
  - The 4-byte **reserved-marker family** (`0x4828`→`0x1054`, `0x4829`→`0x1015`, `0x4824`→`0x4501`,
    `0x1048`→`0x1049`, `0x483B` top, `0x483D`→`0x2519`, `0x4833`→`0x271A`) — all zero-valued and
    invariant corpus-wide.
  - Remaining top-level singletons (`0x4834`/`0x4841` twins, `0x4501`, `0x4808`, `0x4820`,
    `0x4809`, `0x4826`) — structure documented, purpose honestly marked UNKNOWN.

REMAINING for "fully documented": 218 rare types (the long tail — together only 0.07% of block
instances, each appearing in a handful of sessions). `docs/content-type-coverage.md` lists them
ranked, and is now regenerated by a size-driven script rather than a stale snapshot.

## DOCUMENTATION: 256/256 content-types now have spec entries (commits cf8554d..89aec45)
Two corrections got the measurement honest, then the gap was closed:

  1. The tracker's "280/280 = 100%" was enumerated with the **0x5A-scan** parser (undercounts real
     sessions). The size-driven walk over all 282 sessions is the right enumerator.
  2. A naive size-driven walk then reports ~469 types — but ~213 of those are ONE artifact: the
     **file-header index pointer at body offset 20**. It is well-framed-looking
     (`5a 01 00 | 04 00 00 00 | XX XX | YY YY`) but is NOT a block: the `u32` at offset 27 is the
     FINAL-INDEX START (verified exactly, 5/5 sessions). Its value differs per session, so the
     walk invents a fresh bogus content-type in every file — 64% of all "undocumented"
     occurrences. The spec's old one-off `0x254D`/`0x2298` phantom entries are two samples of it.
     Enumerators must skip the header region (start at offset 31).

TRUE state: **256 real content-types, 256 documented (100% of block instances).** Newly dissected
this session: the 20 ubiquitous scaffold types (incl. `0x4807` plugin-registry and `0x4702`'s
1252-entry name-preset table, both layouts fully decoded), the `0x2582`-`0x2586` MIDI clip/note
edit-group family (present iff the session has MIDI note content — proven against empty-MIDI
sessions), and `0x4827`.

HONEST CAVEAT (stated in the tracker itself): 100% means every type is *described* — framing,
parents, counts, layout where decodable. It does NOT mean every field's meaning is proven; ~17
entries still say `purpose UNKNOWN` / `UNPROVEN`. That semantic work is the remaining documentation
frontier, and is left visible rather than papered over — this file once carried a false 100%.

`tools/gen_coverage.py` regenerates the tracker, so coverage is re-measurable rather than a stale
hand-maintained snapshot.

## Remaining frontier (unchanged)
  1. Semantics for the ~17 `UNKNOWN`/`UNPROVEN` entries.
  2. Mixed-MIDI synth `0x2589` ordinal writer bug (diagnosed to the line; see the earlier entry).
  3. add-clip with NEW audio (0x2628 geometry, PT-gated).
  4. PT verification of the in-place edit suite (user-gated — PT remains the only real oracle).

## PT VERDICT 2026-07-23 REFUTED "track-edit matrix complete": ALL 8 DUPLICATES REJECTED
Removes: 4/4 accepted (open + close cleanly). Duplicates: 0/8. The byte-exact round-trip passed
every one of them, because it only proves `remove . duplicate = identity` — which stayed true while
BOTH sides were wrong identically. PT is the only oracle.

THREE root causes found and fixed (bugs #20/#21/#22):
  #20 off-synth duplicate re-keyed NO positional fields. PT identifies a track by per-track
      ORDINALS, not the name bytes — the copy had perfect distinct names everywhere and PT still
      showed it under the SOURCE's name. `_rekey_positional_by_slot` is gated behind `_do_renumber`
      (synth only), so real sessions got nothing. New `_rekey_offsynth_positional` covers 0x2519
      entries, 0x251A lanes, and the 0x200A/0x200B/0x2015 field (which are OVERLAPPING VIEWS of one
      physical per-track ordinal — numbered by distinct anchor offset, not per-type enumeration).
  #21 0x1058 missing from the container->child maps, so a duplicated MIDI track's 0x1057 stayed
      PACKED in a two-offset index element instead of its own — the record was one element short of
      the N+1 every PT session has. Audio's 0x1015 was mapped, which is exactly why audio dups
      LOADED and MIDI dups did not.
  #22 THE CRASH-ON-LOAD CAUSE. A copy is spliced at `at == end_of_source_unit`; when the source is
      the LAST child of an ancestor, that ancestor's declared end EQUALS the insertion offset and
      the strict `z < at < e` growth test skipped it, ORPHANING the copy outside its parent. 8/8 +
      4/4 discriminator, with a controlled pair (06 vs 07: same session, same tool — 06 grew all
      four ancestors and loaded, 07 left 0x258A/0x258B ungrown and crashed).

The round-trip is now `identity . repair`: exact on the 253 corpus sessions whose ordinals are
already PT-valid, and on the other 10 (our own earlier synth output) it returns the session
REPAIRED — proven exactly `remove(dup(x)) == rekey(x)`. 185 tests.

A SECOND SELF-CORRECTION: I first reported #22's orphans as already fixed, from a narrow
four-session in-memory check. Re-measuring across all twelve shipped files showed them still there
at byte-identical offsets to the rejected set. The narrow check was simply wrong.

## STILL OPEN
  1. 0x261C per-track C4 counter cloned verbatim (PT distinct [0,1,2]; ours [0,1,2,2]). NOT fixed:
     the semantic is unpinned — PT-authored mixed shows an audio-only index [0,1] while our own
     mixed synth writes [0,1,3,4,5]. Needs ground truth before touching.
  2. 0x104F+11 MIDI clip-slot id cloned on clip-bearing MIDI tracks (never repeats in PT sessions).
  3. Awaiting PT re-test of /Users/jonkubis/Desktop/PTX_VERIFY_2026-07-24/ (12 files, all now
     orphan-free with unique names and every pinned ordinal invariant satisfied).

## Bug #23 FIXED (0x261C C4), and a 0x104F fix ATTEMPTED then REVERTED
#23 — the 0x261C "C4" counter semantic is now PINNED from the corpus rather than guessed: it is the
track's AUDIO-ONLY index, measured on 43 discriminating PT-authored sessions with ZERO exceptions
(`multiple track types` = Click, MIDI, Audio 1, Audio 2 stores [0,1], NOT the global slots [2,3]).
Our mixed synth's [0,1,3,4,5] is our own non-conforming output, not a second convention. Duplicates
cloned the counter, so two tracks shared an identity ([0,1,2,2]). Fixed + tested; no-op on every
PT-authored session. 186 tests.

REVERTED — the 0x104F MIDI clip-slot id (u16 at +11, carried only by clip-bearing MIDI tracks).
The DEFECT IS REAL: PT-authored sessions never repeat a value (`two midi tracks one note each` =
[0,2] with a gap, `two clips same track clean` = [0,1] on ONE track — so the invariant is
UNIQUENESS, not density), while a duplicate clones it ([0,0], visible in shipped file 04). My
minimal de-duplication (keep the first occurrence, move only collisions to the lowest free value)
was a NO-OP on every PT-authored session and produced unique ids after a dup -- but the corpus
sweep collapsed from 252 round-trip-exact to 207 (failures 11 -> 56). remove cannot invert the
renumber, and I do not yet understand the asymmetry, so it is NOT shipped: a fix that quadruples
round-trip failures is worse than the defect it repairs. Baseline restored and re-verified at
252/11. NEXT: work out why remove fails to invert the id assignment (likely the copy's id must be
derived from something remove can recompute, rather than "lowest free").

## Awaiting PT re-test — /Users/jonkubis/Desktop/PTX_VERIFY_2026-07-24/ (12 files)
All 12 are orphan-free (the proven crash discriminator), have unique names, distinct dense 0x261C
C4 counters, satisfy every pinned ordinal invariant, and reload rc=0. Known remaining defect in the
set: file 04 carries repeating 0x104F clip-slot ids (see above).

## Bug #24 FIXED — the 0x104F clip-slot de-dup, done properly this time
Last turn I reverted this (it collapsed the round-trip 252 -> 207). Diagnosing WHY produced both the
right algorithm and a CORRECTION to the invariant I had asserted:

  * THE ASYMMETRY: "lowest id free among those seen SO FAR" can steal an id a LATER block owns,
    renaming a SURVIVING track -- `[2,0]` -> dup `[2,2,0]` -> `[2,0,1]` -> remove leaves `[2,1]`.
    Reserving every pre-existing id BEFORE choosing confines the edit to the copy.
  * THE INVARIANT, CORRECTED: I claimed "uniqueness, not density -- PT shows gaps [0,2]". That gap
    came from OUR file, not PT's re-save of it. Diffing each file against its PT re-save shows PT
    CLOSES gaps ([0,2] -> [0,1]) and removes repeats ([0,0,1,1] -> [0,1]). But dense-from-zero is
    ALSO wrong: PT's own re-saves include a LONE id of 1. So the id is an allocation slot PT may
    leave gapped, and the only property no PT re-save violates is UNIQUENESS. The fix therefore
    de-duplicates and never renumbers what is already unique.

Verified: no-op on all 13 PT re-saves carrying a 0x104F; unique after a dup; and on all 80 corpus
sessions whose ids are ALREADY unique (every real PT session) duplicate->remove is still exact.
187 tests. Accepted cost: 5 sessions lose clean identity-repair composability (252 -> 247), ALL of
which already carried the defect before any edit.

METHOD NOTE worth keeping: "X.ptx vs X clean.ptx" pairs are PT RE-SAVES of our own output, which
makes them a ground-truth oracle for normalisation rules WITHOUT needing a new PT session. That is
how both the density claim and the uniqueness claim got settled.

## SHIPPED FOR PT RE-TEST — /Users/jonkubis/Desktop/PTX_VERIFY_2026-07-24/ (12 files)
Zero known defects remain in the set: orphan-free (the proven crash discriminator), unique track
names, distinct 0x261C C4 counters, unique 0x104F clip-slot ids, every ordinal invariant satisfied,
validate() clean, reload rc=0.

## BUG #24 REVERTED — it was a CATEGORY ERROR, and the spec already had the answer
I shipped #24 ("de-duplicate the 0x104F clip-slot id") last turn on the strength of two claims. Both
were wrong, and mining the corpus further is what exposed them:

  1. THE "PT RE-SAVE" ORACLE DOES NOT EXIST. I treated `X.ptx` / `X clean.ptx` as before/after of one
     session. Diffing them structurally: `two clips same track` goes from an AUDIO track (0x261C,
     "Audio 1") to a MIDI track (0x2620, "MIDI 1"), and the third pair drops the entire
     0x2582-0x2586 MIDI-clip family. They are SEPARATE CAPTURES, not re-saves. Every
     "normalisation rule" I derived from them (PT closes gaps, PT removes repeats) was an artifact
     of comparing unrelated files.
  2. `0x104F +11` IS NOT AN OBJECT ID -- it is the REGION INDEX into the 0x2629 list, and §8 of the
     spec has documented it as such, "verified high", the whole time. Repeated values are correct BY
     DEFINITION: the same region placed twice, and a stereo track's two lanes referencing the same
     regions. `two clips same track.ptx` -- PT-authored on independent grounds (it satisfies every
     invariant our own writer gets wrong) -- stores [0,0,1,1] for ONE stereo track.

So the fix was renumbering PT-conforming data. Reverted (commit 1925c2c); corpus back to the 252/11
baseline; 186 tests.

GENUINELY NEW, and kept in the spec: `0x104F` hangs off TWO parent chains --
`0x1054 > 0x1052(lane) > 0x1050 > 0x104F` for audio, `0x1058 > 0x1057(track) > 0x1056 > 0x104F` for
MIDI. An editor walking only one chain misses every placement of the other kind. Also recorded: the
`* clean.ptx` caveat, so nobody rebuilds the phantom oracle.

LESSON: before inventing a semantic for a field, GREP THE SPEC FOR IT. §8 already had `0x104F +11`
right; I spent a fix, a revert and two false invariants rediscovering it wrongly. And an "oracle"
needs its provenance checked before its conclusions are trusted -- I validated the conclusions and
never the premise.

## NEW CAPABILITY — `validate_pt_invariants()`, calibrated against real PT verdicts
Not another fix: a correctness GATE. Every bug this session (#19-#23) passed `validate()`, rc=0 AND
the byte-exact round-trip, and PT rejected the file anyway. This checks the INTERMEDIATE against
invariants measured from PT-authored sessions.

CALIBRATION (committed as a test): against the 12 PT-tested files it scores 8/8 rejections flagged,
4/4 acceptances clean, 0 false positives, 0 misses.

It also FALSIFIED one of my own invariants on first run. v1 required the ordinals to be DENSE 1..N
-- what every PT-AUTHORED session shows -- and that flagged file 12, which PT ACCEPTED with [1,3,4]
(the gap left by removing a middle track). So:
    DENSITY  = a normalisation PT-authored files happen to have  -> severity "nonconforming"
    UNIQUENESS = the rule PT actually enforces                   -> severity "error"
Third time this session that "all PT files look like X" turned out to be normalisation rather than
constraint, so the distinction now lives in the API instead of in my head.

USE IT as the gate before shipping any edited session; the 12 files staged for re-test report 0
errors and 0 notes.

## Remaining frontier (unchanged, and honestly bounded)
  1. ~17 spec entries still say UNKNOWN/UNPROVEN. Attempted this turn and STOPPED: the corpus has
     no discriminating evidence for them (the "window closed" pairs also drop the whole MIDI-clip
     family, so they are not controlled experiments). Resolving these needs NEW targeted sessions,
     not more inference over the existing corpus -- inference is exactly what produced #24.
  2. add-clip with NEW audio (0x2628 geometry).
  3. Synth-path writer bugs (mixed-MIDI 0x2589 ordinals). Deliberately untouched: that path is
     PT-confirmed, and a speculative fix there risks breaking something that works.
  4. PT re-test of the 12 staged files -- still the only thing that can confirm #20-#23.

## The gate falsified TWO of its own rules (this is the point of having it)
Stress-testing `validate_pt_invariants` across the whole corpus flagged
`mixedmidi_click_{bottom,top,middle}` and `mixedmidi_reorder_*` as ERRORS -- files PT CONFIRMED
opening on 2026-06-20. Repeated 0x2589 display rows and repeated per-track subtree ordinals are
therefore TOLERATED by Pro Tools; treating them as rules was the normalisation-vs-constraint
confusion again (4th instance this session).

Before removing them I checked they were not load-bearing: with both dropped, the gate STILL
catches 8/8 known PT rejections -- every one is independently caught by containment, 0x2519 entry
ordinals, 0x251A lane ordinals, 0x261C C4, or duplicate names. Zero detection value, pure false
positives -> downgraded to "nonconforming". The 0x2589 COUNT check stays an error (that is #19).

FINAL CALIBRATION, consistent with every piece of PT evidence in existence:
    12 PT-tested files          8/8 rejections flagged, 4/4 acceptances clean, 0 FP, 0 misses
    PT-confirmed synth outputs  0 errors  (previously false positives)
    all 282 corpus sessions     0 errors  (162 of them PT-authored)
Locked in by two negative-control tests. 189 tests.

WHAT THIS CHANGES GOING FORWARD: the ERROR set is now exactly the rules with PT evidence behind
them -- containment, and uniqueness of the 0x2519/0x251A/0x261C ordinals + track names. Everything
else PT-authored files share is recorded as "nonconforming", i.e. style, not law. That distinction
is the single most useful thing this session produced, because I got it wrong four separate times
by eyeballing PT-authored files and generalising.

## Clip layer AUDITED (clean) + regression harness that PROVABLY catches this session's bugs
Ran the calibrated gate over the clip primitives, which had never been checked against it:
`add_clip` / `remove_clip` / `move_clip` produce ZERO error-severity violations across all 51
clip-bearing corpus sessions. The track layer's disease does NOT extend to clips -- a placement
legitimately reuses its region reference (`two clips same track` has two placements pointing at the
SAME region), which independently confirms the 0x104F conclusion that got bug #24 reverted.

New test `test_every_editing_primitive_produces_pt_clean_output` runs the gate over the OUTPUT of
every track/clip editor across a corpus sample. VERIFIED to catch a real regression: disabling the
bug-#20 re-key makes it fail immediately on the 0x251A lane ordinals. Future defects in these
families are now caught by the suite rather than by a human hitting a PT crash. 190 tests.

## `add_clip` ALREADY EXISTS — the frontier item was overstated
The TODO listed "add-clip with NEW audio (0x2628 geometry)" as if clip insertion were missing.
`add_clip(data, lane_zmark, position, region_index=None)` has been there and is the exact inverse
of `remove_clip`; it places a region ALREADY in the session. The genuine gap is narrower: importing
audio that is not yet in the session, which needs the 0x2628 region geometry -- and the spec
already marks that "descriptor model (PARTIAL -- not add/replace-ready)". NOT attempted: building
on partially-understood geometry is exactly what produced #24 and its revert.

## DEAD END, recorded so it is not retried: there is NO PT re-save oracle in this corpus
I have now tried THREE times this session to find a "PT opened our file and saved it" oracle, and
been wrong all three times:

  1. `X.ptx` vs `X clean.ptx` -- REFUTED: the track KIND changes (0x261C audio -> 0x2620 MIDI,
     "Audio 1" -> "MIDI 1"). Separate captures. This one cost a shipped fix (#24) and its revert.
  2. `Session File Backups/X.bak.NNN` -- REFUTED: `mixedmidi_click_bottom.bak.000` reports EIGHT
     tracks for SIX name entries, with duplicated names (Audio 1 x2, MIDI 2 x2) and 0x1014=5 vs our
     3. A different session state, not a normalisation of ours.
  3. (The initial `0x104F` "PT closes gaps" reading came from #1 and fell with it.)

WHAT MADE #2 LOOK CONVINCING, and why that was not enough: every .bak scored 0 errors + 0 notes
under the gate while every corresponding file of ours scored 0 errors + 3 notes -- exactly the
"PT tolerates on load, normalises on save" story I was hoping for. The numbers fit the hypothesis
perfectly and the hypothesis was still false. Provenance has to be established BEFORE the
conclusions are read, not inferred from how well they fit.

DO NOT retry this without external provenance (a file the user confirms Pro Tools wrote).

## OPEN QUESTION surfaced by that dead end (NOT a rule -- uncalibrated)
`track_types()` reports 8 tracks for a 6-entry name table on those .bak files, with repeated names.
Either those files are genuinely malformed, or `track_types` over-reports on some structures. The
gate does NOT check track_types against the name table, so it is blind to whichever it is. I have
not added a rule because I cannot calibrate one against a PT verdict -- adding uncalibrated rules
is what produced the two false positives the gate itself later caught.

## The remaining UNKNOWNs are now MEASURED, not just asserted
I had been saying "these need new captures" without proving it. Measured every flagged type's
payload across all 282 sessions:

  MONOMORPHIC (1 distinct payload — provably NO signal in this corpus, 8 types):
      0x2502, 0x206f, 0x2103, 0x2020, 0x258f, 0x2435, 0x2715, 0x4827
  HAS SIGNAL (tractable from the corpus, 4 types):
      0x201f (5 variants), 0x258e (4), 0x103d (2), 0x1041 (2)

That is the difference between "I gave up" and "the data cannot answer this": for the eight, every
instance is byte-identical, so no correlation with any session property is even possible. Their
LAYOUTS are documented; only the meaning of an invariant constant is open.

WORKED THE HIGHEST-SIGNAL ONE (0x201f, saved window geometry) down to a single field:
  * the discriminator is the u32 at payload+24, taking only 0x0000 or 0x0100
  * REFUTED "it tracks MIDI content": flag=0 in 4 sessions WITH the MIDI clip family; flag=256 in
    14 sessions with NO MIDI track
  * REFUTED "it is the MIDI editor": `midi note editor closed` = 256, same as editor-open sessions
  * CANDIDATE, explicitly NOT claimed: `memory locations window closed` = 0. Suggestive only —
    that file is not a controlled variant (it also differs by the whole 0x2582-0x2586 family), so
    it is a separate capture, not a one-variable change. I checked this BEFORE writing it down,
    which is the step I skipped with the clean/.bak oracles.

THE EXPERIMENT THAT SETTLES IT (cheap, and the same recipe for all four signal-bearing types):
save one session twice changing ONLY whether a given window is open, then diff payload+24.

Remaining as before: 0x2628 new-audio geometry, and PT re-test of the 12 staged files.

## 0x2628 UNBLOCKED STRUCTURALLY — the reimplementation gap is smaller than stated
The spec called this "PARTIAL — not add/replace-ready", and I had twice deferred new-audio import
on that basis. Measuring instead of deferring, the record's LAYOUT is now fully pinned
(2774/2774 corpus records, both checks exact):

    [9 hdr][u32 namelen][name][5-byte descriptor][variable][12-byte CONST tail][27-byte trailer]
    span == 57 + namelen + varlen        size field == span - 7

  * the 12-byte tail `fe ff 00 00 00 00 ff ff 04 00 04 00` is byte-IDENTICAL in every record and
    is found in 2774/2774 -> the variable section can be located by SEARCH, not by arithmetic;
  * exactly 27 bytes follow it, always;
  * so a writer can build or resize a 0x2628 exactly, and DUPLICATING an existing region needs no
    field semantics at all (copy the variable section verbatim).

The nibble-width model is NOT needed for parsing: it lands on the terminator in 2714/2774, and the
60 exceptions are one signature (`00 10 20 11 08`, the `legacy_audio_*` sessions) overshooting by
exactly one byte. Terminator-search is the reliable strategy; nibbles only interpret the fields.

STILL OPEN (correctly): the field SEMANTICS inside the variable section (which slot is length vs
offset vs source position). That blocks creating a region for genuinely NEW audio -- but it does
NOT block region duplication, which is the larger half of what "add clip" means.

LESSON: "PARTIAL — not add/replace-ready" had been sitting in the spec as a stop sign, and I
treated it as one for several turns. It marked an UNFINISHED MEASUREMENT, not an impossible one.
Worth re-testing the other stop signs rather than inheriting them.

## NEW CAPABILITY SHIPPED — duplicate_region() (commit ffa6971)
The first genuinely new editing capability this session, not another fix. Appends a copy of an
audio region under a new name; `add_clip(region_index=...)` then places a clip REFERENCING it.
End-to-end verified: the clip reports the new region name, validate() clean, PT gate 0 errors,
reload rc=0. 191 tests.

Why it was cheap once 0x2628 was measured: a region is a self-contained 0x2629 in the 0x262A list
(u32 count at payload+0); NEITHER 0x2629 NOR 0x2628 is a master-index target -- only 0x262A is --
so an insert adds no index entries, just an offset slide. And because the 0x2628 variable section
is delimited by the constant tail, duplicating needs only a NAME rewrite with
`span == 57 + namelen + varlen`.

## THE NEW-AUDIO BOUNDARY IS NOW EXACT (was vague for many turns)
"add-clip with NEW audio" was listed as one blocked item. It is really two, and only one is left:
  DONE  — create a new REGION and place a clip on it (duplicate_region + add_clip).
  OPEN  — point a region at a DIFFERENT wav. `_repoint_clip` fails with
          `ValueError: list.index(x): x not in list` at `_audio_filenames(data).index(old_filename)`
          because a duplicated region has no entry in the audio-FILENAME list (0x1003 / 0x103a).
NEXT CONCRETE STEP: dissect the audio-FILE record chain (0x1003/0x103a and whatever holds the
filename + the region->file link, the "findex" mentioned in the 0x2629 spec row) and add an
`add_audio_file()` primitive. That is a bounded structural task of the same shape as this one --
and the same shape as the 0x2628 work that turned out to be a measurement, not a wall.

## CLIP-IMPORT CHAIN NOW RUNS END TO END (commit d053970)
The item that sat on the frontier as "add-clip with NEW audio (0x2628 geometry, PT-gated)" is now
mostly built, and what remains is one specific thing rather than a vague blocker.

    duplicate_audio_file(d, i, "IMPORTED.wav")   0x103A filename entry + its 1:1 0x1003 record
    duplicate_region(d, i, "IMPORTED")           0x2629 entry (0x2628 name rewritten)
    add_clip(d, lane, pos, region_index=...)     placement referencing the new region

Verified on a real session: files 1->2, regions 2->3, the placed clip reports the new region,
validate() clean + PT gate 0 errors + rc=0 at EVERY stage. 192 tests.

WHY IT WAS CHEAP once measured: none of 0x103A / 0x1003 / 0x2629 / 0x2628 is a master-index target,
so every step is a splice + offset slide with no index entries. The 0x103A entry stride is
`13 + namelen` (`02 00 00 00 00 <u32 len> <name> EVAW`), and 0x1003 records are 1:1 with filenames
(4 wavs -> 4; 1300 -> 1300).

Factored `_splice_in(data, at, blob)` out of the three call sites that had each grown their own
copy (duplicate_region / add_clip / remove_clip).

## WHAT IS ACTUALLY LEFT (precise, no longer a category)
  1. The 0x1003 body is copied VERBATIM, so a registration describes the SAME audio as its source.
     Pointing it at genuinely DIFFERENT audio needs the identity fields inside 0x1003 rewritten
     (length / rate / GUID). `wav_clip_identity()` already COMPUTES these for the synthesis path,
     so the next step is mapping its outputs onto 0x1003's offsets -- a bounded field-mapping task.
  2. PRO TOOLS HAS NOT SEEN ANY OF THIS. Structural cleanliness is necessary, not sufficient --
     that is the lesson of bugs #20-#23, all of which passed every structural check.
  3. Documentation: 0x201f's one 2-valued field, and the 8 monomorphic constants (no signal in
     this corpus by measurement, not by assumption).

## NEW-AUDIO IMPORT: the chain is now BUILT END TO END (commit 62558ee)
    duplicate_audio_file(d, i, "NEW.wav")     0x103A entry + its 1:1 0x1003 record
    retarget_audio_file(d, idx, wav_path)     point that record at a DIFFERENT wav
    duplicate_region(d, i, "NEW")             0x2629 entry (0x2628 name rewritten)
    add_clip(d, lane, pos, region_index=...)  placement referencing the new region

The identity mapping needed no discovery: `set_clip_wav` had established it for the SYNTHESIS path
(+15 sample count, +44 umid, +292 umid-with-00-prefix, +301 id2). `retarget_audio_file` applies the
same fields to ONE record. All fixed-width -> size-neutral.
Verified: sample count moves 8448 -> 11540 == wav_clip_identity(target), the original registration
stays byte-identical, validate() clean, PT gate 0 errors, rc=0 at every stage. 193 tests.

### The honest remainder
  1. **PRO TOOLS HAS NOT SEEN ANY OF IT.** Not boilerplate: bugs #20-#23 each passed validate(),
     rc=0 AND the byte-exact round-trip while PT rejected the file. Structural cleanliness is
     necessary, never sufficient. These primitives are UNVERIFIED, not done.
  2. The region's 0x2628 LENGTH field is not updated by `retarget_audio_file`. If the new audio
     differs in length from the source region's, the clip geometry will be wrong.
     `set_clip_wav` writes that field (at `0x2628` +13+namelen+5) as part of a whole-clip
     retarget -- wiring it into the region path is the next bounded step.
  3. Documentation: 0x201f's single 2-valued field; the 8 monomorphic constants (measured to carry
     no signal in this corpus -- a NEW capture is required, this is not an inference gap).

### What this session actually established about method
Three things I had been quoting as blockers were unfinished MEASUREMENTS, not walls: the 0x2628
geometry, most of the UNKNOWN list, and the "add-clip needs new audio" item. Each dissolved when
measured. The one real external dependency is Pro Tools itself, and that has now been the
bottleneck for the whole session -- every one of bugs #20-#24 traces back to a PT verdict.

## 0x2628 LENGTH FIELD proven + writable (commit 0b20724) — the import chain is field-complete
The gap named one commit earlier is closed. The FIRST field the 0x2628 descriptor declares is the
region's LENGTH IN SAMPLES: for every corpus region whose WAV is on disk it equals
`wav_clip_identity(wav)[0]` EXACTLY (40/40, across 2/3/4-byte widths). That is sharper than the
spec's old "final field == source-file length", which it had itself recorded as REFUTED.

    region_length(d, i)                 read
    set_region_length(d, i, samples)    write, size-neutral, byte-exact when set back

Full chain, field-complete:
    duplicate_audio_file -> retarget_audio_file -> duplicate_region -> set_region_length -> add_clip
194 tests; every stage validate()-clean, PT gate 0 errors, rc=0.

### THE remaining structural limit, stated exactly
The length field's WIDTH is nibble-declared and is 2 bytes in ~94% of records -> counts above
65535 do not fit. Widening requires rewriting the descriptor nibble AND resizing the block, which
moves every enclosing size and index offset. `set_region_length` RAISES rather than truncating.
That resize is the one genuinely unfinished piece of the 0x2628 model. It is tractable -- the
layout is fully pinned (span == 57 + namelen + varlen) and `_splice_in` already does the
grow-and-slide -- but it is real work, not a measurement.

### Everything else outstanding
  1. PRO TOOLS HAS NOT SEEN ANY OF THE IMPORT CHAIN. Bugs #20-#23 each passed validate(), rc=0 AND
     the byte-exact round-trip while PT rejected the file. Unverified is not done.
  2. Docs: 0x201f's single 2-valued field; the 8 monomorphic constants (measured: no signal in this
     corpus, so capture-bound rather than inference-bound).

## 0x2628 FIELD WIDENING IMPLEMENTED (commit 77e19de) — no known unimplemented field remains
The item I called "the one genuinely unfinished piece of the 0x2628 model" one commit ago is done.
`set_region_length` now widens: rewrite the slot's nibble, splice the extra bytes in, and let
`_splice_in` grow every enclosing container and slide every index offset.

Verified: 4321 fits (+0 bytes), 70000 and 5000000 widen to 3 bytes (+1) and read back exactly;
other regions untouched; validate() clean, PT gate 0 errors, rc=0; and `span == 57+namelen+varlen`
still holds after widening. Only hard stop left: a nibble caps one slot at 15 bytes.

A check I nearly misread as corruption: widening changes the block multiset by one at body offset
20 (0x1640 -> 0x1641). That is the FILE-HEADER INDEX-POINTER PHANTOM documented earlier this
session -- its apparent "content type" is the low half of the final-index offset, so growing the
body shifts it. Total block count unchanged. My own earlier finding predicted it exactly, which is
the first time this session that a documented result saved me from a false alarm rather than the
reverse.

Also fixed a now-obsolete assertion: `test_region_length_is_the_wav_sample_count` required a
ValueError for 10,000,000, which was only true while widening was unimplemented.

### STATE
IMPLEMENTED, structurally complete, no known unimplemented field:
    tracks : duplicate_track / remove_track (audio, MIDI, click, last-entry, single-track)
    clips  : add_clip / remove_clip / move_clip
    import : duplicate_audio_file -> retarget_audio_file -> duplicate_region
             -> set_region_length -> add_clip
    gate   : validate_pt_invariants (calibrated 8/8 + 4/4 against real PT verdicts, 0 FP corpus-wide)
    195 tests; corpus 252 round-trip-exact.

REMAINING, and both are external-input-bound rather than analysis-bound:
  1. PRO TOOLS VERIFICATION of the import chain and of the 12 staged files. Bugs #20-#23 each
     passed validate(), rc=0 AND the byte-exact round-trip while PT rejected the file, so
     "structurally clean" has a measured false-positive rate. This is the bottleneck.
  2. Docs: 0x201f's single 2-valued field, and the 8 monomorphic constants -- measured to carry
     ZERO signal in this corpus, so they need a capture where the value differs.

## IMPORT CHAIN STAGED FOR PT — /Users/jonkubis/Desktop/PTX_VERIFY_2026-07-25_IMPORT/ (6 files)
The import chain was "structurally clean but unverified", which is the weakest possible state given
that bugs #20-#23 all passed every structural check. It is now TESTABLE: a LADDER where each file
adds exactly one primitive, so a Pro Tools failure localises to a specific call rather than to
"somewhere in the chain".

    13_register_audio_file      duplicate_audio_file  -- 2nd filename entry + its 0x1003 record
    14_retarget_to_other_wav    + retarget_audio_file -- that record points at a DIFFERENT wav
    15_add_region               + duplicate_region    -- a new region 'IMPORTED'
    16_set_region_length        + set_region_length   -- region length -> the wav's 11540 samples
    17_place_clip_full_chain    + add_clip            -- THE PAYOFF: a new clip on the timeline
    18_widen_region_length      set_region_length(70000) on the ORIGINAL -- exercises WIDENING

All six: validate() clean, PT gate 0 errors, rc=0. `Audio Files/` copied alongside so the session
can find its media. The 12 track-edit files in PTX_VERIFY_2026-07-24 were re-checked this turn and
still match the committed code exactly.

FOUND WHILE BUILDING IT (real API trap, now documented): lane/region zmarks are byte offsets into
ONE body, so every splice invalidates them. Chaining edits with a lane zmark captured before the
chain fails with "no 0x1052 lane at zmark N". `region_list`/`clip_lanes` now say so, and recommend
carrying region INDICES (stable under append) rather than zmarks.

## Synthesis paths swept with the gate — clean, and now pinned
The gate had only ever been run over `control_files/`. Sweeping `generated/` (413 of our own
outputs) found 29 with ERROR-severity violations: 0x2589 chain counts (23), 0x261C C4 (11),
0x251A lane ordinals (6).

Checked before reacting: EVERY LIVE synthesis path is clean (stereo 8/12, mono 8, mixed-MIDI x2,
click) -- so the 29 are artifacts of older code, not a live bug. New test
`test_synthesis_paths_are_pt_invariant_clean` pins it, because synthesis is a SEPARATE writer from
the editors and nothing was holding it to these invariants. 196 tests.

The mixed-MIDI paths report "nonconforming" notes (repeated 0x2589 rows, non-dense subtree
ordinals) and that is correct: PT opens those files (confirmed 2026-06-20). Only ERRORS are
asserted -- the severity split earned its keep again.

## Second measurement pass: I had only measured 12 of the 21 flagged types
Enumerating every spec section carrying an UNKNOWN/UNPROVEN marker gives **21 types**, not the 12
I measured earlier. Measured the other nine:

  MONOMORPHIC (no signal in this corpus): 0x201e, 0x2025, 0x2049, 0x2508, 0x2509  (+0x207b, below)
  HAS SIGNAL: 0x1017 (3 payloads), 0x2056 (9 real payloads)

**0x207b CORRECTED — its documented "two forms" was a phantom.** 282/282 sessions carry ONE
124-byte record. The apparent second form (an 11-byte block with payload `01 00`, in a single
session) sits at body offset 20 -- the file-header INDEX POINTER, not a block. Its "content type"
is the low half of the final-index offset, which in that session read as 0x207b. So 0x207b is
monomorphic.

**GENERAL RULE, now in the spec:** any per-type variant count must EXCLUDE blocks below body offset
31. The same phantom had also inflated 0x2056 (10 apparent payloads -> 9 real). This is the third
distinct place the offset-20 pointer has corrupted a measurement this session (coverage tracker,
0x207b's "two forms", 0x2056's variant count) -- it is worth checking FIRST whenever a rare variant
appears in exactly one session.

RUNNING TALLY across both passes: 14 monomorphic, 4 with signal, 3 closed (0x1041 resolved,
0x103d explained by parent, 0x258e scope corrected).

NEXT TRACTABLE: 0x1017 -- 3 payloads, and NOT explained by parent context (0x1018 alone holds 2),
so unlike 0x103d there is genuine within-context variation to correlate. 0x2056 has 9.

## 0x1017 RESOLVED — and it found a code divergence (commit b277f90 + gate note)
Fully decoded from the corpus, phantom-filtered:
  parent 0x204a : 564 instances, 40-byte all-`ff` PLACEHOLDER (one payload)
  parent 0x1018 : a PLUG-IN DESCRIPTOR -- `<u8 kind><u32 namelen><ASCII name><FourCC reversed>`
                  282x `"Polyphonic"`, 35x `"Click II"`; the FourCC `igiD` reads back as `Digi`,
                  the same reversal the 0x4807 registry uses.
PROVEN PRESENCE RULE: the `Click II` descriptor is present IFF the session has a click track --
35 with both, 247 with neither, 0 exceptions across all 282.

CROSS-CHECK FOUND A DIVERGENCE: `remove_track("Click 1")` leaves the descriptor behind, so the
session declares a click plug-in with no click track. Classified NONCONFORMING, not an error,
because PT ACCEPTED exactly that file (09_click_remove opens and closes cleanly). The gate now
reports it by name; calibration re-verified at 8/8 + 4/4 + 0 FP.

Notable: this is the first defect this session found BY DOCUMENTING rather than by testing. The
presence rule came out of spec work, and the code divergence fell out of checking the
implementation against it -- the reverse of the usual direction.

TALLY of the 21 flagged types: 14 monomorphic, 3 with signal remaining (0x2056 x9, 0x201f x5,
and 0x1017 now closed), 4 closed (0x1041, 0x103d, 0x258e, 0x1017), 1 corrected (0x207b).

## 0x2056 field-mapped — "9 variants" is ONE struct with 8 fields
Measured on all 1170 real instances (phantom-filtered): a FIXED 41-byte payload, **33 bytes
constant**, with variation confined to eight small-enum fields:
    +0 {0,1}   +9 {0,1,4}   +13 {2,4,8}   +17 {1,3}
    +29 {0,1}  +30 {0,1}    +35 {1,8}     +39 {80,96,100}
By parent: 0x2588's instance is MONOMORPHIC across all 282 sessions; 0x2581 carries 8 of the 9
payloads. Sessions hold 4 of these (263), sometimes 6-7.

The shapes are suggestive (+13 ∈ {2,4,8} looks like a power-of-two zoom, +39 ∈ {80,96,100} like a
percentage/height) and I did NOT write those guesses into the spec as findings -- the corpus has no
session that varies one view setting while holding the rest fixed, so they are unfalsifiable here.

### THE SINGLE EXPERIMENT THAT UNBLOCKS THE DOCUMENTATION GAPS
Every remaining open entry needs the same cheap thing: **one session saved twice, changing exactly
one setting**, then diff. Concretely, the highest-value captures would be:
  * zoom level changed (pins 0x2056 +13, maybe +39)
  * track height changed (pins another 0x2056 field)
  * a window opened/closed  (pins 0x201f payload+24 -- currently 2-valued, MIDI and MIDI-editor
    readings already REFUTED)
  * any of the 14 monomorphic types' settings changed at all -- they are byte-identical in all 282
    sessions, so ANY session that differs gives the first signal those entries have ever had.
That is a ~10-minute exercise in Pro Tools and would likely close more of the spec than another
full session of corpus analysis, which has now reached its measured limit.

## 0x201f: systematic sweep instead of hand-picked hypotheses
Previously I had tested two readings by hand. Ran a proper sweep instead — the flag against twelve
computed session properties across all 282 sessions:

  * NO PERFECT PREDICTOR exists among any of them.
  * REFUTED (new): tempo/meter. The flag=256 group is dominated by names like `120 to 140bpm`,
    `3-4 meter`, `SYNTH_meter_*`, which looked decisive — but the tempo/meter block counts are
    FIXED regardless of flag (0x2028/0x2029 = 2, 0x2718/0x2719 = 1) and their payload spans
    OVERLAP between groups. The filename pattern is an artifact of how those captures were made.
    (Third hypothesis refuted for this field, after MIDI content and the MIDI editor.)
  * SOLID, one-way: flag=256 ⟹ no audio tracks, no click, no regions, no markers — 32/32, no
    exceptions. The converse FAILS: 27 flag=0 sessions are also audio-free, and nothing measured
    separates those 27 from the 32.

Recorded in the spec so the next attempt does not re-test the same three readings. This is the
limit of what correlation over this corpus can say about the field; separating those 27 from the 32
needs a capture that varies one thing.

## Bug #25 — audio-file list assumed a folder literally named "Audio Files" (commit above)
Found by sweeping the NEW import primitives corpus-wide (they had only been exercised on 2-3
sessions). 49/52 region-bearing sessions worked; 3 raised "no 0x103A audio-file list".

PRE-EXISTING root cause: the 0x103A list opens with the MEDIA-FOLDER NAME, length-prefixed at block
offset +18 -- "Audio Files" by default, but whatever the folder is really called. `_audio_filenames`
searched for the literal, so RELINKED sessions (3 in the corpus, folder "8192 wavs") parsed as
having ZERO audio files; my `_audio_file_list` inherited the same bug. Both now read the length
prefix: 52/52 parse, agreeing with the old search on all 49 "Audio Files" sessions.

GOTCHA worth remembering: the two readers use different bases for the same field -- `_audio_file_list`
is zmark-relative (+18/+22) but `_audio_filenames` slices from `b.offset` == zmark+7, so it needs
+11/+15. My first fix used the wrong base and the relinked sessions still returned []; caught by
re-running the check instead of trusting the edit.

SWEEP RESULT for the new primitives (this is what makes them trustworthy now):
    duplicate_region     52/52 gate-clean
    duplicate_audio_file 52/52 gate-clean (was 49 + 3 raises)
    set_region_length    52/52 byte-exact no-op, 52/52 widening gate-clean

## Gate gained REFERENTIAL INTEGRITY checks — closing the hole bug #25 used
The gate checked ordinals, containment and counts -- all properties of VALUES -- but never that a
REFERENCE RESOLVES, which is exactly why #25 escaped it and needed a corpus sweep to find. Added,
both holding 162/162 on PT-authored sessions:
  * every AUDIO placement's 0x104F +11 region index resolves into region_list()
  * a session with regions must have a parseable 0x103A audio-file list

Scoping mattered: checking BOTH 0x104F chains together fails on 31 PT-authored sessions, because
the MIDI chain (0x1058>0x1057>0x1056) indexes a different list than the audio chain
(0x1054>0x1052>0x1050). The two-chain finding from the bug-#24 investigation earned its keep again.

PROVEN to catch #25: reintroducing the hardcoded "Audio Files" lookup makes both relinked sessions
report `audio-file-list`. Calibration re-verified: 8/8, 4/4, 0 FP, corpus 282/282 clean. 198 tests.

WHAT THIS SAYS ABOUT THE GATE'S DESIGN: every check it had was value-shaped, so a whole CLASS of
defect (dangling references) was invisible to it. Worth asking what other classes are missing --
ordering constraints and cross-container agreement are the obvious next candidates.

## Gate: cross-container COUNT agreement added (nonconforming) — 2nd blind-spot class closed
Named last turn as an unexamined class; now checked. A container's stored count vs the children it
frames: 0x1015->0x1014, 0x1058->0x1057, 0x262A->0x2629, 0x1054->0x1052, 0x2624->per-track records.
All five agree with ZERO mismatches on 162/162 PT-authored sessions.

**My negative controls stopped me shipping it as an ERROR.** I added it at error severity; the
suite failed two tests immediately -- `mixedmidi_click_*` store 0x1058=1 while framing 2 0x1057
children and are PT-CONFIRMED to open (2026-06-20), and the live mixed-MIDI synth trips it too. PT
tolerates the disagreement. Downgraded to nonconforming; suite green, corpus 282/282 error-clean.

THIRD instance this session of "every PT file does X" being a normalisation, not a rule (after
ordinal density and the 0x2589/subtree ordinals). The pattern is now cheap to detect because the
severity split + negative controls exist -- this cost one test run rather than a shipped defect.

WHAT THE 13 NOTES REVEAL: all our own older mixed-MIDI output, writing 0x1015 as the TOTAL track
count instead of the audio count (the SYNTH-path twin of editor bug #17) and 0x1058 off by one.
The live editors are clean; the live mixed-MIDI SYNTH still writes them. That is a real, bounded
follow-up with a known-correct target -- and unlike the documentation gaps it needs no new capture.

## Bug #26 FIXED — the mixed-MIDI SYNTH's kind track counts (the gate's first catch)
The `container-count` note added last turn immediately localised a LIVE defect: the mixed-MIDI
synthesiser wrote 0x1015 as the TOTAL track count and 0x1058 off by one. PT's convention (162/162,
zero mismatches) is 0x1015 == audio (0x1014) count, 0x1058 == MIDI (0x1057) count. This is the
synth-path twin of editor bug #17.

`_sync_kind_track_counts` recomputes both from the framed children; `synthesize_mixed_midi` calls
it. Recomputation is safe HERE (the editors still adjust by delta) because #17 forbade it only
while the convention was unknown -- it is pinned now.

WHY IT SURVIVED EVERY EARLIER SWEEP: `_fix_container_counts` covers only 0x1054->0x1052 and 0x2624,
so running it changed ZERO bytes on these files. The defect was invisible to the one function whose
name suggests it should have caught it.

Verified: both mixed-MIDI specs lose the note, counts equal framed children, 199 tests, corpus
282/282 error-clean. PT tolerated the stale values, so this is conformance, not a crash fix.

### Gate blind-spot classes: 2 of 2 named classes now closed
    references           -> region-reference / audio-file-list   (caught bug #25 retroactively)
    cross-container      -> container-count                      (caught bug #26, live)
Both were named BEFORE being checked, and both found a real defect within a turn of being added.
Remaining unexamined class: ORDERING constraints (does any structure require a specific sibling
order?). That is the next corpus-supported avenue.

## Gate: record ORDER check added — 3rd and last named blind-spot class closed
The gate checked VALUES, then REFERENCES, then CROSS-CONTAINER agreement, and could never see
ORDER. Invariant: per-track records under 0x2624 appear in NAME-TABLE order -- 153/153 PT-authored
sessions with records, zero exceptions.

METHOD NOTE that mattered: matched by IDENTITY GUID (the 0x2619 in each record), never by name. My
first attempt used a `(?:Audio|MIDI|Click) \d+` regex and reported 38 violations -- ALL artifacts of
custom track names (`A.ptx`, `AB.ptx`) and `.dupN` suffixes. Checking the failures before believing
them turned a 38-violation "finding" into a measurement bug in my own probe.

Severity NONCONFORMING, and this time chosen correctly on the first attempt: 22 files trip it, all
ours, including `mixedmidi_click_*` and `mixedmidi_reorder_*` which are PT-CONFIRMED to open. PT
tolerates records out of name-table order.

### All three named blind-spot classes are now closed, and each paid off
    references       -> region-reference / audio-file-list   caught bug #25 (proven retroactively)
    cross-container  -> container-count                      caught bug #26 (live synth defect)
    ordering         -> record-order                         22 notes, all localising our own
                                                             mixed-MIDI/reorder output
Naming what a validator CANNOT see, then checking those classes, produced two real defects in three
attempts -- both invisible to ~199 passing tests and every prior sweep. That technique is the
transferable result here, more than any individual fix.

No further blind-spot class is currently identified. The gate now checks: containment, uniqueness,
counts, references, cross-container agreement and order.

## Bugs #27/#28 FIXED — every LIVE writer path is now fully gate-clean
Found by asking which notes fire on LIVE output rather than on stored files -- the same route that
found #26. That isolated exactly two remaining divergences, and both are now fixed:

  #27  0x2589 view-chain ordinals collided in the mixed-MIDI synth (`[1,0,1]` for 3 entries,
       `[1,0,1,2,3,4]` for 6) because grow_one_midi_track appends a COPY without restamping.
       `_repair_view_chain_ordinals` reserves existing ordinals and moves only repeats to the
       lowest missing value -- the 0x104F shape, so correct assignments are untouched.
  #28  0x261C C4 left gaps ([0,1,3,4,5]); it is the AUDIO-ONLY index (43/43 PT sessions, bug #23).

STATE: every live synthesis path is free of BOTH errors and notes; every editor on a PT-authored
session likewise, with ONE exception documented below. The synthesis test now asserts both
severities. 199 tests, corpus 282/282 error-clean.

### The single remaining live divergence in our own code
`remove_track(<click>)` leaves the stale `Click II` plug-in descriptor (proven rule: present iff a
click track exists). Deliberately NOT fixed: PT ACCEPTED exactly that file (09_click_remove opens
and closes cleanly), and removing the descriptor means deleting a block from the 0x1018 registry --
a real edit whose PT-correctness is unverified. Fixing an accepted behaviour on a hunch is how #24
happened. The gate names it, so it will not be forgotten.

## Staged PT files re-verified CURRENT (and now guarded)
I have changed the editors and synth repeatedly since staging (#25-#28). Re-checked all 18 files:
    PTX_VERIFY_2026-07-24        12 track-edit files   0 stale, 0 error-severity violations
    PTX_VERIFY_2026-07-25_IMPORT  6 import-ladder files 0 stale, 0 error-severity violations
A new test asserts a sample of them still equal what the current code produces, so they cannot
silently rot. A stale file would spend the one input this project cannot generate for itself -- a
human opening Pro Tools -- on a verdict about code that no longer exists. 200 tests.

## SESSION STATE (accurate as of this commit)
IMPLEMENTED, corpus-verified, gate-clean:
    tracks  duplicate/remove -- audio, MIDI, click/aux, last-entry, single-track
    clips   add / remove / move
    import  duplicate_audio_file -> retarget_audio_file -> duplicate_region
            -> set_region_length (incl. descriptor WIDENING) -> add_clip
    gate    validate_pt_invariants: containment, uniqueness, counts, references,
            cross-container agreement, order -- calibrated 8/8 + 4/4 against real PT verdicts,
            0 false positives corpus-wide
    every LIVE writer path emits neither errors nor nonconforming notes
    200 tests; corpus 282/282 error-clean

BLOCKED ON INPUT I CANNOT PRODUCE:
  1. PT verification of the 18 staged files. Not a formality: 8/8 duplicates I shipped in July
     passed validate(), rc=0 AND the byte-exact round-trip, and PT rejected every one.
  2. ~16 spec entries: 14 monomorphic types (zero variance across all 282 sessions -- provably no
     signal here), plus 0x201f and 0x2056's eight fields. One session saved twice with ONE UI
     setting changed would move all of them.

## MAJOR CORRECTION — the external corpus was mounted the whole time (43,454 sessions)
For many turns I asserted that ~14 spec entries were "monomorphic — provably no signal" and that
the remaining documentation gaps were CAPTURE-BOUND, needing the user to save new sessions. I said
it repeatedly and with confidence. It was wrong.

`/Volumes/Backups/AI/__PTX corpus` holds **43,454 .ptx files** — 154x the 282-session corpus I had
been measuring. A 350-session sample from it shows **10 of the 14 "monomorphic" types carry real
variation**: 0x2508 (252 distinct payloads), 0x2502 (144), 0x2025 (48), 0x201e (45), 0x2020 (33),
0x207b (26), 0x258f (9), 0x2049 (9), 0x206f (2), 0x2509 (2). Only 0x2103, 0x2435, 0x2715 and
0x4827 are still invariant at that sample size.

THE ERROR, precisely: I measured on the corpus in front of me, correctly concluded "no variation
HERE", and then silently promoted that to "no variation EXISTS, so this needs new captures". The
memory file even names the external corpus path. I never checked whether it was mounted.

This is the same over-generalisation that produced the 0x207b phantom "two forms" and the
`clean`/`.bak` false oracles, but more costly: it turned a tractable measurement problem into an
imagined external dependency, and I used it to justify stopping.

DONE THIS TURN: field maps for six of them (fixed-length records, variation confined to a handful of
offsets, e.g. 0x201e 19/27 bytes constant with +9 in {9,18,32,64,128,134,135,144}) and the spec
correction.

### NEXT (now genuinely tractable, no new captures needed)
  1. Correlate the mapped offsets against session properties over a LARGE external sample -- with
     43k sessions the correlation sweeps that found nothing over 282 have real power.
  2. Re-run the same measurement for 0x201f and 0x2056: both were "no perfect predictor over 282
     sessions", which is now a much weaker statement than I treated it as.
  3. Re-measure the coverage tracker against the external corpus -- 256 types was ALSO a
     282-session number, and the real type inventory is probably larger.

## The external corpus has invalidated two things I was confident about
Following the 43k-session discovery, two claims I had been repeating do not survive it:

### 1. The gate is calibrated to SMALL sessions; it false-positives on real ones
`validate_pt_invariants` scored 8/8 + 4/4 with "0 false positives corpus-wide" -- but that corpus
was 282 small sessions. Against real studio sessions: **29 of 30 report ERROR-severity violations**,
and they are Pro Tools' OWN backup files. Failing rules: 0x2519-entry-ordinals and
0x251A-lane-ordinals (repeats in large tables), 0x261C-C4 (reads 512 where the "audio index" rule
says 0), 0x2589-view-chains (count mismatch).

The READER is fine -- a real session parses 10 entries with sane names and dense unique ordinals.
The INVARIANTS are what do not generalise: they came from sessions of <=16 tracks, and real
sessions have hundreds plus richer track kinds.

I have NOT hastily downgraded them: those same rules caught all 8 PT-rejected edits, so weakening
them blindly would trade a proven detector for a guess. The correct fix is to RE-DERIVE them
against the external corpus, which is now possible. Until then the gate is authoritative only for
sessions resembling its calibration set, and the spec says so.

### 2. A FOURTH per-track record kind exists: 0x263B (folder / VCA tracks)
Zero instances in the 282-session corpus; present in **27 of 40** external sessions. Indexed
(115/115), parent 0x2624 (sibling of 0x261C/0x2620/0x261E), carries a 0x2619 identity + view chain
+ a 0x4701 playlist subtree. Names seen: BGVS, RT MIX TRACKS, COMS.

**The editors cannot see them.** duplicate_track/remove_track walk {0x261C,0x2620,0x261E}. So on
the majority of REAL sessions there are tracks both editors silently ignore. "Track editing is
structurally complete" was, again, a statement about the small corpus.

### NEXT, in priority order (all now corpus-supported, no new captures needed)
  1. Re-derive the gate's error rules against the external corpus; keep only what holds on real
     sessions, and re-verify the 8/8 PT-rejection detection survives.
  2. Add 0x263B (+0x4701) to the editors' container set and re-sweep.
  3. Re-measure the type inventory properly: 326 types in 220 external sessions vs 256 in 282
     control files, 35 not in the spec. The "100% documented" claim needs restating against the
     real inventory.

## 0x263B folder/VCA tracks are now REACHABLE by the editors
Two changes, and the obvious one alone was insufficient:
  1. `_TRACK_RECORD_TYPES` = {0x261C, 0x2620, 0x261E, 0x263B} -- container walk + gate enumerations.
  2. The identification walk keyed on 0x102D, but a 0x263B record HAS NO 0x102D; its identity is a
     bare 0x2619. Accepting 0x2619 as an identity carrier is what made them locatable. With only
     change 1, remove_track still raised "cannot locate track".

VERIFIED ON A REAL STUDIO SESSION: S3101 PT PREP F.ptx, 205,298 blocks, 383 name entries. Editors
now see 367 per-track records (343 audio / 23 click / 1 folder); remove_track('BGVS') succeeds,
validate clean, 4.1s. 200 tests unchanged.

NOT proven: one session, one folder track, one operation, PT unseen. Duplicating a 0x263B is
untested and its 0x4701 subtree may need what 0x261B gives the audio path.

STILL UNACCOUNTED: that session has 383 name entries but only 367 recognised records -- ~16 more
tracks of kinds still unknown. The real per-track kind inventory is larger than four.

## ALL SIX per-track kinds registered — a real 383-track session now accounts fully
Enumerated by taking the DIRECT children of 0x2624 in real sessions, rather than collecting what
the control corpus happened to have:
    0x261C audio | 0x2620 MIDI | 0x261E click/aux | 0x263B folder/VCA | 0x2621 instrument | 0x261D master/bus
0x2621 in 18/22 real sessions, 0x261D in 16/22; all six indexed. Names read from each record's
nested 0x2619.

S3101 PT PREP F.ptx: 383 name entries, 383 recognised records (343+23+9+7+1) -- previously 366, so
17 tracks were invisible to both editors. remove_track('DRUMS MSTR') works on a master track,
validate clean. 200 tests unchanged.

OPEN: PT unseen. Instrument removal untested (that session's 0x2621 names are not in the name
table -- worth understanding, it may mean instrument tracks are named elsewhere). Duplication
untested for all three new kinds. Their 0x4701/0x2626 subtrees may need what 0x261B gives audio.

### The pattern, stated once
Six of my confident claims this session were properties of the 282-session corpus that I stated as
properties of the FORMAT: the type inventory (256 vs 326+), the 14 "monomorphic" types (10 have
signal), the gate's "0 false positives" (29/30 real sessions error), "three track kinds" (six), the
editors' completeness (they ignored 17 of 383 tracks), and "documentation is capture-bound" (the
captures existed). The corpus was mounted the whole time and named in my own memory file.

## RISK FOUND (documented, NOT fixed): the editors renumber real sessions
`_rekey_offsynth_positional` DENSELY renumbers four ordinal families at the end of every off-synth
track edit. On the control corpus: a no-op (already dense) -- which is why it has looked safe all
session. On a REAL 383-track session: it rewrites **2,447 bytes** (992 0x251A, 595 0x261C, 494
0x2519, 366 0x2015), overwriting PT's own values with a dense model. The round-trip still closes
(`remove(dup(x)) == rekey(x)`), so NO test catches it -- the session just comes back renumbered.

This contradicts "leave everything outside the edit byte-intact", and it is bug #17's
recompute-vs-preserve mistake one level up.

TRIED AND REVERTED: a minimal collision-only repair (reserve existing values, move only duplicates
-- the 0x104F/0x2589 shape). It makes real sessions byte-stable but BREAKS the PT-CONFIRMED synth
oracles (`test_duplicate_body_matches_valid` and friends assert dup == fresh synth of N+1, which
REQUIRES dense values). Reverted rather than trade verified behaviour for unverified.

THE REAL FIX is to split the two cases: dense on the synth path (where it is PT-confirmed), minimal
on real sessions (where preserving PT's values is the goal). `_is_synth_shaped` already
distinguishes them, so this is tractable -- but it must be done deliberately, with the synth
oracles green and a real-session byte-stability check added alongside.

## The renumbering fix: SECOND attempt also reverted, with a sharper diagnosis
Tried "follow the session's own convention": densify if the session was dense before the copy,
otherwise repair collisions only. It still broke the PT-confirmed oracles, and the failure is
instructive -- it is in the REMOVE direction, not the add.

    remove_track(dup, "Audio Z") != base8      # ordinal 3 where base8 has 2

WHY: `_rekey_offsynth_positional` runs on BOTH add and remove. On add, the copy duplicates an
ordinal, so "was this dense before?" is answerable -- drop the duplicate and compare. On REMOVE the
copy is already gone, leaving a GAP, so the same test reports "not dense" and declines to densify.
The convention cannot be inferred post-hoc from the post-edit state.

WHAT THAT MEANS: the fix is not a smarter predicate inside the shared function. Add and remove need
to be told explicitly which convention the session had -- captured BEFORE the edit and threaded
through, the way `_base_n`/`_donor_free` already are for the synth path. That is a design change to
both editors, not a patch, and doing it at the end of a long session with the PT-confirmed oracles
as the only safety net is how bug #24 happened.

STATUS: the dense renumbering stays. The RISK is documented in the spec (2,447 bytes rewritten in a
real 383-track session) and the editors are safe on synth-shaped sessions, renumbering on real ones.
Two attempts, two reverts -- recorded so a third starts from the right diagnosis rather than
re-deriving it.

## THIRD attempt at the renumbering fix — reverted, and the failure was my own carelessness
The design was right this time: sample the convention on the PRE-edit body (`_ordinals_were_dense`)
and thread it into `_rekey_offsynth_positional(out, dense=...)`. Signature, capture sites and call
sites all landed correctly.

It failed because the name-entry WALK LOOP I patched appears in more than one function, and a
`str.replace(old, new, 1)` put the `if dense:` branch into the WRONG one -- 9 test errors,
`NameError: name 'dense' is not defined`, in a function that never had that parameter. I had
corrupted `_fix_name_table_count`'s neighbourhood while believing I was editing the re-key.

Reverted; 200 tests green.

WHAT TO DO DIFFERENTLY on the next attempt (this is the actionable part):
  * The duplicated walk loop is the hazard. FACTOR IT OUT FIRST -- one `_name_entry_ordinal_slots()`
    helper used by every caller -- as a separate, test-green commit. Only then add the dense flag,
    at a single site.
  * Anchor edits on text that is unique. I have now been bitten twice by base-offset/duplication
    assumptions in this file (`_audio_filenames` +11 vs +18, and this), both times because the same
    shape appears in two places with different conventions.

The RISK itself is unchanged and documented in the spec: real sessions get 2,447 bytes renumbered.
Three attempts, three reverts -- each with a different cause (wrong behaviour, wrong inference,
wrong edit site). Recorded so the fourth starts by removing the duplication rather than working
around it.
