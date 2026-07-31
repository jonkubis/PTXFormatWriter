# ptxformatwriter — TODO

Open research / feature items. Each entry should say what we want, what's unknown,
and why it matters. (Resolved items kept briefly for provenance.)








## Item 11 CLOSED — the 82-track failure was the scan parser reaching the master index

The null `0x2501` child ref diagnosed above traces to `final_index._label_maps`, which enumerated
blocks with the **0x5A scan parser**. `_fill_offsets` resolves every donor child ref by
`(label, occurrence)` and **returns 0** when the occurrence is missing from the target — so a
block the scan misses becomes a NULL POINTER in the synthesized index.

At n=82 the scan found **one** `0x258a` and **one** `0x258b` where the size-driven walk finds two.
The donor references occurrence 1 of each, so both resolved to 0, plus a third null for the
`0x2501` sharing that record — three nulls, not the one my first probe counted (it only matched
`0x2501`-typed refs). n=81 and n=83 agree between the two walks, which is exactly why the failure
looked like an inexplicable single track count.

My earlier "ruled out: the scan parser" was **wrong**, and instructively so: I compared scan vs
size-driven for `0x2501` — the type named in the error message — and they agreed. The error names
the type whose ref could not be *parsed*, not the type that was *missed*. Comparing only the type
the symptom mentions is not a test of the hypothesis.

Fix: `_flat_blocks` now walks by declared size. Same medicine as every other reader in this
project; this was the last one still on the scan.

Safety, measured rather than assumed — `compose_index` is PT-confirmed byte-exact at many N:
**41 of 42 synthesis outputs are byte-for-byte identical** to HEAD across three specs (stereo /
mono / mixed) and fourteen track counts. The single difference is `mixed` at 82 tracks, which
used to RAISE and now produces a session validating clean. Full sweep of 8..140 for all three
specs: **no failures at any n**.

Three tests, all verified to bite (revert `_flat_blocks` to the scan and all four subtests error).
The scan undercounts the final 82-track session by 594 blocks across 23 content types, so the
unit test has real signal.

REMAINING from the synthesis group: item 13 (duplicated `0x2589` view-chain ordinals in
click-bearing synthesis).


## Synthesis items 11/12: item 12 REFUTED, item 11 localized to one byte (DIAGNOSED, NOT FIXED)

**Item 12 — "from n=83 every stereo synthesis trips the validator's CONTAINMENT error, including
2 errors at n=418" — REFUTED.** Swept `synthesize([2]*n)` for every n in 8..140 plus 200 and 418:
**zero validator issues at every n**, and no error at 418. The two claims the audit said "cannot
both be right" are not in conflict — the n=418 PT-confirmed record stands, and the containment
error does not occur. `synthesize_stereo_inline` is likewise clean at 10/50/81/82/83/84/120/418.

**Item 11 — "`synthesize()` raises ValueError at exactly 82 tracks" — CONFIRMED as to the number,
but NOT as a threshold.** Swept 8..140 for three specs:

| spec | failing n |
|---|---|
| stereo `[2]*n` | none |
| mono `[1]*n` | **82 only** |
| mixed `[1,2]*(n//2)` | **82 only** (the "83" in the audit is the same 82-track spec — `83//2 == 41`) |

n=81 and n=83 are clean. It is an isolated failure at exactly 82 tracks, mono and mixed only.

**Root cause, localized.** `synthesize_mixed_session` emits a `0x2587` index record whose THIRD
child ref — type `0x2501` — carries offset **0**, while all three `0x2501` blocks are present in
the body (at n=82: 238801 / 242598 / 267301). The audit's "the synthesized master index genuinely
contains a NULL pointer" is exactly right.

The reported symptom is a downstream artifact of that null. In `parse_records`, `is_child_ref`
requires the offset to be a known zmark; 0 is not, so the loop ends one ref early and reads the
ref's own type bytes as `entry_count` (`01 25 00 00` = 0x2501 = 9473), then hits a zero element
tag -> `bad element tag at 12257: 0000000000 (record type 0x2587 start 12216)`. Decoding by hand
confirms the record parses perfectly if that third child ref is accepted: entry_count 1, one
element with tag1=4, k=1, offset 0x0003b3a6.

**Ruled out:** the scan parser. `final_index.block_layout` (scan) and `_size_driven_blocks` return
IDENTICAL `0x2501` positions at n=81/82/83, so this is not the undercount/phantom defect that
explained so many other readers.

**NEXT STEP:** the null is already present in `synthesize_mixed_session`'s output — the raise
happens in the folder-leaf `rename_track` that consumes it, not in the rename itself. Find where
that `0x2587` record's `0x2501` child ref offset is resolved during the mixed-session index build
and why it yields 0 at exactly 82 tracks.

**Related, worth checking while there:** `final_index.offset_holes`'s `add()` silently RETURNS
when the offset is not a known zmark. That is why a null ref is never repaired by a later
reindex — it masks the fault rather than causing it, but it is the same "silently skip" shape as
several defects closed this session.


## session_info n_tracks undercounted (CLOSED); the `_lane_track_name` 'Z' item is REFUTED

**Item 8 — CONFIRMED.** `session_info()['n_tracks']` returned the `0x261B` count, with the
comment *"one 0x261B per track of any type"*. That comment was false: Pro Tools does not write a
`0x261B` for every kind of track. **114 of 352 sessions** across three drives undercounted — 46
reported against 52 real track records, 61 against 70. Now counts the track RECORDS
(`_TRACK_RECORD_TYPES`: audio / MIDI / click / folder / instrument / master / VCA), the same set
`remove_track`'s own guard uses. 114 -> 0. MIDI tracks are the common missing kind (a
`mixedmidi_5` control has 5 records and 3 `0x261B`); 69 control sessions exercise the difference,
so the test is not vacuous.

**Item 9 — `_lane_track_name()` returns the literal `'Z'` for a zero-length name — REFUTED, at
the source.** The function accepts a length only when `1 <= n <= 64`, so a zero-length name never
matches; it advances and ultimately returns `""`. There is no path that yields `'Z'` unless the
name genuinely is `"Z"`. 0 occurrences over 352 sessions as well. (Note the probe that produced
that 0 was itself wrong — it called the function with 2 of its 3 arguments and swallowed the
`TypeError`, so the corpus number alone proves nothing. The source does.)

Also corrected here: `session_info`'s `n_markers` comment still said "deduped", which stopped
being true when `markers()` moved to reading one `0x2030` instance.


## markers() lost distinct memory locations (CLOSED); the clips()/region_list() item is REFUTED

**Item 6 — `clips()` and `region_list()` "use DIFFERENT region index spaces" — DOES NOT
REPRODUCE.** Checked every clip in every session that has one: for each clip, does
`clips()[i]['name']` agree with `clip_names(lane_zmark)[clip_index]`? **0 disagreements over 139
sessions** (51 local + 88 PT-authored external). Recorded as unconfirmed rather than "fixed" —
there was nothing to fix.

**Item 7 — `markers()` collapsing 209 -> 112 — CONFIRMED, but the blanket framing was wrong.**
The reader de-duplicated by `(name, position)` because a session can carry a duplicate `0x2030`
list. Two separate findings:

* Where the ratio is exactly 1.0 or 2.0 and matches the number of `0x2030` instances (61 of 73
  PT-authored sessions with markers), the old reader was **correct** — the list really is stored
  twice. The audit's "collapses genuinely distinct markers" reading does not apply there.
* Where it is not (12 of 73), markers were genuinely lost. On `DWTS PT 2603 I.bak.023.ptx` one
  list holds **209 records with distinct ordinals 1..209** but only **112 distinct
  `(name, position)` pairs** — 30 of them named `' -'` at a single position. PT numbers memory
  locations 1..N and two slots may legitimately share a name and a position, so 97 real markers
  vanished. That is the audit's 209 -> 112.

Fix: scope to ONE `0x2030` instance instead of de-duplicating by content. Lossless — of 48
sessions with two or more instances, all 48 have instances identical in `(ordinal, name,
position)`, and no `0x2077` was found outside a `0x2030` in any session.

**A regression the measurement caught, not the tests:** anchoring on the first `0x2030` *in file
order* made `markers()` return **0** on 6 sessions that have markers — a session can carry
`0x2030` blocks holding no records. The anchor is the first list that actually HOLDS records.
Invariant now holds 75/75 (`markers() x instances == 0x2077 records`), with 0 zero-marker
sessions.

Selection extracted to `_first_marker_list_records(blocks)` so it is testable from block tuples
alone — `markers()` needs a master index, which a synthetic fixture has no cheap way to provide.
Four tests, verified to bite in both directions (content de-dup restored; anchor set to file
order).


## Name-based edits acted on EVERY match (CLOSED) — and the audit item's framing was wrong

Round-2 item 5 was filed as "the duplicate family never checks `new_name` is free, so the
identity round-trip can remove the wrong object", with the implied fix "refuse a name collision".
**Measurement refuted that fix.** Pro Tools writes duplicate names itself: across PT-authored
sessions on three drives, 20 of 108 carry duplicate REGION names, 5 of 266 duplicate TRACK names,
2 of 108 duplicate audio filenames. Refusing a collision would reject sessions PT produces.

The real defect is the name-based LOOKUP, and it is worse than "removes the wrong object" — it
acts on all of them:
- `remove_track` on a session with two tracks named `Audio 2`: **11 tracks -> 9**, the name gone
  entirely. A track the caller never asked to delete.
- `duplicate_track` from an ambiguous source: **11 -> 13**, producing TWO tracks named `FRESH`.
- `validate()` called both results clean.

Both functions DOCUMENTED the precondition ("Unique names." / "Constraints: unique source name")
and neither enforced it. Now they refuse the ambiguous CALL — not the session — and say how many
tracks matched.

Counting has to cover click/aux tracks: `track_types` returns 0 for `128 click tracks.ptx`, a
genuine 128-track session, so `_track_name_matches` takes the larger of the `track_types` count
and the 0x2519 name-table count. (This is the same blind spot that once made a track-count guard
refuse every edit on that session.)

Also aligned while here: `_name_table_shift` still computed `2 if flag else 0` while its docstring
— corrected earlier the same day — said the flag is a COUNT. Now `2 * flag`. Behaviour-neutral:
`_name_table_entry_start` steps forward until an entry parses, so both rules give an identical
entry start on 362 of 362 sessions. That empirical backstop is precisely why this reader was
unaffected by the flag bug while `eos_validator.check_name_table`, which trusted the flag
directly, raised false end-of-stream errors on flag=2 sessions.

Four tests, verified to bite.


## Appended siblings became children; out-of-range clip placement dropped silently (CLOSED)

Three more round-2 items, all measured on the control corpus.

**`duplicate_region` / `duplicate_audio_file` — the copy became a CHILD of the previous last
sibling.** Both append after the last framed entry and then grow "every container spanning the
insertion point" (`z < at <= e`). The last sibling ENDS at that point, so it was grown too. For
regions the last `0x2629` ends exactly where the `0x262A` list ends in 20 of 20 sessions, so the
list declared N+1 while holding N direct children — 52 of 52. Same for the `0x1003` run inside
`0x1004` — 52 of 52. A strict `z < at < e` is NOT the fix: it grows nothing, because the list
ends at the insertion point too. The right set is the insertion container and its ancestors,
stated as a containment test. `_splice_in` takes an optional `container=` for this;
`add_clip`/`remove_clip` keep the default (their splice points are strictly interior, so the two
rules coincide, and both are PT-confirmed).

**`duplicate_audio_file` never bumped the `0x1004` count.** `0x1004`'s u32 at +9 equals the number
of `0x1003` records in 52 of 52 sessions. Adding a registration left it at N. Now incremented.
Checked and NOT needed: the `0x103A` list has no entry count — the u32 before its entries is 0 in
all 52 sessions, so it is not the count it looked like.

`validate()` caught NONE of these: the gate covers neither `0x262A` nor `0x1004`.

**`add_clip_to_track` / `add_clips_to_tracks` dropped clips silently.** The lane helpers rewrite a
`0x1052` only when its index matches, so an out-of-range index rebuilt the block byte-identical
and returned a session carrying the clip's global blocks but no placement, with no error. On
`10 stereo tracks.ptx` (valid 0..9), indices 10, 60, -1 and -11 all gave 0 placements. Negative
indices never wrap — `2 * -1` is just a lane number nothing matches. Both helpers now raise and
name the valid range; `_place_clip_lanes` reports every unplaced index, not just the first.

Five tests, each verified to bite.


## validate() false positives — the pre-write gate was rejecting real sessions (CLOSED)

`validate()` is the gate every write path cites as its clean-result evidence, and its docstring
said "sound on every corpus session (no false positives)". Measured: **0 of 197** in
`control_files`, **25 of 75** on the external drives — a local-corpus claim generalised. Two
independent causes, both in `eos_validator.check_name_table`, both now fixed:

1. **The `0x2519` preamble flag is a count, not a boolean.** Read as `PREAMBLE_EXTRA if flag
   else 0`; the flag takes values 0 (255 sessions) / 1 (141) / 2 (6), and `flag=2` needs base+4.
   Now `PREAMBLE_EXTRA * flag`. Clean walk 394/402 -> 400/402. All 6 `flag=2` sessions are on
   external drives — the reason a rule "verified on 436 local sessions" survived.
2. **The `0x2A` at trailer+6 is a field value, not a frame marker.** 359 of 10,035 PT-authored
   entries hold something else there; 350 of those are followed by a correctly framed entry, so
   the walk was never desynced. Now raises only when the field is unexpected AND the next entry
   fails to frame.

End to end: `validate()` non-empty on PT-authored sessions **36/317 -> 0/317**.

Cost, measured rather than assumed: against deliberate in-place corruptions of 60 control
sessions (count +5 / first name_length +4 / first name_length -1) detection goes 60,58,58 ->
59,55,55 of 60. ~5% of synthetic desyncs now slip through in exchange for the false positives.
Also measured and **rejected**: additionally requiring the +18 ordinal to continue the run — no
extra detection (80/80, 79/80, 79/80 either way) and false positives 1/317 -> 7/317.

Four tests, each verified to bite by stubbing the branch it names. Note the trap found while
writing them: a bare `assertRaises` on a corrupted table passes even with the trailer branch
stubbed to always-in-sync, because clobbered bytes raise downstream on `name_length` instead —
the test has to assert `exception.field`.

Docstrings corrected in place: `validate()` (the false-positive claim, now stating the measured
rate and that the residual risk is the 23-vs-21 trailer stride) and the two "436 local sessions,
no exceptions" comments.


## THE RE-KEY WAS CORRUPTING REAL SESSIONS ON DRIVES I HAD RECORDED AS UNREACHABLE (2026-07-26)

`/Volumes/Mac 4TB SSD` (819 `.ptx`) and `/Volumes/Hack 2TB SSD Old` (1611 `.ptx`) ARE MOUNTED. The
"corpus disconnected" note below is about `/Volumes/Backups` only; 2430 further sessions were
reachable the whole time, and they are where the defects below live. Neither corpus I had been using
could have shown them.

THE TEST THAT FOUND IT: apply a positional re-key to an **unedited** Pro Tools session. Every byte
it writes is, by definition, a rewrite of PT's own value.

    corpus                      re-key is NOT a no-op        bytes
    control_files                 3/120 (our own fixtures)        6
    /Volumes/Samples              0/120                           0
    /Volumes/Mac 4TB SSD        115/120                       18062
    /Volumes/Hack 2TB SSD Old    14/120                        1088

**Defect 1 -- the 0x261C "C4" pass renumbered PT's own values.** Its guard was "some slot is
nonzero", calibrated on one vendor's writer. `/Volumes/Samples` reads clean only because THAT writer
puts a child block over the target offset, so the containment guard skips all 2580/2580 records --
a property of those files, not of the format. Elsewhere the slot holds real values (512, 729590018)
and they were overwritten with 0,1,2,... on records belonging to SURVIVING tracks.

FIXED with a conformance gate judged on the PRE-EDIT session (`_c4_uses_dense_convention`, passed in
as `c4_dense`). Judging POST-edit is what sank all four earlier conditional-renumber attempts: a
duplicate leaves a repeated value and a removal a gap, so a post-edit test calls a conforming session
non-conforming and skips exactly the renumber it needs. Result: C4 fires 115/120 -> 0/120 on Mac 4TB,
1/120 on Hack, and still fires on 92/120 controls that genuinely use the convention.

I GOT THE GATE WRONG FIRST, in a way worth remembering: I tested whether the read-back column was
dense `0..k-1`. But the pass numbers by RANK over all 0x261C records while SKIPPING child-covered
ones, so its own output is `[0,1,3]` when rank 2 is skipped -- my test judged the pass's own output
non-conforming, which would have made an edit stop repairing a session it had just written. The
correct test is idempotence: `value == rank`. That correction alone moved the control count 68 -> 92.

**Defect 2 -- `_give_copy_free_track_index` wrote an OUT-OF-RANGE C4.** Found only because the gate
exposed it: on `3 stereo plus click` the duplicate produced `[(0,0),(1,1),(2,4),(3,3)]` -- rank 2
holding 4 on a 4-record session. It had written "lowest free" on top of a renumber that had already
given the copy a distinct value, and it round-tripped ONLY because remove renumbered unconditionally
and painted over it. 0x261C dropped from that helper; the copy now gets an in-range value from the
renumber itself (`4 mono -> 5` now reads `[0,1,2,3,4]`, was `[0,1,2,3,5]`).

**Defect 3 -- an UNBOUNDED offset let a phantom block steer a write 1.31 MB away.** Exposed only
after defects 1 and 2 stopped masking it: Mac 4TB still showed 23/120 sessions changed, 46 bytes
(2 each). The `0x251A` lane-ordinal pass computed `pos = z + 15 + nlen + 18` where `nlen` is read
from the block's own `+11`, and wrote `su16(pos, k)` with no check that `pos` lies inside `[z, e)`.
On `M14 BOOM ZAHRAMAY_MIX.bak.047.ptx` a **290-byte** `0x251A` at 1977310 read `nlen=26403`, so the
write landed at 2003746 -- inside a `0x1038` audio payload.

The block itself is a PHANTOM, and that is not the bug: `0x5A` is `'Z'` and occurs freely in leaf
data, so the walk manufactures phantoms inside big payloads and always will. **The rule is that any
offset DERIVED from a block's own bytes must be bounds-checked against that block before it is
written** -- the same guard the post-ID writes already carry. My first instinct, a "reality
predicate" keyed on the 0x0002 master index, would have been wrong: most real blocks are nested and
never referenced by the index, so index-membership rejects far too much.

FIXED. The re-key is now a **complete no-op on unedited Pro Tools sessions: 0/120 changed, 0 bytes,
on all four corpora** (control_files, Samples, Mac 4TB, Hack 2TB) -- 480 sessions. Pinned by
`test_no_write_is_derived_from_a_phantom_block`.

## CLOSED — the 12% blind spot was PROTECTING a second bug (2026-07-26)

Both fixed. The blind spot below was real, and closing it alone would have caused corruption,
because two defects were cancelling:

1. `_name_entry_ordinal_slots` computed its start from `_name_table_shift` alone while
   `_inline_name_entries` resolves it EMPIRICALLY (a third preamble form exists). The slot walk
   returned ZERO on 69/612 sessions, silently disabling `_repair_next_kind_chain` and the ordinal
   renumber there. The shared helper `_name_table_entry_start` already existed and was not used.
2. `_renumber_name_entry_ordinals` forced the nonzero run to start at 1, on "300/300 real sessions".

Widening the walk first broke the no-op invariant -- 1257 bytes into 7 UNTOUCHED Pro Tools files.
Reverted, then measured across the whole population:

    reachable before the widening   543/543  nonzero == 1..k
    newly reachable                  27/34   nonzero == 1..k
    newly reachable                   7/34   distinct but starting at 2:  [2,3,4,5,6,...]

**Those 300 sessions were necessarily ones the SLOT WALK COULD REACH.** The 7 files that number from
2 were structurally excluded from the evidence that justified the rule -- so the walk's blind spot
had been preventing the renumber from rewriting them. Neither bug was visible until the other was
fixed.

The renumber now preserves the base (`base..base+k-1` from the first nonzero present); on a session
starting at 1 it is byte-identical to before, which covers every session the rule was measured on.
With that in place the walk fix landed cleanly: **0 bytes changed on all four corpora**, invariant
intact, blind spot closed.

THE LESSON, and it is the sharpest instance of the recurring one: *a rule measured through a parser
is only ever validated on what that parser can read.* "300/300" and "3379/3379 over 40 sessions"
both sound decisive and both silently exclude the population where they fail. Before trusting a
denominator, ask what the locator that produced it could not see.

## (historical) `_name_entry_ordinal_slots` FAILS SILENTLY ON 12% OF SESSIONS

Found while trying to diagnose the name-table off-by-one below. Two walks over the SAME `0x2519`
table disagree: `_inline_name_entries` parses 175-184 entries on the affected sessions while
`_name_entry_ordinal_slots` returns **zero**.

    slots == entries (works)             540/612    entries 1..512
    ZERO slots                            69/612    entries 0..210
    slots != entries                       3/612    entries 47..54
                                        --------
    silent no-op                          72/612    = 11.8%

WHY IT MATTERS: `_repair_next_kind_chain` guards with
`if len(slots) < 2 or len(ents) != len(slots): return data`, so on those 72 sessions it **looks like
it ran and repaired nothing**. Anything else keyed on those slots is equally blind there.

This is the failure shape this project keeps rediscovering: a locator that returns EMPTY instead of
raising, behind a guard that reads empty as "nothing to do". The 18-byte `_ORD2015_ANCHOR` missing
24.1% of blocks was the same shape, and it manufactured a false format rule that sat in the spec for
a day. Size is NOT the discriminator -- the working set spans 1..512 entries and the failing set
0..210, so the ranges overlap and something structural distinguishes them.

NOT FIXED, and deliberately not papered over. Two reasons to leave the behaviour alone until the
cause is known: it is a REACH limitation, not corruption (the repair declines; it does not write
wrong bytes), and making the guard raise instead would stop editing 12% of real sessions to fix a
no-op. What it needs is a walk that either parses those tables or says why it cannot -- an
investigation, not a patch.

Related and still unexplained: the 7 sessions whose name table is short by exactly one row (see the
guard's docstring). This is why that one is still an honest tolerance rather than a diagnosis -- the
GUID-ownership method that would have identified the missing record needs the slot walk, and the
slot walk is exactly what returns zero on those files.

## EVERY OBSERVED REAL CONTENT-TYPE IS NOW DOCUMENTED (2026-07-26)

    832 sessions, four drives   482 types   322 documented   160 phantoms   0 real undocumented

Read that precisely, because this file once carried a FALSE "100% documented":

  * **"Documented" means DESCRIBED** -- framing, parent/child, per-session counts, payload layout
    where decodable. It does NOT mean every field's MEANING is proven. Entries still say
    `purpose UNKNOWN` and mark fields `UNPROVEN`; that is the remaining semantic work and it is
    deliberately visible.
  * **C and D are SAMPLED** at 150 of 819 and 150 of 1610. A rarer type may exist outside the
    sample. The denominators are printed in the tracker for exactly this reason.

The last five needed dissection rather than filtering: `0x203f` (a SECOND 22-byte view-chain element
beside `0x203b`, same size and same `0x2015` parent -- so any locator keyed on
size-22-under-`0x2015` can match the wrong one), `0x2631` under `0x2632`, `0x255c` + `0x2557` under
`0x2551`, and `0x2757`, a lone 88 KB "block" under `0x1004` that survives all three phantom
signatures and is listed honestly rather than filtered by assertion. All PT-authored -- zero
instances in our own output, checked with `session_is_probably_ours`.

THREE PHANTOM SIGNATURES, each found by dissecting the survivors of the previous one:

    1. >=50% of instances nested inside a leaf payload (0x1038, 0x2613/5/6, 0x1003, 0x1017)
    2. low byte 0x00 and never index-referenced -- 256*k of a string LENGTH or first letter
    3. declared size 8 with parent 0x1014 -- the 8-byte id after the `2A 00 00 00` tag

Unfiltered, the walk reports 171 undocumented types. 166 are noise. Publishing that number would
hand the next reader a work list that is mostly garbage -- the same failure as the false 100%,
pointing the other way.

## FOUR UNDOCUMENTED TYPES, AND THE SAME SAMPLING TRAP A THIRD TIME (2026-07-26)

Diffing content-type sets across all four corpora (A/B exhaustive, C/D 150-session samples):

    corpus                      sessions   distinct ctypes
    A control_files                282           256
    B /Volumes/Samples             250           194
    C /Volumes/Mac 4TB SSD         150           421
    D /Volumes/Hack 2TB SSD Old    150           307

150 sessions from ONE new drive carry more distinct types than all 282 controls. 213 types appear in
C/D and never in A/B -- but **164 of those are phantoms**, and the naive reading ("213 undocumented
types!") would have produced a wildly wrong coverage claim. `0x1600` and `0x0c00` pass a
session-count filter and are still noise: exactly one per session, and `0x1600` = 256 x 22,
`0x0c00` = 256 x 12 -- a string LENGTH being framed as a block type. After filtering: 49 real,
38 already in the spec, **11 genuinely undocumented**.

REAL AND NEW: `0x261F > {0x2515, 0x2610 > 0x2514}` under `0x2624`, 527/527 containment, `0x261F` a
sibling of the six track-record kinds carrying the full per-track apparatus. Written up in the spec.

**AND THE HYPOTHESIS ABOUT IT WAS WRONG, refuted by my own test.** `0x261F` looked like the
alternate-playlist record: on the 16 sessions that surfaced during its dissection,
`n(records) + n(0x261F) == n(0x210B)` held 16/16. Across 87 sessions from all four corpora it holds
74/87, and it FAILS on the session that proved the ordinal rule (36 tracks, 0 x `0x261F`, 126
playlists). The 16/16 came from sampling only sessions that CARRY the family -- all one engineer's
project family.

**That is the third time in one session that a rule confirmed on a narrow sample turned out to be a
property of the sample** (after "distinct with gaps" from a locator missing 24.1%, and "dense 1..N"
from two single-playlist corpora). The pattern is now unmistakable: *sampling on the presence of the
thing you are studying guarantees a biased denominator.* Sample the population, then measure.

## A THIRD OF THE "PT ORACLE" IS OUR OWN OUTPUT (2026-07-26)

`control_files` has been the Pro Tools oracle for this entire project. **94 of its 282 sessions are
not Pro Tools saves.** A session records its own `.ptx` filename in the `0x2067` save-lineage block;
a donor-transplanted session keeps the DONOR's name there. Comparing the two:

    188  self-consistent (genuine PT saves)
     85  records a donor that IS still in the corpus   -- unambiguously our output
      5  records a donor no longer present             -- SYNTH_*, also ours
      4  near-identical name                           -- genuine PT sessions RENAMED on disk

So ~90 of 282 are ours. The 4 renames are why the sharper test (`session_is_probably_ours`) requires
the donor to be PRESENT, not merely to differ: `2 stereo plus click on top.ptx` records
`2 stereo click on top.ptx` and is a perfectly good PT file.

**Any figure ever quoted here as "n/n on PT-authored controls" that did not exclude these is partly
circular.** That includes several in this file. It also explains the earlier finding that 10
controls fail the `next_kind` chain and the `0x2015` ordinal invariant -- those 10 are a subset of
these ~90, and the two invariants were partitioning by provenance all along.

Shipped as `session_self_name()` / `session_is_probably_ours()` with
`test_a_third_of_the_control_corpus_is_our_own_output`, so a future sweep can filter rather than
rediscover this. USE IT before quoting any oracle statistic.

## THE 0x2015 ORDINAL IS A PLAYLIST INDEX — and I got it wrong once myself (2026-07-26)

`value[i] = 1 + (playlists owned by tracks before i)` — the 1-based position of the track's FIRST
playlist in the session-wide `0x210B` list. PROOF: `SANTA CLAUS IS COMIN.bak.011.ptx`, 36 tracks and
126 `0x210B`, reads `[1, 2, 12, 22, ..., 102, 103, ...]`; gaps `[1, 10, 10, ..., 1, 1, ...]` (tracks
3-12 own ten playlists each) and `sum(gaps) + 1 == 126` == the `0x210B` count exactly.

**Earlier the same day I wrote into the spec that "density IS the invariant, dense 1..N in 250/250".
That was wrong,** and it is a cleaner example of the corpus-width trap than the one it replaced:

    reading                                     corpus it was true on          why it failed
    "stable per-track ID, distinct with gaps"   a locator missing 24.1%        phantom gaps
    "dense 1..N, density is the invariant"      250/250 Samples + 255/255      single-playlist only
                                                controls                       (14 real sessions differ)

250/250 reads like overwhelming evidence and is exactly as narrow as 1/1: `control_files` is ONE
machine, `/Volumes/Samples` is ONE vendor, and every session in both owns one playlist per track.

**IMPLEMENTED AND TESTED, OPT-IN, DEFAULT OFF** -- `repair_playlist_ordinals_after_add` /
`..._after_remove`. Every duplicate leaves two tracks sharing an ordinal (226/226 control sessions),
so the defect is real, but Pro Tools opened, used and closed such a file cleanly (PT-confirmed this
session): it is FIDELITY, not a crash. The repair rewrites SURVIVING tracks -- everything below the
copy shifts -- which is the exact class of change reverted four times here, so the DEFAULT stays
where Pro Tools has confirmed it. Flipping it after one PT run is a one-line change, not a build.

STAGED FOR THAT RUN: `~/Desktop/PTX_VERIFY_ORDINAL_REPAIR/` -- an A/B pair of the SAME edit
(`4 mono tracks` + duplicate of `Audio 4`) differing in exactly this field:

    A repair OFF   0x2015 ordinals [1, 2, 3, 4, 4]   what ships today
    B repair ON    0x2015 ordinals [1, 2, 3, 4, 5]   identical to PT's own `5 mono tracks.ptx`

An A/B pair rather than one file, so a crash is attributable to the FIELD under test rather than to
the edit in general. B fine -> flip the default. B misbehaves while A is fine -> opt-in was right
and the ordinal is not free to renumber the way PT's own files suggest. Both outcomes are useful;
the READ ME states both so the result is interpretable without this conversation.

    counts[i]  = ord[i+1] - ord[i]        (last: n_0x210B + 1 - ord[last])
    add:    insert counts[src] at the copy's index, rewrite the column as prefix sums from 1
    remove: delete that entry, rewrite               <- exact inverse by construction

CONFORMANCE IS CHECKED THREE WAYS, all must agree: column starts at 1, strictly increasing, and its
gaps sum to EXACTLY the 0x210B count. The third is the one that earns trust -- it ties the column to
an INDEPENDENT object count instead of to itself, which is the minimum bar for a field that has been
documented wrongly twice.

    re-writing the column on an UNEDITED session        NO-OP on 337/337 conforming (of 492)
    duplicate -> repair -> remove -> repair             byte-exact 224/226
    unrepaired duplicates with a repeated ordinal       226/226
    repaired columns all-distinct                       216   (the other 10 are the
                                                              non-conforming files, declined)

The no-op result is the real validation of the FORMULA: recomputing the column from the counts it
derives reproduces Pro Tools' own bytes exactly, on every conforming session. A wrong formula would
have moved something.

The 2 inexact round trips are `robust_audio_mixed12` and `synth_4mono_8stereo`, whose malformed name
tables make the round trip inexact WITHOUT any repair -- the test gates on that baseline, because
the property under test is "does not break what worked", not "fixes pre-existing breakage".

**PROVEN BY NAME, not just arithmetic (2026-07-26, later).** `body_synth.track_playlists(data)` now
attributes playlists to tracks via this rule and the result is checkable against Pro Tools' own
`trackname.NN` convention. On the reference session, track `BD in.04` claims playlist indices 2..11:

    BD in.04, BD in.07, BD in.06, BD in.05, BD in.08, BD in.01, BD in, BD in.02, BD in.03, gutter1

Every playlist of `BD in` and nothing else. **36/36 groups** share their track's base name, and the
`gutterN` extra is systematic (one per multi-playlist track, numbered in track order). So `pc(src)`
above is no longer a bare subtraction on a column of unknown meaning -- it is a count of NAMED
objects that can be verified before it is written. That removes the main reason the writer change
felt unsafe; what remains is that it rewrites SURVIVING tracks, which still wants a PT run.

Playlists live in ONE flat `0x2107` container (126/126), NOT nested per track -- checked, because
per-track nesting would have made the count structural. It is positional instead.

## HOW FAR THE CRASH FIX REACHES — the ten-UUID identity is absent from half of two corpora (2026-07-26)

`_uuid_list_offset` finds the per-track ten-UUID list in:

    control_files          4525/4525   100.0%      PT Studio 2024
    /Volumes/Mac 4TB SSD   8866/8866   100.0%
    /Volumes/Samples        231/724     31.9%
    /Volumes/Hack 2TB      1252/2600    48.2%

`_reuuid_copied_track` returns the session UNCHANGED when the locator finds nothing, so on those
blocks the UUID half of the duplicate identity fix is inert. That is CORRECT -- the field is really
absent -- but it bounds what Pro Tools confirmed: the tested file (`4 mono tracks`, PT Studio 2024)
carries the list in 100% of its `0x261B`, so the UUID path was fully exercised there. On a session
without it, a copy's identity rests entirely on the `0x261B` INTEGER ID RANGE, which no PT run has
isolated.

THREE HYPOTHESES TESTED AND ALL THREE REFUTED, which is why this is a measured fact and not a defect:

  * *generational* -- no. Presence varies WITHIN a version: PT HD 10.3.0 72.2%, 10.3.2 67.5%,
    10.3.10 33.7%. (The banner regex also failed on 8896 blocks, reporting them as `?` at 100% --
    an artefact of the extraction, not evidence.)
  * *by track kind* -- no. audio 40.6%, click/aux 53.2%, master 61.1%: all intermediate, no split.
  * *the locator's hardcoded `count == 10` is over-specified* -- the attractive one, and REFUTED.
    Re-running the same signature with the count left free "found" a list in all 1231 missing
    blocks at counts 2..12, which looked exactly like an `_ORD2015_ANCHOR`-style over-specified
    constant. It is noise: those matches sit at block offsets **4, 10 and 5** -- inside the header,
    not near +1100 where the real list lives -- and **0 of 78** sessions had every id distinct
    across tracks, which is the property that makes them identities at all. An over-eager scan
    produced 1231 false positives; the distinctness check is what caught it.

WHAT WOULD SETTLE IT, and it is now STAGED:
`/Users/jonkubis/Desktop/PTX_VERIFY_NO_UUID_LIST/01 130 G/DUPLICATED 01 130 G Full Stereo.ptx`
-- a `/Volumes/Samples` session with **0 of 5** `0x261B` carrying the list, `Drums` duplicated as
`DrumZ`. The whole session folder is copied so Pro Tools can open it standalone; the original on
`/Volumes/Samples` is untouched. Checked before staging: reloads rc=0, 6 name entries == 6 track
records, all 6 integer id ranges distinct, `duplicate -> remove` byte-exact.

    opens and closes cleanly  ->  the integer id range ALONE is sufficient; the fix is general
    crashes, or DrumZ shows   ->  the UUID list was not the only colliding identity, and there is
    under Drums' name             another field to find on this generation

A crash there is a RESULT, not a setback: it would point straight at the remaining identity field,
the same way "both Audio 4 tracks are selected" pointed at the two selection flags.

## PT VERDICT #2: A REPEATED 0x2015 ORDINAL CRASHES PRO TOOLS (2026-07-27)

Reported on `PTX_VERIFY_ORDINAL_REPAIR/A repair OFF - 4 mono duplicated.ptx`:

> ugh, same issue again. Two tracks named Audio 4 and Pro Tools crashes on close

**That file was the deliberate "before" case of the A/B experiment, so the crash IS the result.**
The question the pair was staged to ask -- does Pro Tools merely never WRITE a repeated ordinal, or
does it REJECT one -- is answered: it rejects.

EVERY STORED NAME IN THAT FILE IS CORRECT AND DISTINCT. `0x2519`, `0x251A`, `0x1014`, `0x102D`,
`0x2619`, `0x210B` each hold exactly one `Audio 5`; the name table reads
`[Audio 1..Audio 5]`. So Pro Tools is not displaying a stored name -- it resolves the track through
the `0x2015` ordinal, and two tracks claiming slot 4 collapse into one object. Same signature as the
`0x261B` id-range collision, one field along.

    file PT CONFIRMED working    ordinals [1, 2, 3, 4, 5]   == PT's own `5 mono tracks.ptx`
    file that crashed            ordinals [1, 2, 3, 4, 4]

**IT WAS A REGRESSION OF MINE, bisected to `59add8f`.** At `388ec46` -- the PT-confirmed state -- a
duplicate emitted `[1,2,3,4,5]`; from `59add8f` on it emitted `[1,2,3,4,4]`. The correct value had
been an accidental SIDE EFFECT of `_give_copy_free_track_index`, which that commit changed. It is
now produced deliberately by `repair_playlist_ordinals_after_add`, DEFAULT ON, with the exact
inverse in `remove_track`, so it cannot vanish silently again.

**AND I TOLD YOU THE FILE WAS SAFE.** The READ ME said "A is what ships TODAY. You already confirmed
a file built this way opens... and closes cleanly." False: the confirmed file has `[1,2,3,4,5]` and A
has `[1,2,3,4,4]`. My own staging script printed both columns and I wrote the opposite.

18 STAGED FILES CARRIED THE REPEAT, including some from 2026-07-23/24 that predate this session. The
two that were tested and confirmed (`01_dup_4_mono`, `03_dup_3_stereo`) happen to be ones without
it -- so a repeated ordinal was very likely the ORIGINAL crash cause too, accidentally fixed on the
26th and re-broken the same day. All 12 regenerable staged files are now rebuilt and verified.

**A SECOND GAP, found while regenerating.** `01 130 G Full Stereo.ptx` has clean ordinals and ZERO
`0x210B` blocks. The conformance gate tied its counts to the `0x210B` total, so it declined and left
the copy sharing -- on a session where the copy would then crash. Real PT sessions do have no
`0x210B`. Fixed: a column that is exactly `1..N` is its own evidence, counts all 1.

    repair rewriting an UNEDITED session, four corpora:  0 bytes  (control 64, Samples 60,
                                                                  Mac-4TB 60, Hack 60 conforming)

## THE COMPLETE TRACK-IDENTITY MODEL, MEASURED (2026-07-27)

The spec described the track subtree as "GUID-keyed (8B GUID in `0x102d` after `2A 00 00 00`)" with
"exactly one `0x261B` per track of ANY type". Both halves are narrower than that. Measured across
four drives:

    field                         coverage
    0x102D block                  9945/9945 records, EVERY kind, all four corpora   100%
    2A GUID tag inside it         control 1464/1464, Mac-4TB 6311/6311,
                                  Hack 1694/1694  -- but /Volumes/Samples 376/476    79%
    0x261B                        audio, click/aux, master ONLY (100% of those);
                                  MIDI, instrument, VCA have NONE
    0x261B ten-UUID list          control + Mac-4TB 100%; Samples 32%, Hack 48%

`/Volumes/Samples` is PT HD 10.3/11.2 -- the same generation missing the UUID list -- so on that
vintage roughly a fifth of tracks carry NO GUID at the documented location. Combined with the
0x261B rules, a MIDI track in such a session has no identity field this codebase reads at all.

GUID COLLISIONS ARE NOT A REAL PROBLEM: 0/45 sessions on each external corpus. The 2/58 in
control_files are our own broken fixtures.

FOUR OF MY OWN PROBES WERE WRONG BEFORE THIS CAME OUT RIGHT, all the same shape -- an unscoped or
ambiguous search reporting something other than what it claimed:

  * "GUIDs collide in 13/150 external sessions" -- an unscoped `find(2A 00 00 00)` matching
    coincidental bytes anywhere in the record. Scoped to the 0x102D child: 0/45.
  * "10 of 16 records have no 0x102D" -- all 16 have one; my probe returned None when EITHER the
    block was missing OR it held no tag, and I read it as the first.
  * `meter_map` "the module's worst offender" -- applicability judged by block presence; 219
    sessions declare count=0 and an empty result is correct.
  * a clean 100% for `_uuid_list_offset` from `sorted(paths)[:280]`, which puts control_files first
    and truncated away the three drives that expose every real defect.

A probe is just another locator that can fail silently. The discipline this project applies to the
SPEC's claims -- scope the search, name the denominator, distinguish the failure modes -- has to
apply to the instrumentation with equal suspicion.

## WHICH IDENTITY MECHANISM APPLIES TO WHICH TRACK KIND (2026-07-27)

Follows from `0x261B` belonging to exactly three kinds. A track's identity is not one thing:

    kind                       0x102D GUID   0x261B id range   0x261B ten-UUID list
    audio 0x261C                    yes           yes             yes on newer sessions
    click/aux 0x261E                yes           yes             yes on newer sessions
    master/bus 0x261D               yes           yes             yes on newer sessions
    MIDI 0x2620                     yes           NONE            NONE
    instrument 0x2621               yes           NONE            NONE
    VCA 0x261F                      yes           NONE            NONE

Verified on the duplicate path: `8 MIDI tracks.ptx` has **0 `0x261B` before and after** a duplicate,
and `mixedmidi_4_AMSm` has 3 before and 3 after -- the MIDI copy adds none, so
`_reallocate_duplicate_track_ids` and `_reuuid_copied_track` CANNOT act on it. That is correct, not
a gap: there is no field to re-key.

The GUID path does cover it. Both sessions' copies introduce exactly **1 new GUID** and leave every
track GUID distinct (8 -> 9 and 4 -> 5). So a MIDI duplicate is identified by its `0x102D` GUID
alone.

**WHAT THIS BOUNDS.** Pro Tools confirmed an AUDIO duplicate (`4 mono tracks`), which exercises all
three mechanisms at once. A MIDI duplicate exercises only the GUID. Those are different code paths,
and only the first is PT-confirmed -- which is exactly what
`PTX_VERIFY_2026-07-24/A_no_audio_needed/01_MIDI_duplicate_track.ptx` was staged to answer.

## PT VERDICT: THE CRASH ON CLOSE IS FIXED (2026-07-26)

Reported from Pro Tools on `PTX_VERIFY_2026-07-25_GENUINE/A_mono_ladder/01_dup_4_mono_tracks.ptx`:

> fixes it. Both "Audio 4" and "Audio 5" are selected, but otherwise everything is good and Pro
> Tools doesn't crash on close.

**The crash on close is gone.** That is the first duplicate this project has produced that Pro Tools
opens, uses and closes cleanly. Which of the identity fixes did it is NOT established — the tested
file carried all of them at once (fresh `0x261B` id range, wholly-hashed UUIDs, a free dense
per-track index, and the head selection flag cleared). Do not attribute it to one of them in future
notes without an isolating test.

The residual selection display was a SECOND flag, and the verdict is what proved it: the tested file
already had the head flag cleared (`[0,0,0,1,0]`) and Pro Tools still showed both tracks selected,
so its displayed selection follows at least the tail flag at `0x2015 end-4`. Both are now cleared.

That flag was in front of me and I misread it. I had found the column, seen it disagree with the
head flag, and dismissed it as "the focused track" -- because I quoted it as `+254`, its offset in
the 258-byte `0x2015`, and `0x2015` is not fixed-length (258, 277, 218, 210, 230, 212 all occur).
At `+254` it reads past the end of four of those sizes and looks non-binary and unrelated. Anchored
to the block END it is binary in 226/226 sessions. **A field near the end of a variable-length block
must be anchored to the end** -- the same lesson the spec already records for `0x2010`'s trailing
footer, re-learned the hard way.

## THE SELECTION SYMPTOM IS NAMED, AND TWO WORSE DEFECTS FELL OUT OF LOOKING FOR IT (2026-07-26)

Pro Tools' report was *"Both Audio 4 tracks are selected"*. That half of the symptom now has an
exact cause, found by asking the ladder instead of guessing: each `0x2015` carries TWO binary
per-track SELECTION flags, at `zmark + 28` and at `end - 4`. `1..8 mono tracks.ptx` reads `[1]`,
`[1,1]`, `[0,0,1]`, `[0,0,0,1]` … — the just-created track, in both columns. Neither is a structural
"is last" bit: `1..4 stereo` have none set and `5 stereo` has all five, which is a user's selection
state, not a derived field. The columns do not always agree (`2 mono` reads head `[1,1]`, tail
`[1,0]`), so they are related but distinct state — a selection set and its anchor is the natural
reading, though which is which is UNPROVEN. Our duplicate read `[0,0,0,1,1]` in both where PT's own
5-track session reads `[0,0,0,0,1]`.

FIXED by clearing BOTH flags on the COPY only. Deliberately not PT's own behaviour — PT's Duplicate
Tracks MOVES the selection to the new track — because rewriting the source would break the
`duplicate -> remove` inverse, and "source selected, copy not" is a state PT itself writes.

The copy could not be found by its fresh GUIDs (its `0x2015` carries none), so it is identified
structurally: the added block is the one whose removal turns the post-edit run back into the
pre-edit run byte for byte (`_added_block_index`).

**Two defects found while verifying that, both worse than the one I was chasing:**

**1. `2A 00 00 00` is the GUID tag AND the number 42.** Track 42's own ordinal spells the tag, and
the 8 bytes after it — ordinary neighbours, not an id — are found inside EVERY track record. So
`Audio 42` resolved to all 512 records in `512 mono tracks.ptx`, all 128 in `128 stereo tracks.ptx`,
and so on in every session with >= 42 tracks. `duplicate_track` spliced the copy in once per match;
`remove_track` would have deleted every matched track. 15 of 4458 corpus tracks were ambiguous
(track 42 in 11 sessions, plus genuinely shared ids in `midi9`/`mixedmidi_9_mix3midi`, where one
seed occurs 18 times and lies inside two records). FIXED: a multi-way match is treated as NO match
and falls through to the positional rule, which resolves all 15. Note this is the same hazard as
the already-skipped offset-3 size-field case, at a position no offset skip can reach.

**2. The index-pointer block has no content_type.** The file's first block (offset 20, `size == 4`)
stores the master-index offset in its payload, so the frame reads the low half of that offset as a
type — a different session-specific value in every file. `_blocks_by_type` already skipped it
locally (it once zeroed four header bytes); `_size_driven_blocks` did not, so on a generated session
the value landed on `0x261C`, the walk counted a fifth TRACK RECORD in a four-track session, and the
name-table guard refused a good file. FIXED at the source (`_raw_block_bounds` tags it `-1`), so
every consumer is covered rather than each one re-learning it.

REVERTED ALONG THE WAY: a plain dedup of `pl_ranges`. Every track record holds exactly two of the
identity children (a `0x102D` and the `0x2619` nested inside it, 4458/4458), so a dedup looked
obviously right — but it changed nothing on 226 sessions, because the real problem was two DIFFERENT
records matching, not one record twice. Kept only as part of the ambiguity check above.

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

## A THIRD REAL-SESSION CORPUS, AND A NEW DEFECT FAMILY (2026-07-26)

`/Volumes/Samples` holds **250 more real sessions** -- found only by widening the volume search that
had previously stopped at four volumes and `maxdepth 6`. They contain NO `0x263B`/`0x2621`/`0x261D`
and no MIDI or click at all (250/250 are pure audio), so they do not close the documentation gap --
but they are an INDEPENDENT validation set the editors had never seen.

Sweeping them: **214/250 byte-exact, 36 failures**, all one family (`* Multi-Drums.ptx`,
`* Full Stereo.ptx`).

    ValueError: cannot locate track 'Kick In' (or an unsupported track type)

NOT a regression and not the master-track guard. The session is 12 tracks, ALL audio (`0x261C` x12),
with a complete-looking block set: `0x1014` x12, `0x102D` x12, `0x2619` x12, `0x251A` x24,
`0x1052` x15, `0x2624` x1. The track name is present with a valid length prefix in `0x2519`, in two
`0x251A` lanes, AND in a `0x1014` (z=41657, namelen 7). Yet `pl_ranges` comes out EMPTY for every
track, so `duplicate_track` refuses at the "cannot locate" guard.

**CAUSE FOUND: the track has TWO NAMES and they disagree.** The `0x102D`/`0x2619` records in this
family read `Audio 2`, `Audio 4`, ... while the display name in `0x2519`/`0x251A`/`0x1014` is
`Kick In`, `Snare Top`, ... -- the tracks were RENAMED in Pro Tools and only the second set followed.
Those same records carry **no GUID at all**, so neither of `duplicate_track`'s two routes to
`pl_ranges` can work: it searches for the display name (absent there) and seeds GUIDs (absent too).

Documented in the spec under "0x2619/0x102D can hold a DIFFERENT name from the display name".

THE FIX is not a patch to the search: a correct implementation must link the `0x2519` entry to its
`0x261C` record by POSITION or ORDINAL rather than by name or GUID.

PARTIALLY VALIDATED, and deliberately not implemented on the strength of it:

  * **entry count == track-record count in every session checked** (38/38, 56/56, 68/68, 23/23,
    24/24, 8/8, 34/34) -- a necessary condition for positional linkage, and it holds.
  * the ORDER is NOT yet validated. The obvious check -- does the Nth entry's name equal the Nth
    record's inner `0x2619` name -- is useless precisely BECAUSE of the two-names finding: it
    matches only 14/38, 15/56, 4/23, and a mismatch means "renamed", not "wrong order".

So the counts line up but the correspondence is unproven. Implementing positional linkage now would
rest on an unverified assumption, which is the specific mistake that produced nine failed diagnoses
earlier in this session. VALIDATE THE ORDER FIRST -- e.g. on sessions where the existing name/GUID
route DOES resolve, check that it picks the same record the positional rule would -- then implement.
The 36 sessions are the fixture, and the acceptance test is that they stop refusing.

The failure is SAFE (a refusal, not corruption) but it means `duplicate_track` cannot edit this
session family at all.

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

> **UPDATE 2026-07-26 -- HALF of this was fixed and half was still LIVE, and I nearly closed it as
> stale.** Re-tested against the current writer: the `next_kind` chain is CLEAN on every spec tried
> (`[1,0,2]`, `[1,0,2,0]`, `[1,0,2,0,0]`, `[1,0,2,2,1,2]`, `[1,0,2,0,2,0]`), so that half is genuinely
> fixed. But the same runs showed the `0x2015` PLAYLIST ORDINALS still wrong -- `[1,0,2,0]` emitted
> `[1,2,4,3]`, `[1,0,2,0,2,0]` emitted `[1,2,4,6,3,5]` -- numbered in CREATION order where Pro Tools'
> is a running total and strictly increasing. That is the exact shape of the fixtures below
> (`mixedmidi_5_AMSmM` = `[1,2,5,3,4]`), so the writer was STILL PRODUCING THEM. Fixed at the source
> in `synthesize_mixed_midi`; all five specs now conform. **The lesson: "I tested the thing the note
> names and it is fine" is not the same as "the note is stale."** The note named one symptom of a
> two-part defect.

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

REGENERATED 2026-07-26, twice: first with the head-selection, ambiguous-GUID and index-pointer
fixes -- that set is the one Pro Tools confirmed no longer crashes -- and again with the tail
selection flag. Every file: `rc=0`, name entries == track records, zero id collisions, zero UUID
collisions, and the copy is NOT selected in EITHER column (`01` now reads
`sel_head=1/5 sel_tail=1/5`, matching PT's own `5 mono tracks.ptx`; before the tail fix the same
file read `sel_tail=2/5`, which is exactly what PT displayed). All four duplicates round-trip
byte-exact. `PTX_VERIFY_2026-07-24/` regenerated too.

TODO NEXT: fix the SYNTH writer's next_kind so newly synthesized mixed sessions are valid, and
regenerate the 10 broken controls (they are checked-in artifacts used as test inputs).

## PARTIALLY UNBLOCKED (2026-07-26) — two other drives were mounted all along

`/Volumes/Mac 4TB SSD` (819 `.ptx`) and `/Volumes/Hack 2TB SSD Old` (1611 `.ptx`) are readable. They
do NOT replace `/Volumes/Backups` for the `.bak`-chain work described below, but they are real
third-party sessions from a different Pro Tools generation than either corpus used so far, and they
immediately exposed two live corruption defects (see the 2026-07-26 entry above). USE THEM: a rule
that holds on `control_files` + `/Volumes/Samples` alone has been wrong before, because those are one
machine and one vendor respectively.

## BLOCKED — the `/Volumes/Backups` corpus drive is DISCONNECTED (2026-07-25)

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

## The one `AttributeError` in the edit audit was a real out-of-bounds WRITE (fixed)
The 105-session edit-path audit came back with no INTRODUCED validator errors, but one session
crashed BOTH `duplicate_track` and `remove_track` with
`'NoneType' object has no attribute 'start'` — not a considered refusal like the `ValueError`s,
so worth chasing. It was a genuine memory-corruption-class defect.

DIAGNOSIS (the useful part — how it was found, since the symptom was six passes downstream):
  1. The index was locatable in the SOURCE and unlocatable after the edit, so the edit destroyed
     it — not a bad input.
  2. Wrapped every module-level function in `body_synth`/`final_index` with a predicate
     ("did a buffer that HAD a locatable index come out without one?") and let it name the first
     offender. That pointed straight at `_fix_container_counts` with no bisection by hand.
  3. Diffed the buffer: `5a 01 00 06 00 00 00` → `5a 00 00 00 00 00 00`. The payload after was
     byte-identical, so this was an in-place 4-byte write of zero onto a block HEADER, not a
     mis-sized splice.
  4. Logged every write the pass makes and filtered to that address: one write, from the
     `0x2107` at `z=190812, e=190829`, to `190830..190834` — outside its own block.

ROOT CAUSE: `_count_field_pos` returns `z + 18` (payload+9) for `0x2107` unconditionally. An EMPTY
`0x2107` has 10 bytes of payload and ends at `z+17`, so the count field it names does not exist.
The write landed on the NEXT block's `block_type` + `size`, which broke the contiguous top-level
chain walk that `final_index_ref` uses — so the master index became unfindable and every later
pass died on `.start`.

FIX: bounds-check both write sites in `_fix_container_counts` against the owning block's end, and
have `_count_field_pos` return None when the field would not fit. Same class as the `0x251A`
bounds-check in `21f281f` — that is now twice, so treat "compute an offset from a convention, then
write it" as requiring a bounds check by default in this file.

MEASURED:
  * The failing session now edits clean both ways, 0 new validator errors, index locatable.
  * Re-run of the audit: `AttributeError` gone from both columns. dup 57→61 clean, ValueError
    44→41 — so the corruption was ALSO surfacing as three silent ValueError refusals, not just
    the one crash. rm 93→94 clean. Still zero INTRODUCED rows.
  * `_fix_container_counts` remains a no-op on 480 untouched sessions (0 bytes).
  * The shape is rare: 0 of 267 `0x2107` containers in 480 sampled sessions are empty. The one
    session that has it has 25 tracks and NO playlists at all.

The regression test pins `_count_field_pos`'s arithmetic directly (its `0x210B` branch reads only
`z` and `e`), so it needs no external drive. VERIFIED TO BITE: with the guard removed it returns
118 for the empty case and the test fails.

## Name-table entries are UTF-8, and the 64-byte cap was ENDING the walk
Chasing the edit audit's refusal column paid off better than the crash did.

Of 102 real sessions, `duplicate_track` refused 41. Splitting those by reason:
  * 33 = "it is a master/bus track" -- an ARTIFACT OF MY AUDIT, which always targeted
    `names[-1]`, and the last name-table entry is usually the master. Retargeting to the first
    non-master track put the true clean rate at 94/102, not 61/102. Worth remembering: a
    refusal rate measured with a badly-chosen target measures the target, not the code.
  * 8 = "name table appears truncated" -- the real defect. All 8 were backups of ONE project
    (`NICK JR_ROUTINING_2019`), so this is a narrow shape, not a broad limitation.

ROOT CAUSE: `_name_entry_head` required every name byte in `0x20..0x7E` and capped the length
at 64. Both are wrong:
  * Names are UTF-8. The blocking entry was a clip called `Butterbeans Café ...`, and macOS
    stores that DECOMPOSED (NFD), so the `é` is `e` + U+0301 = `cc 81`. Non-ASCII -> rejected.
  * Real names reach 100 BYTES. The old cap was 64.

Neither failure reports anything -- the entry just doesn't parse and the walk ENDS. So the
table silently looks shorter than it is, and `_assert_name_table_complete` then (correctly)
refuses to edit it. 29 of 180 entries parsed.

THE ORACLE THAT SETTLED IT: each name table declares its own entry count. Against that count,
the corrected walk agrees on 32 MORE sessions and on ZERO FEWER (253 both / 32 new-only /
0 old-only / 3 neither). I did not have to argue the new parse was better -- the file says so.

I also nearly mis-called this. The corrected walk changed (not just extended) the parse on 5
sessions, which looked like a regression. It was the opposite: the new walk recovers a FIRST
entry the old one skipped (a name containing `·`), so those tables were previously off by one
at entry [0] -- and `_fix_name_table_count` would have written that wrong count.

MEASURED:
  * 288 sessions: 11,937 -> 14,948 entries parsed (+25%). 35 sessions gained, 0 lost.
  * All 8 previously-uneditable sessions now parse in full (e.g. 180 names / 179 records) and
    duplicate with 0 new validator errors.
  * Edit audit re-run: `rm` 94 -> 105 clean and its ValueError refusals went 8 -> ZERO --
    remove_track now succeeds on every real session in the sample. `dup` 61 -> 71 clean
    (the remaining 34 are the master/bus target artifact above). Still zero INTRODUCED rows.
  * True max name length is 100 bytes; cap now 255 with UTF-8 validity as the real guard.
  * The `0x2A` trailer marker is NOT usable as a validator: only 94.98% of entries have it.

STILL OPEN: 3 sessions where NEITHER walk matches the declared count. Not yet diagnosed.

### The no-op check on this one turned out to be the strongest evidence
I ran it expecting to defend against a regression -- the walk now finds MORE entries, and
`_fix_name_table_count` WRITES that count, so a wrong walk would corrupt untouched sessions.
It went the other way:

    _fix_name_table_count on 480 UNTOUCHED sessions
      PRE-FIX :  54 sessions modified, 61 bytes
      POST-FIX:   5 sessions modified,  5 bytes

So the short walk was writing a TOO-SMALL entry count into 54 untouched real sessions -- and PT
bounds its read of the inline list by exactly that count, which is the documented "end of
stream" trigger. That defect was invisible from the reader's side (rc=0 either way) and only
showed up as a no-op violation.

The residual 5 are all one project (`M20 PUP PUP BOOGIE_MIX`) and are presumably the same
sessions as the 3 "neither walk matches the declared count" cases. NOT yet diagnosed -- next
thread to pull on this file.

## The declared count arbitrates the parse (residual 5 sessions -> 0)
The 5 leftover sessions (`M20 PUP PUP BOOGIE_MIX`) had two names carrying a raw `0x10`
mid-string -- `DR PPB \x10OP HAT`, which is `DR PPB TOP HAT` in every other copy of the name.
The control-character guard ended the walk there: 7 entries parsed of 143.

I did NOT just relax the guard. Measured with the declared-count oracle over 280 sessions:
    strict      : 277 match
    permissive  : 279 match   <- gains 2 but LOSES 1
Trading a session away is not acceptable when the read feeds `_fix_name_table_count`, which
WRITES the count PT bounds its read by. So the table's own declared count now ARBITRATES:
strict first, permissive accepted only if strict disagrees with the declared count AND
permissive reproduces it exactly.

    arbitrated  : 280/280 match, no session traded away

And the write path is now perfectly inert on unedited sessions:
    _fix_name_table_count on 480 UNTOUCHED sessions
      before today : 54 modified, 61 bytes
      after UTF-8  :  5 modified,  5 bytes
      arbitrated   :  0 modified,  0 bytes

NUL stays rejected in both modes -- without that, runs of zero padding parse as names.

Edit audit unchanged by this (dup 71 clean / 34 master-bus-target refusals, rm 105 clean / 0
refusals, zero introduced errors), which is the expected result: arbitration fixes COUNTS, not
editability.

## 0x2506 SETTLED — the multiplier is the PLAYLIST count
    n(0x2506) == n(0x2077) * n_playlists,  n_playlists = n(0x210B) or (if absent) n(0x2015)

296/296 and 361/361 on two independent samples, ZERO non-integer ratios anywhere.

WHY IT RESISTED FOR SO LONG, AND THE GENERAL LESSON: nearly every session gives each track
exactly one playlist, so on ~96% of the corpus the playlist count and the track count are THE
SAME NUMBER. That is why `n(0x2015)`, the `0x2624` declared count and the name-table entry count
all tied at exactly 96.6% -- they were not three candidates, they were one number wearing three
hats. And it is why `n(0x210B)` looked REFUTED at 39.3%: 181 of 296 sessions carry no 0x210B
list at all, and for those the answer is not 0, it is one-per-track.

The 3.4% that missed were the only informative sessions in the corpus -- all multi-playlist
(DWTS PT 2603), M running 1-4 above the track count and matching n(0x210B) exactly. I found it
by dumping every count for JUST those six and reading across the row.

RULE TO REUSE: when several candidate quantities score identically, they are probably the same
quantity on most of your data. Go straight to the rows where they DISAGREE -- that is the entire
information content of the measurement. Averages over a corpus where the candidates coincide
tell you nothing.

Also consistent with the 0x2015 ordinal being a PLAYLIST index (spec §12): the playlist, not the
track, is what PT scaffolds per marker.

## Hunting for VARIATION is what moves "described" to "settled"
Several spec entries said, in effect, "this field never varies" -- and each was measured on 19-26
control sessions. I swept **1,532 sessions** across four drives looking specifically for the
sessions that BREAK those claims. Four broke.

1. **`0x206f` is a MIDI INPUT-DEVICE LIST, not a sentinel.** The entry called the payload
   invariant across 247 instances. It is invariant because the list is almost always EMPTY.
   Layout: `u16 @+0 = 1`, `u16 @+2 = device count`; when non-zero a `0x208C` follows holding a
   length-prefixed device NAME (`Alesis Vi61`, `OXYGEN 8`) wrapping a `0x208E`. Only 26 of 1,532
   sessions have a device at all. The old entry read the first 4 bytes as one u32 = 1; in the
   populated form they are `01 00 01 00` = 65537, which is exactly why the u32 reading looked
   constant. It is two u16s and the second is a count.
2. **`0x2018 +0` is not constant.** Recorded `= 1 (26/26)`; actually 1 x1411, **0 x120**,
   16777217 x1. The control set happened to contain no zero.
3. **`0x2435` is not one-project.** The entry flagged cross-project variance as UNPROVEN because
   all 7 instances were one project. It appears in **28 project families**. Caveat lifted.
4. **`0x2509`'s port pair varies** -- `(0,0)` x919, `(28283,28282)` x493, `(30,45840)`,
   `(30,40656)`, ... The entry's own caveat ("this user's setup, not a proven default") was
   right. The recurring `30` against a varying large value fits the port reading.

STRENGTHENED, not changed: `0x203f`/`0x203b` never share an innermost parent in 1,532 sessions
(72 vs 306,021 instances) -- alternatives in one slot, and `0x203f` is the rare setting.
`0x2511` still shows zero deviation from `Snap1..Snap48`.

THE GENERAL LESSON: "invariant across N instances" is worth very little when those N instances
are the DEFAULT state of an optional feature. 247 empty lists say nothing about a full one. Ask
what the field would look like if the feature were USED, then go find a session that uses it.

## 0x2506: two forms, a per-playlist bit, and a hypothesis I refuted
The spec called the 0x2506 payload constant. Over **779,382 instances in 240 sessions there are
exactly two forms**, differing at payload+2: `FFFFFFFF` (78.4%) vs `00000000` (21.6%).

Laid out as a marker x playlist matrix, every MARKER ROW is identical and the variation runs
along the PLAYLIST axis -- so the field is a per-playlist property replicated per marker. Most
sessions are uniform (51 all-FF, 5 all-00, 9 mixed); the 9 mixed ones carry the information.

I then guessed it was the mono/stereo flag. The track names in two sessions fit beautifully
(`Overheads`/`Room`/`Far Room` set, mono drum spots clear). Against ACTUAL channel counts it
scored **20.8% (10/48)** and failed wholesale in both directions. Refuted and recorded, because
the name-reading was convincing -- reading a story into a column of track names is not evidence.

Also measured: `0x2077` has NO child-count field for its 0x2506 children (best candidate 1.1%
over 5,332 markers where name length != child count, i.e. noise). So rows can be added/removed
with only size fixes and the offset shift.

## duplicate_track leaves the 0x2506 matrix a row short (open)
Now that the multiplier is known, the defect is exact: duplicate_track ADDS A PLAYLIST but adds
NO 0x2506 rows, so the session ends up short by exactly n(0x2077) -- confirmed on 8 real
sessions (short by 6,7,7,7,6,7,9,9 = their marker counts). remove_track has the inverse gap.

**THE UNCOMFORTABLE PART: every PT confirmation we have of duplicate_track is on a MARKERLESS
session**, where n(0x2077)=0 and the deficit is identically zero. So this defect has never been
in front of Pro Tools. Fixing it needs the per-playlist bit above decided (which form to emit)
-- OR the observation that in a uniform session every row is the same byte string, which covers
51+5 of 65 sessions. NEXT: implement for the uniform case, refuse/warn on mixed.

### ...and the attempted FIX is reverted (3 wrong walks, and it made things worse)
I implemented `sync_marker_playlist_rows` + wired it into `duplicate_track`. REVERTED. Recording
it because the next attempt should not re-derive any of this.

The measurement stands and is worth having: `duplicate_track` leaves the matrix short by exactly
n(0x2077), on 79/79 marker-bearing real sessions, and the relation held 79/79 BEFORE the edit
(a third independent confirmation of the 0x2506 rule).

WHERE IT WENT WRONG -- the row-run locator, three times:
  1. FORWARD scan from payload start, resyncing on the next 0x5A: desyncs when a 0x5A occurs in
     the marker's variable-length header. Reported 0 rows for 6 of 388 markers that hold 140 each,
     and the caller then silently SKIPPED those markers (short by 6 instead of 0).
  2. BACKWARD walk from the marker end in 17-byte steps: the rows are NOT the trailing bytes --
     there is a ~23-byte trailer after the last row. Reported 0 rows for EVERY marker.
  3. Find the first fully-framed 0x2506 then step forward 17 at a time: this LOCATES correctly
     (51/79 sessions then satisfied the relation) but the splice still destroyed one 0x2077 in
     the other 28.
So the rows are a contiguous run with a variable-length header BEFORE and a ~23-byte trailer
AFTER. Any future walk must handle both ends; two of my three attempts assumed one of them away.

A SEPARATE BUG I FIXED ON THE WAY AND SHOULD KEEP IN MIND: when a marker is itself the top-level
block, `tz == z`, so the marker's size field IS the container's -- incrementing both applied the
growth TWICE. Set sizes ABSOLUTELY (`span - 7`), never incrementally.

WHY IT IS REVERTED RATHER THAN LEFT IN: with the sync wired in, 28 of 79 sessions LOSE a 0x2077.
I verified this is mine, not pre-existing -- with the sync stubbed out, 0 of 79 lose one. Today
duplicate_track produces a short matrix; with my patch it produced a short matrix AND destroyed a
marker. Strictly worse, on a code path Pro Tools has confirmed, with no way for me to PT-test it.

NEXT ATTEMPT SHOULD START BY: writing the locator against a FIXTURE with a known row count and
asserting it round-trips, before touching duplicate_track at all. All three of my walks would
have been caught in minutes by that. Then check whether the new playlist's 0x210B block is even
being created -- n(0x210B) does go +1, so that part is fine.

## The 0x2506 row sync LANDED (second attempt, prototyped outside the module)
Reverted once yesterday-in-this-session; now shipped. `duplicate_track`/`remove_track` add/drop
one 0x2506 row per marker for the playlist they add/remove, so the matrix stays exact.

WHAT MADE THE DIFFERENCE, and it is the transferable part:
  * **The settled relation became an ORACLE for the locator.** `n(0x2506) == n(0x2077) x
    n_playlists` means every marker must hold exactly n_playlists rows -- so I could measure the
    LOCATOR ALONE against 3,038 markers in 94 sessions before touching a write path. It came back
    100% correct, which immediately told me the locator was never the bug. My first attempt spent
    three rewrites on the locator because I had no way to test it in isolation.
  * **Prototyped in scratch, not in the module.** The repo stayed green the whole time.

THE TWO REAL BUGS (both in the SPLICE, both invisible without the oracle):
  1. **Only the top-level ancestor was resized.** Markers nest `0x271A -> 0x2030 -> 0x2077`, and
     the intermediate 0x2030 needs the growth too. Leaving it 204 bytes short made its child walk
     stop early and the LAST marker of each group vanish from the block stream. (Same shape as the
     0x2718-wraps-a-nested-0x2028 note in the tempo work -- an intermediate container's size.)
  2. **Sizes were written at ORIGINAL offsets into an already-mutated buffer.** Row counts came
     out 211,210,209,...,203 -- a cascading drift that reads exactly like a locator bug and is
     not one. Fix: process markers right-to-left and write each marker's size WHILE its offset is
     still valid; containers start below every insertion so theirs never move.

VALIDATED: locator 3,038/3,038 markers; sync 94/94 sessions (markers preserved, +1 row per
marker, exact byte growth, index resolves, 0 new validator errors); add-then-drop round-trips
BYTE-IDENTICAL at two insertion indices; relation holds after duplicate_track 79/79 and after
remove_track 80/80 (was 0/79); edit audit unchanged (dup 71 clean, rm 105 clean / 0 refusals).

SAFETY PROPERTY THAT MATTERS MOST: on MARKERLESS sessions -- every shape Pro Tools has confirmed
-- the new pass is a STRICT NO-OP, verified byte-identical on 66 duplicated sessions. So this
cannot regress anything already confirmed. It is still PT-UNCONFIRMED on marker-bearing
sessions, which is now the fourth thing worth putting in front of Pro Tools.

The regression test uses a fixture with a bare 0x5A in the header AND a trailer after the last
row, and is verified to BITE: both earlier locators return 0 rows on it, the shipped one 3.

## The 0x2506 per-playlist bit: still undecoded, and the near-miss is the lesson
Three hypotheses tested against the bit, none survives:

  * **track is STEREO** -- 89.5% on MIXED-bit sessions (77/86), but 38.1% on all-FF and 23.7%
    on all-00; 48.4% overall. REFUTED.
  * **track timebase** (tick vs sample) -- could not even be aligned: 0x1052 lanes are per
    CHANNEL, so a 6-playlist session has 10 lanes. Worth remembering on its own.
  * **track has clips** -- 72.1% inverted / 27.9% direct on mixed sessions. Noise.

THE METHODOLOGICAL POINT, and it is the exact MIRROR of what cracked the 0x2506 multiplier:
there, throwing away the rows where all candidates agreed was the entire insight -- because I was
testing an EXACT ARITHMETIC RELATION, and the agreeing rows carried no information. Here, the
same restriction MANUFACTURES a result: 89.5% on mixed sessions looks like a decode and is a
selection effect, because a CORRELATION was being tested, and the agreeing rows are exactly where
it has to be checked.

    Restrict to the disagreeing cases to FIND a hypothesis. Test on everything to KEEP it.

Had I quoted the 89.5% alone I would have shipped a wrong rule with convincing evidence attached
-- the same failure as the earlier "it fits the track names" reading, one level more subtle.

STILL NEEDS PT: one session saved twice with a single playlist-level setting changed.

## Closing out four "not yet measured" caveats with one sweep (1,532 sessions)
Each of these was a spec entry that named its own missing measurement. Three are now settled and
the fourth is settled as UNSETTLEABLE from the corpus, which is also worth knowing.

  * **0x2435 -- fully resolved.** The entry flagged BOTH project-specificity and cross-project
    size/value variance as unproven. 558 instances across 28 project families are **exactly ONE
    distinct byte form**, span 84 throughout. I had explicitly written "whether the bytes stay
    identical across those 28 is a separate measurement, not yet made" -- so I made it.
  * **0x2715 -- structure settled, semantics provably not.** All 894 instances in 1,532 sessions
    are a single byte form; the 51-byte payload is exactly three length-prefixed strings with
    nothing left over: key `sessionMetadataBase64`, value `AQAAAAAAAAA=`, encoding `Base64`. The
    value base64-decodes to `01 00 00 00 00 00 00 00` = u64 LE 1, confirming the recorded
    round-trip. What the 1 MEANS cannot be settled here: the corpus is monomorphic, so every
    reading (format/version vs id vs flag) fits equally.
  * **0x2511 -- strengthened.** One byte form across all 1,532 sessions; nothing deviates from
    `Snap1..Snap48`. Noted in the spec that this does NOT support the 'Snapshot preset names'
    reading: a value nobody has ever changed carries no evidence about its purpose.
  * **0x2612 -- rarity quantified.** ZERO instances in 1,532 sessions. The entry said "lives in
    only one project family"; that understates it. Its field widths cannot be settled by corpus
    reading at ALL, so it belongs on the PT list rather than the corpus list.

WORTH SEPARATING, because I have now hit it twice today: "byte-identical everywhere" settles
STRUCTURE and says nothing about MEANING. It settled 0x2435 completely (the open question there
WAS variance) and settles nothing about 0x2715's u64 or 0x2511's names (where the open question
is purpose). Recording which kind of question an entry is asking is what makes the difference
between a measurement that closes it and one that just adds a bigger number.

## A measured NEGATIVE: six unsettled fields are not derived counts
Several of the still-unsettled entries speculate that a varying field is a count of something
(0x2020's entry literally says its a/b/c/d were "tested against block-type population counts,
only inconsistent/coincidental matches"). I made that test exhaustive rather than anecdotal.

For 0x2049, 0x104d, 0x2018, 0x2020, 0x207b and 0x2056: every varying u16/u32 in the first 64
payload bytes, tested for equality against 16 measured session quantities (track / playlist /
marker / region / clip / lane / plugin / block / 0x2015 / 0x202a / 0x2519 counts, sample rate,
bit depth, tempo- and meter-event counts, and the type's own instance count), across 440
sessions. Candidate fields per type ranged 19-129.

    RESULT: no field matches any known quantity in every session. Zero hits, all six types.

That is worth recording precisely because it is negative: it rules out the cheapest explanation
for all six at once, so nobody re-runs it a third time, and it moves these entries from "maybe
corpus-answerable" to "PT-gated" on evidence rather than on my impression. These fields are
stored settings/UI state, not anything derivable from the session's own structure.

STATE OF THE UNSETTLED LIST: 28 -> 26 typed sections. What remains is dominated by window
geometry, view/zoom state, and UI flag semantics -- questions of the form "what does this flag
DO", which by construction need the application. The corpus can still be mined for STRUCTURE
(and today it gave up 0x206f's device list, 0x2435's cross-family identity, 0x2715's full
payload, and the 0x2506 relation), but it cannot assign meaning to a UI toggle nobody toggled.

## CORRECTION: the 0x2506 rule is PER MARKER; my aggregate form was overstated
I committed `n(0x2506) == n(0x2077) * n_playlists` as settled on "296/296 and 361/361, zero
exceptions". A third sweep with a different seed found a counterexample in the first 386 sessions.

`2 BUSS for Jon.ptx` carries TWO 0x2030 marker lists whose markers DISAGREE: 8 markers hold 69
rows (its 69 playlists) and 8 hold 1 (its single track). Total 560, against the 1,104 the product
predicts.

The robust statement is the PER-MARKER one, and it is exact:

    rows(marker) == n_playlists,  for every 0x2077     385/386 sessions, no exception WITHIN them

The aggregate follows only when every marker agrees. TWO lessons, both mine:
  * **"N/N exact" on a random sample is not universality.** Two clean samples of ~300 sessions
    missed this; a third with a different seed hit it immediately. Vary the seed before writing
    "no exclusions".
  * **Stating a per-object rule in aggregate form hides which object it belongs to**, and breaks
    the moment two populations of that object coexist in one file. The per-marker form would have
    survived unchanged.

## marker-scaffold-matrix added to validate_pt_invariants (NOTE, not error)
Checks that every 0x2077 holds exactly n_playlists 0x2506 rows -- the defect the row sync fixes.
NOTE severity deliberately: 1 of 386 real PT sessions is legitimately ragged (above), so an error
would reject a file Pro Tools wrote. Silent on all 440 untouched sessions; verified to BITE
(drop one row per marker -> fires, naming "markers hold [4] rows but the session has 5
playlists").

## NOTICED, NOT YET ACTED ON: 0x2519-entry-ordinals errors on files PT itself wrote
Across 440 untouched sessions the only ERROR-severity findings are `0x2519-entry-ordinals` (23)
and `per-track-subtree-ordinal` (2). The validator's docstring says "error" means "PT is known to
REJECT these", so an error firing on 23 sessions Pro Tools authored contradicts that contract --
the same shape that got `track-id-uniqueness` and `0x2589-view-chains` demoted to notes earlier.
NOT changing it now: it may be calibrated against a real PT verdict, and I already made one hasty
change today that had to be reverted. Investigate before demoting: does the 23 come from the
locator misreading a second layout (as with track-id-uniqueness), or is the ordinal genuinely
non-conforming in those files?

## ANSWERED: 0x2519-entry-ordinals was MY bug, and the answer was the third option
I left this open asking "is the 23 the locator misreading a second layout (as with
track-id-uniqueness), or genuinely non-conforming ordinals?" Neither. It was a byte-vs-character
length bug in the validator, and I had armed it earlier the same day.

    check("0x2519-entry-ordinals", [... data[p + len(n) + 18 : p + len(n) + 20] ...])

`_inline_name_entries` returns DECODED names, and names are UTF-8, so `len(n)` is the CHARACTER
count. Any entry after a name containing a non-ASCII character had its ordinal read two bytes
early -> garbage value -> spurious "repeated ordinal" ERROR.

THE DIAGNOSTIC THAT SETTLED IT IN ONE STEP: split the 440 sessions by whether they contain a
multi-byte name.

    error + non-ASCII names : 23        clean + non-ASCII names : 19
    error + all-ASCII names :  0        clean + all-ASCII names : 398

Zero all-ASCII sessions fire. That is not a pattern any "second layout" story produces.

**THE PART WORTH REMEMBERING: fixing a reader can ARM a bug that was previously unreachable.**
This arithmetic was always wrong, but before today's UTF-8 fix the entry walk STOPPED at the
first accented name, so those entries never reached the line. Making the parser more capable
pushed new data into old code. After a reader fix, re-examine every consumer of what it returns
-- a second site (`_guid_after`, same function) had the identical bug and was fixed with it.
A third site at line ~6540 already used `len(name.encode())`, so the codebase knew; the two
validator sites were simply missed.

RESULT: ERROR-severity findings on 440 untouched sessions 25 -> 2, and the surviving 2 are OUR
OWN stale synthesized outputs (`test_clickbottom_3stereo.ptx`, `mixedmidi_click_bottom.ptx` --
`mixedmidi` is referenced in tests/test_body_synth.py), both showing the repeated-ordinal defect
that actually crashed Pro Tools and that the editors now repair. So: **zero error-severity
findings on genuine PT-authored sessions**, and the two that fire are true positives.

The regression test pins the CONTRACT (`name_end` advances by BYTE length) rather than the two
call sites, because the next consumer to get this wrong will be a third site.

## A track whose NAME has an accent was not editable at all (two bugs, one behind the other)
Following my own "after a reader fix, re-audit every consumer" note found a user-visible defect
the validator work had only hinted at. Names are UTF-8; anything that MEASURES one must count
BYTES. Two places did not, and the second was unreachable until the first was fixed.

  1. **`track_types()` returned mojibake.** It decoded names as latin1 (`_hdr_name`), so
     `135·3BPM` came back as `135Â·3BPM` -- a string whose `.encode()` is NOT in the file. Since
     the editors locate a track by `track_name.encode()`, **a name read from the public API could
     not be passed back into `duplicate_track`/`remove_track`**: "cannot locate track", on 42 of
     42 corpus sessions carrying a non-ASCII name (71 names, 0 usable).
  2. **`duplicate_track` compared name lengths in CHARACTERS.** `len(new_name) != len(source)`
     then `_unique_same_length_name(len(source))` -- which produces a name of exactly N BYTES. A
     22-character/23-byte name took a 22-byte placeholder and spliced ONE BYTE SHORT, corrupting
     the master index into "bad element tag". This only became visible after fix 1, because
     before it the lookup failed first.

THE DIAGNOSTIC THAT SEPARATED THEM: try an accented-name track and an ASCII-name track IN THE
SAME SESSION. 28/28 sessions gave "bad element tag" for the accented one and "OK" for the ASCII
one -- which rules out "this session is broken" and points at the name itself.

FIX: `_decode_name()` (UTF-8 with a latin1 fallback, so it is a strict improvement -- valid UTF-8
now round-trips and invalid bytes behave exactly as before) applied at `_hdr_name`; byte-length
comparison in duplicate_track. There are still ~20 other latin1 name decodes in this module,
listed by `grep -n '\.decode(' ptxformatwriter/body_synth.py`; they feed region/clip readers
rather than the edit lookup, so they are a smaller (but real) follow-up.

MEASURED: accented-name tracks 0/28 -> 28/28 duplicate CLEAN with no new validator errors; all
71 non-ASCII names across 42 sessions now round-trip to bytes present in the file (was 0/71);
edit audit dup 71 -> 72 clean, refusals 34 -> 33; rm unchanged at 105 clean. 232 tests green.
The regression test runs entirely on a local control file (rename a track to `Café`, then
duplicate it) and is verified to bite -- reverting `_hdr_name` alone fails it.

## ...and the loose end is closed: region_names and clips were broken the same way
I flagged "~20 other latin1 name decodes remain, a smaller but real follow-up". Rather than
reason about 20 lines, I tested WHICH PUBLIC READERS actually produce a name that is not in the
file. That found the two that mattered in one pass:

    reader          non-ASCII names   NOT in file (broken)
    track_types                  71                      0   (fixed last commit)
    region_names               2932                   2932
    clips                      2305                   2305
    markers / clip_lanes          0                      -   (no non-ASCII names produced)

Both trace to the SAME two lines (`region_names` and its `_region_list_names` helper), now on
`_decode_name`. After: 0 broken across all three readers -- 5,308 non-ASCII names, every one
round-tripping to bytes present in its session.

THE METHOD IS THE POINT, and it is the third time today it has been the fastest route: define
the property the correct code must have (a name must re-encode to bytes IN the file), then let
the corpus tell you which call sites violate it. Auditing 20 decode lines by reading them would
have been slower and less certain -- and would not have told me that `markers` and `clip_lanes`
were already fine, which is how I know the remaining latin1 decodes are not name readers at all.

The remaining `.decode("latin1")` calls in this module are WAV/RIFF chunk text, plugin-id
FourCCs and index-record tags -- byte-oriented data where latin1 is the correct choice, not
user-facing names.

## core.py had the same bug -- and my first fix REGRESSED the parser
`core.PTFFormat.parsestring` decoded latin1, so `regions()` and `tracks()` returned mojibake:
2,560 and 1,930 non-ASCII names, EVERY ONE not present in the file it came from.

**MY FIRST FIX BROKE THE PARSER AND I ALMOST SHIPPED IT.** Changing the decode alone made some
sessions return ZERO regions and ZERO tracks (`M15 CHASE SONG_MIX.bak.014`: 539/462 -> 0/0). The
cause is the lesson from two commits ago, firing again immediately: FIVE parse loops advance
their cursor by `len(decoded_string)`, so making the decoder correct armed five latent
byte-vs-character bugs at once.

    pos += len(wavname) + 4        j += len(regionname) + 4       j += len(trackname) + 5
    j += 4 + len(midiregionname)   j += 4 + len(regionname)

Fixed by advancing with the u32 LENGTH PREFIX (`self._read4(pos)`), which is exact regardless of
encoding and cannot drift again. Same for the version `skip` at line 463, which was also
computing a PARSE OFFSET from a decoded string.

**WHAT CAUGHT IT: a before/after comparison on real sessions, not the test suite.** All 232 tests
passed with the broken version -- the control files are pure ASCII, so nothing exercised it.
Running the OLD and NEW code over the same 60 sessions and diffing (region count, track count)
is what exposed it, and it is the check to run for any change to a parser:

    region/track counts differing from before: 0   (must be 0)
    names not present in their file: before 1068 -> after 0

RULE, now demonstrated three times in one session: after fixing a reader, (a) grep every
consumer of what it returns for length arithmetic, and (b) diff old vs new behaviour over the
corpus. A more capable parser pushes new data into old code, and a green suite proves nothing
when the fixtures do not contain the data that got more capable.

## CORRECTION + close-out: I asserted the name audit was finished; it was not
Two commits ago I wrote that the remaining `latin1` decodes "are NOT name readers -- they are
WAV/RIFF chunk text, plugin-id FourCCs and index-record tags". That was reasoning from a
SPOT-CHECK of five readers, and it was wrong: `region_list` is a name reader, and every one of
its 1,690 non-ASCII strings was absent from the file it came from.

I found it by doing systematically what I had previously done by hand -- enumerate EVERY public
single-argument reader by reflection (30 of them), walk whatever it returns for strings, and
assert the property "a non-ASCII name must re-encode to bytes present in this session".

    BEFORE the sweep : region_list 1690 broken   (region_names/clips already fixed)
    AFTER            : 0 broken across all 30 public readers

Also swept the whole package for the arming pattern behind the core.py regression -- any `len()`
of a value that came from `.decode(`, `parsestring(` or `_decode_name(`, both as a variable and
inline. **Zero remaining instances** in any module. The two surviving `latin1` decodes in core.py
are the deliberate fallback inside `parsestring` and a 4-byte type code; both correct.

THE LESSON IS ABOUT MY OWN CLAIM, not the bug: "I checked the important ones and the rest are a
different kind of thing" is an assertion dressed as a finding. The reflective sweep costs one
script and answers the question for the whole API instead of the part I happened to look at --
and it immediately contradicted me. Prefer enumerating the surface to sampling it whenever the
property is cheap to state.

## The write layer, swept the same way: identity edits must be byte-exact
Applied the enumerate-the-surface method to the 38 public mutators. The cheap universal property
for the in-place ones is: **an identity edit must return the input unchanged.** Two failed.

    move_clip(same pos)      122/122 byte-identical
    rename_track(X->X)       156/156
    set_session_name(same)   160/160
    sync_marker_rows(0)      160/160
    replace_tempo_map(same)  141/160   <- 19 sessions rewritten, up to 820 bytes
    replace_meter_map(same)   46/56    <- 10 sessions, 8 bytes each

TEMPO, two distinct causes, both now fixed:
  1. `_resize_tempo_block` replicated RECORD 0 as the template for all N records, discarding
     per-record bytes. `0x2029` had had exactly this bug and was fixed long ago
     (`_resize_meter_safe`); `0x2028` was missed. Now clones per index; template only for
     records grown beyond the originals. (19 -> 15 sessions.)
  2. The patch loop set BOTH `+21` and `+39` to `1 if idx==0 else 0`. Measuring 3,247 real
     records shows only `+39` is the first-record flag (1 in 309, essentially all index 0);
     **`+21` is an INDEPENDENT per-record boolean** -- set in 1,419 of 2,938 non-first records,
     disagreeing with `+39` in 1,417. Deriving it from the index rewrote real data 1->0 in 1,247
     records. Now preserved rather than derived. (15 -> 2 sessions.)

    RESULT: replace_tempo_map identity 141/160 -> 158/160, readback still exact, a real
    single-event replace still works, 232 tests green.

WHY THIS MATTERS BEYOND TIDINESS: `replace_tempo_map`/`replace_meter_map` are PT-CONFIRMED write
paths. Silently zeroing a per-record flag in a confirmed path is how a confirmed path stops being
correct without anyone noticing -- and no test caught it, because identity-byte-exactness was
only ever checked on the 26-session corpus, where it held.

STILL OPEN (characterised, not fixed):
  * meter identity rewrites a u32 in the 0x2029 TAIL (past the records) on 10 sessions --
    `fe ff ff ff` (-2, a renumbered start bar) -> `01 00 00 00`. The reader does not expose it,
    so `meter_map` round-trips while the bytes do not.
  * 2 tempo sessions still differ; not yet characterised.
  * `+21`'s MEANING is unknown. Preserving it does not require knowing it, but decoding it needs
    PT (one session saved twice with a tempo event changed from constant to ramp).

## Meter identity is byte-exact too: the trailing entry has NO single formula
The spec recorded the 16-byte trailing entry's leading i32 as the START-RELATIVE bar
(`bar - start_bar + 1`). Measured per event across the corpus:

    matches BOTH forms (start_bar == 1, where they coincide) : 419
    only relative : 48        only ABSOLUTE : 22        NEITHER : 20

Relative fits 467/509, absolute 441. Neither is the rule, and they agree on 82% of events --
which is exactly why one of them looked universal. So a writer must not impose either.

FIX: rewrite a trailing entry ONLY when its event's bar actually changed (captured before the
record is overwritten); otherwise leave it alone. Identity replace 46/56 -> 55/56 byte-exact,
readback unchanged, a real meter replace still works. 232 tests green.

THE PATTERN ACROSS TODAY'S THREE CONDUCTOR FIXES IS ONE RULE: **a writer should preserve every
byte it is not deliberately setting.** Record cloning per index (tempo + meter), leaving `+21`
alone, leaving an unchanged trailing entry alone -- three instances of the same discipline, and
each one was a case where the code CONFIDENTLY DERIVED a value that real sessions disagreed with.
Deriving is only safe where the derivation has been proven exhaustive; preserving is always safe.

WRITE-LAYER IDENTITY STATUS: move_clip, rename_track, set_session_name, sync_marker_playlist_rows
all byte-exact everywhere tested; replace_tempo_map 158/160; replace_meter_map 55/56. The three
stragglers are uncharacterised and are the next thread here.

## Write-layer identity is now clean: 160/160 tempo, 55/56 meter
The last two tempo stragglers were the SAME lesson a third time. `+39` is the first-record flag
in 3,245 of 3,247 real records -- but two sessions of one project carry it on record **2**. So
even that one is not derivable, only overwhelmingly likely.

FIX (and it generalises): a record that ALREADY holds its target event is left completely
untouched. Identity replace then cannot rewrite anything by construction, and every per-record
exception -- known (`+21`) or unknown -- survives. tempo 158/160 -> 160/160.

THE REMAINING METER STRAGGLER IS NOT A DEFECT: `SYNTH_meter_4-4_to_7-8_bar3.ptx` is OUR OWN
output (the `SYNTH_` prefix), carrying a bar number written by an older formula -- the current
code computes 3 for a file whose own name says bar 3, and the stored value is 2. A stale
artifact, not PT data. Worth regenerating rather than "fixing" the writer to match it.

    move_clip / rename_track / set_session_name / sync_marker_playlist_rows : byte-exact
    replace_tempo_map : 160/160        replace_meter_map : 55/56 (the one above)

THE RULE THAT CAME OUT OF ALL SIX FIXES TODAY, worth applying to every future writer:
**touch only what actually changes, and preserve everything else.** Every one of these defects
was the code deriving a value that real sessions disagree with -- record contents, `+21`, `+39`,
the meter trailing entry. Each derivation was measured and TRUE for 82%-99.9% of the corpus,
which is exactly why each survived so long. A no-op edit that is byte-exact BY CONSTRUCTION does
not depend on any of those derivations being right.

## rename_track: NO defect, a guard I tried and rejected, and a real spec finding
Tested the inverse property `rename(A->B); rename(B->A) == original` across the corpus.

    LONGER name   138/138 byte-identical
    same length   137/138
    SHORTER        128/138

The 10+1 failures looked alarming (one round-trip grew the file by 1,518 bytes). They are an
ARTIFACT OF MY TEST NAMES. `rename_track` matches LENGTH-PREFIXED occurrences, which is strong,
but for a 1-character name `01 00 00 00 <byte>` occurs by chance in binary payloads: after
renaming a track to `Z`, the session contained 83,096 raw `Z` bytes and some carried a matching
length prefix, so renaming BACK hit unrelated data.

**REAL short names are fine: 56/56 tracks whose name is 1-2 bytes (`OH`, `TC`, ...) round-trip
BYTE-IDENTICALLY.** So there is no user-facing defect, and I did not ship a fix for one.

TWO GUARDS TRIED AND REJECTED, both on measurement:
  * **Block-type allowlist** (accept an occurrence only inside known name-bearing types). The
    8-type list I measured separated real from accidental sites with ZERO overlap on 98
    sessions -- and then refused 6 of 138 LEGITIMATE renames, because track names also appear in
    `0x202a`, `0x261f`, `0x2628`, `0x4501`, `0x4702`, `0x4809`, `0x207a`. My sample used only each
    session's FIRST track name and under-sampled the sites. Same trap as the 0x2506 multiplier
    and the meter trailing entry: a rule that is perfectly clean on the sample I drew.
  * **Minimum name length** (refuse renaming from a name < 3 bytes). Would refuse 100 real
    tracks, and those demonstrably work.

A guard that blocks legitimate work to prevent a failure nobody can hit is worse than no guard.

THE REAL WIN IS THE SPEC FINDING: a track's length-prefixed name legitimately appears in
**0x1014, 0x1052, 0x1057, 0x2015, 0x2519, 0x251A, 0x210B, 0x2619** *and also* **0x202a, 0x261f,
0x2628, 0x207a, 0x4501, 0x4702, 0x4809** -- notably 0x261f (the VCA-shaped seventh track-record
kind documented today) and 0x2628 (a REGION named after its track). Any future name-based edit
must expect all of them; the shorter list is what an eight-type sample looks like.

## The staged PT experiments were STALE — regenerated (this is the one that mattered)
The PT queue is the binding constraint on this project, so a stale staged file wastes the
scarcest resource there is: the user actually sitting in front of Pro Tools.

CHECKED all pending experiments against current code:
  * `PTX_VERIFY_2026-07-24/A_no_audio_needed/*.ptx` (8 files, the MIDI + mixed set) -- all
    MARKERLESS, so today's 0x2506 row sync is a no-op on them. Still current, 0 validator
    errors each. Leave them.
  * `PTX_VERIFY_NO_UUID_LIST/.../DUPLICATED 01 130 G Full Stereo.ptx` -- **STALE**. Built 26 July,
    it carries 35 of the 42 0x2506 records its shape requires: short by exactly 7, one per
    marker. Testing it would have told us about code that no longer exists.
  * `PTX_VERIFY_ORDINAL_REPAIR` -- already ANSWERED (the crash was the answer); optional.

REGENERATED with current code as `CURRENT 2026-07-27 - DUPLICATED 01 130 G Full Stereo.ptx`;
the stale file is left beside it for comparison, clearly marked do-not-test in the READ ME.

A USEFUL CONSOLIDATION: `01 130 G Full Stereo` has 7 markers AND lacks the 0x261B UUID list, so
this ONE file now covers BOTH open experiments -- the no-UUID-list identity path and the
marker-bearing duplication that today's row sync fixed. That drops the PT queue from four items
to three, without dropping any coverage.

VERIFIED BEFORE STAGING: markers 7->7, scaffold 35->42, playlists 5->6, the markers x playlists
relation exact, ordinal column [1,2,3,4,5,6] with no repeat, zero new validator errors,
re-encrypts and reloads byte-identically.

GENERAL POINT WORTH KEEPING: **re-verify staged artifacts after every code change that could
touch them.** Ten commits landed today; one of them silently invalidated a file that had been
sitting on the Desktop waiting to be tested. Staging is a snapshot, and snapshots go stale.

## The read layer's output could not be fed to the write layer (set_markers)
Extended the identity sweep to the mutators not yet covered. Results:

    set_track_names(current names)   138/138 byte-identical
    move_clip(same position)         111/111
    reorder_tracks(identity order)    72 byte-identical, 66 refused (ValueError)
    set_markers(markers(d))          FAILED on ALL 88 sessions that have a marker

The set_markers failure was not a subtle one: `markers()` returns DICTS
(`{"name","tick","sample"}`) and `set_markers` took `(name, ticks)` PAIRS, so the obvious
composition raised "too many values to unpack". **A read layer whose output its own write layer
cannot consume is a gap in the API, not an inconvenience** -- and no test covered it because
every existing caller builds the tuple list by hand.

FIXED: `set_markers` now accepts either form. 63 of 88 sessions round-trip their marker list
EXACTLY (names and ticks).

THE OTHER 25 EXPOSED A REAL FORMAT LIMIT, not a bug: a marker is either TICK-locked or
SAMPLE-locked (spec §10, top position byte 0x40 vs 0x00). `markers()` reports a sample-locked
one as `tick=None` with the value in `sample`, and `set_markers` only emits the tick encoding.
Those 25 sessions therefore cannot be round-tripped at all. They used to fail deep inside
`int()` with "int() argument must be a string, a bytes-like object..." -- a message that never
mentions markers. Now they get a refusal that names the marker and says why.

WRITING sample-locked markers is a genuine open feature (the encoding is documented in §10, so
it is implementable), not a defect -- recorded as such rather than papered over.

    NOT chased: reorder_tracks refuses 66/138. Its refusals may be entirely legitimate (it is
    documented for synth-shaped sessions), but the ratio deserves the same split-by-reason
    treatment that turned duplicate_track's "40% refusal rate" into an artifact of my harness.

## reorder_tracks: NOT a defect either -- my harness, for the FOURTH time today
I flagged "reorder_tracks refuses 66 of 138" as deserving a split-by-reason. It did, and the
answer was the same as every other refusal-rate scare this session.

    62 of 66 refusals:  "new_order must be a permutation of 0..N"

I built the permutation from `len(track_types(d))`. The function documents that it indexes
`track_playlist_order(d)` -- a DIFFERENT list (all playlist kinds, including click/aux, which
track_types omits). The two differ by up to 62 entries. With the documented length:

    identity reorder byte-identical : 101
    changed                         :   5
    refused "bad element tag"       :   8   (a pre-existing index-parse issue on those files)

And the 5 "changes" are not a defect either. `track_playlist_order` is CREATION order;
`_container_display_offsets` is the CURRENT DISPLAY order. So `list(range(n))` does not mean
"leave it alone", it means "reset the display order to creation order" -- which correctly
rewrites index offsets on any session whose tracks were reordered in Pro Tools. Every diff was
inside the master index with the length unchanged, exactly as an index-only edit should be.

THE TRUE IDENTITY TEST -- reorder to the session's OWN current display order -- is byte-identical
on 85/85 sessions where the two lists align.

TWO LIMITS OF THAT TEST, stated because they matter:
  * ZERO sessions in the sample had a display order differing from creation order, so it did not
    exercise a genuine reorder. It shows the no-op path is faithful, not that reordering is.
  * Sessions where `len(display) != len(playlists)` were SKIPPED, and those are probably the
    same files as the 5 above. That shape is uncharacterised.

FOUR TIMES IN ONE SESSION a "high failure rate" has turned out to be my test harness: the
master/bus target in duplicate_track, the block-type allowlist sample, the `list(range(n))`
permutation here, and the wrong-length permutation before it. **Before reporting that code
refuses N% of real sessions, check what the API says its inputs MEAN.** The docstring for
reorder_tracks names its index list in the first sentence.

## The display-order path was reading through the SCAN parser (two fixes, one open finding)
Chasing the `bad element tag` refusals found that the master index parses cleanly on 140/140
sessions when given SIZE-DRIVEN block sets. The failures came from the two readers feeding it
scan-derived data instead.

  1. `track_playlist_order` enumerated via `flat_blocks(parse(data))`. The scan parser does not
     raise here -- it SILENTLY UNDERCOUNTS: **37 playlists where there are 119**, 29 where there
     are 104, on **31 of 140 sessions (22%)**. Verified the extra blocks are real: all 119 are
     DIRECT CHILDREN of the 0x2624 track list, not nested strays. `reorder_tracks` indexes this
     list, so a caller would have been reordering a third of the tracks and writing an index
     element list that omits the rest. Now size-driven.
  2. `_container_display_offsets` passed `block_layout`'s scan-derived sets to `parse_records`,
     which frames elements by "is this offset a live zmark" -- a short zmark set makes a real
     element look like garbage. That is the actual source of `bad element tag ... 0x2624`, on
     **33 of 140 sessions**. Now size-driven: 33 hard failures -> 0.

    TRUE identity reorder (to the session's own display order): 85/85 byte-identical, unchanged.
    232 tests green.

OPEN, CHARACTERISED, DELIBERATELY NOT FIXED: on 54 sessions the index display list and the
playlist list still differ in length, and the display list contains offsets that are NOT of the
three types `track_playlist_order` filters for. The 0x2624 element list covers ALL track kinds --
including 0x261D master, 0x261F (the VCA-shaped kind documented today) and 0x2621 -- while the
filter is `(0x261C, 0x261E, 0x2620)`. Extending it changes `reorder_tracks`' INDEX SPACE, which
is a behavioural change to a PT-CONFIRMED function, so it deserves its own pass rather than
being tacked onto a reader fix at the end of a long day. Until then those sessions simply refuse
(a caller cannot build a valid permutation), which is a safe state, not a corrupting one.

## Display-order path FIXED end to end: 139/139 identity, real reorders work
Finished the thread I had deferred. Three changes, each measured:

  1. `_DISPLAY_ORDER_TYPES` -- the `0x2624` display list indexes SEVEN track kinds, derived by
     resolving every entry of every display list back to its block: 6,456 entries over 139
     sessions, ZERO unresolved. 0x261c audio 4029, 0x261e click/aux 1819, 0x261d master 274,
     0x261f VCA 248, 0x2620 MIDI 70, 0x2621 15, 0x2623 1. The old `(0x261C, 0x261E, 0x2620)`
     filter left the enumeration short on 54 of 139 sessions. NOTE it is NOT `_TRACK_RECORD_TYPES`
     -- that set carries 0x263b, which no display list has ever pointed at, and including a type
     the list does not contain would make the enumeration LONGER than the list it indexes.
  2. `_container_display_offsets` -- size-driven sets into `parse_records` (was `block_layout`).
  3. `reorder_tracks` -- same fix; it had its own scan-derived call.

    TRUE identity reorder:  85/139 with 54 mismatches and 33 hard errors  ->  139/139 byte-identical
    real reorder (move last displayed track to top): 104/104 took effect, size unchanged,
    zero new validator errors. 232 tests green.

THE THREAD IS WORTH RE-READING AS A WHOLE, because I was wrong twice inside it and the corpus
corrected me both times: I called the scan parser's short list an "undercount" before checking
(it WAS one -- all 119 blocks are direct children of 0x2624), then assumed my size-driven list
was complete when it was still missing four track kinds. Each step needed its own measurement;
neither was deducible from the previous one.

## Sample-locked markers can now be WRITTEN (the last named non-PT item)
`set_markers` emitted only the LOW 5 BYTES of the position, so the u64's TOP byte -- the one
`markers()` decodes the timebase from (§10: 0x40 tick-locked, 0x00 sample-locked) -- came from
whatever the template happened to be. Every marker written was therefore forced to the
template's timebase, and a sample-locked marker could not be expressed at all. That is why the
writer had to refuse 25 of 88 corpus sessions.

FIX: `_build_marker_record` takes an ENCODED u64 position and writes all 8 bytes at both
name_end and name_end+8; `set_markers` builds it from whichever field the caller supplied
(`tick` -> `0x4000000000000000 + ZERO_TICKS + tick`, `sample` -> the plain count).

    all tick-locked sessions      63/63 round-trip EXACT (names, ticks and samples)
    sample-locked present         25/25 round-trip EXACT   (was: refused outright)

Also fixed `markers()`' own name decode, which was still `latin1` -- the same class as the five
readers corrected earlier. It had not surfaced because no marker in the sampled sessions has a
non-ASCII name, which is luck rather than safety.

232 tests green.

THE NAMED NON-PT LIST IS NOW EMPTY:
  * 0x2624 filter extension          DONE (commit 533210f, 139/139 identity reorders)
  * length-mismatch shape            DONE (resolved by the same change, 54 -> 0)
  * writing sample-locked markers    DONE (this commit)
What remains genuinely needs Pro Tools: the 26 unsettled typed spec sections (overwhelmingly
"what does this UI flag DO"), and the three staged experiments -- of which the regenerated
`CURRENT 2026-07-27 - DUPLICATED 01 130 G Full Stereo.ptx` now covers two at once.

## The headline coverage number was inflated — "documented" meant "mentioned anywhere"
Audited the claim that underpins everything this tracker says: **0 genuinely undocumented types.**
It rested on two filters, and BOTH were too generous.

  1. **`doc = {c for c in counts if ("0x%04x"%c) in spec}`** — a type counted as documented if its
     hex string appeared ANYWHERE in the spec, including a passing mention inside another type's
     prose. Measured honestly:

         observed content types                        482
         with their OWN `### ` spec entry              146
         "documented" by a passing MENTION only        176

  2. **`is_phantom`'s second signature** (`low byte 0x00` and never index-referenced) had no
     top-level clause. A phantom is a false `0x5A` INSIDE another block's payload; the top-level
     chain is walked contiguously by declared size, so nothing false can appear in it. Without
     that clause the signature was eligible to swallow real types.

`0x2000` is the proof that both mattered at once: **480 instances in 480 sessions — one per
session, always TOP-LEVEL, block_type 0x0001, span 13, always between 0x2033 and 0x2634** — and
it had NO entry, qualifying as "documented" on four incidental mentions while simultaneously
matching the phantom signature. A universal top-level block was invisible to both halves of the
tracker at once. Now documented (spec §0x2000) and the phantom test carries the top-level clause.

THE TRACKER NOW REPORTS THREE STANDARDS instead of one — mentioned / has an entry / settled —
with the 176 mention-only types listed as the work queue. This is the same failure the file's
own 2026-07-20 correction was about (a FALSE 100% from a too-generous measurement), which is
precisely why it was worth re-auditing rather than trusting.

    HONEST STATE: 146 of 482 types have a real entry; 176 need one; 160 are phantoms;
                  26 of 145 entries still say the PURPOSE is unproven.

## CORRECTION to my own correction, then real progress on the queue
I reported "146 have entries, 176 need one". **That overstated the gap by 23.** My entry
detector only counted headings that START with `0x`, so it missed real entries titled
`### Tempo (0x2028 map / 0x2718 lane)`, `### 0x261F / 0x2610 / ...` and similar. An over-strict
filter is the same error as the over-generous one it replaced, just pointing the other way --
and I made both within an hour. The detector now reads ANY heading level and any position.

    reported (wrong)  146 entries / 176 mention-only
    corrected          169 entries / 153 mention-only

WORKED THE QUEUE: profiled the universal (832/832) mention-only types and wrote entries for
nine, all from measurement over 180 sessions:

  * **0x2016 / 0x2017 / 0x2019 / 0x201A** -- a FAMILY, not four unrelated types: each is
    top-level, each holds EXACTLY ONE 0x2011 child, and they sit in a fixed contiguous run
    `0x207B -> 0x2016 -> 0x2017 -> 0x2019 -> 0x2018 -> [0x4300] -> 0x201A -> 0x201B` in 180/180.
    Sizes and block_type vary together, so they are variable-length records, not one shape.
  * **0x1022** -- large top-level LEAF (3.5-7KB), always immediately after the I/O routing table
    (`0x2603 -> 0x1022 -> 0x2033`).
  * **0x2027 / 0x202D** -- universal 13-byte stubs with a 4-byte zero payload (the `0x2000`
    shape) that ALSO occur far larger in a minority of sessions. That split is the useful fact:
    they are optional containers, and their empty case is the stub -- which is what `0x2000`
    cannot yet be shown to be, never having been seen non-empty.
  * **0x2023 / 0x202B** -- 0x2023 has only 5 distinct payload openings across 180 sessions
    (configuration), 0x202B has 108 (per-session data) and carries a length-prefixed inner
    `0x5A` record the block walk does not surface as a child.

    entries 169 -> 178, mention-only 153 -> 144.

Every entry states its confidence honestly: framing/position/universality are HIGH (180/180),
PURPOSE is unproven for all nine, and each says what Pro Tools experiment would settle it.
Documenting STRUCTURE is corpus work; documenting MEANING is not.

## Queue, batch 2: six more entries and TWO structural findings
    entries 178 -> 184, mention-only 144 -> 138

  * **0x2071 / 0x2072 are an identical adjacent PAIR** -- both exactly 181 bytes in every
    session, both block_type 1, both holding one 0x2070 child, identical payload openings,
    always in the order `0x2066 -> 0x2071 -> 0x2072 -> 0x202D`. 0x2072 has exactly ONE distinct
    payload across 180 sessions. Two instances of one structure, not two types -- but WHICH is
    which is corpus-unsettleable precisely because 0x2072 never varies.
  * **0x2595** -- universal top-level LEAF, span 10, payload a single byte `0x01` in all 180.
    A flag nobody has ever changed, so its meaning is unrecoverable here (same limit as 0x2511).
  * **0x2050 / 0x2073 / 0x202E** -- top-level leaves whose payload is usually empty or constant
    and occasionally large. 0x2073 is span 21 in 156 sessions and **187,371 bytes in 17**.

TWO FINDINGS THAT ARE WORTH MORE THAN THE SIX ENTRIES:

  1. **The `0x2011`-child rule is a CLASS, not a run.** I documented 0x2016/0x2017/0x2019/0x201A
     as a family; 0x206C, 0x2505 and 0x259C obey the same rule (top-level, exactly one 0x2011
     child, 180/180) from elsewhere in the sequence. **Seven types share one payload schema**, so
     `0x2011` is the shared record and the PARENT's content-type is the discriminator. Spec
     updated to say so rather than leaving four types looking special.
  2. **The top-level sequence is a fixed skeleton of SLOTS, most empty in most sessions.**
     0x2000, 0x2027, 0x202D, 0x202E and 0x2073 are all universal, all top-level, all usually a
     13-or-21-byte stub with a zero payload, and all occasionally large. That reframes "what is
     this 13-byte block" from a puzzle per type into one observation about the format: PT writes
     the slot whether or not it has anything to put in it.

Both findings came from PROFILING A BATCH rather than one type at a time -- the pattern is only
visible across types, and would have been missed by documenting each in isolation.

## Queue, batch 3: eight entries; the slot pattern now has NINE members
    entries 184 -> 192, mention-only 138 -> 130

  * **0x242E / 0x2430 are TWIN 22-byte wrappers**, adjacent, byte-constant across all 180
    sessions, each wrapping one empty catalog stub: 0x242E -> 0x1058 (MIDI regions),
    0x2430 -> 0x1055 (audio regions). This CONFIRMS and locates the "always-empty 0x2430-wrapped
    stub" that the 0x1055/0x1057 entries had noted in passing -- the two wrappers are a matched
    pair, one per catalog kind, and the POPULATED catalogs are the separate top-level blocks.
    Fixture, not data: copy verbatim.
  * **0x230A** -- 1,154 bytes in every session with exactly ONE distinct payload across 180. A
    factory constant table, carrying an inner length-prefixed 0x230B the block walk does not
    surface. Content undecoded, and byte-constancy means the corpus can never decode it.
  * **0x2424 / 0x2426 / 0x2634 / 0x262C** -- four more universal top-level stub slots. With
    0x2000, 0x2027, 0x202D, 0x202E and 0x2073 that makes **NINE**. 0x262C is the extreme: span 17
    in 174 sessions and **74,966 bytes in 2**.
  * **0x2510** -- top-level leaf, 4 distinct payloads over 180 sessions (configuration).
  * **0x2507** joins the 0x2011-child class, now EIGHT types.

THE SLOT GENERALISATION IS NOW WORTH STATING AS A FORMAT RULE: Pro Tools emits each top-level
slot unconditionally and fills it only when the corresponding feature is used. **An empty stub
means "feature absent", not "block missing"** -- which is directly actionable for synthesis
(emit the stub) and for editing (do not treat an empty slot as something to remove).

Batch profiling keeps paying: this batch's two best results -- the wrapper PAIR and the ninth
slot member -- are both cross-type facts invisible when documenting one type at a time.

## Queue, batch 4: nine entries, one field PROVEN, and the slot band
    entries 192 -> 198, mention-only 130 -> 124

  * **0x1028 = the session SAMPLE RATE.** `u32` LE at payload+2 (block+11). Verified against the
    independent `session_sample_rate()` reader on 120 sessions: **120 agree, 0 disagree**; values
    44100 (82) and 48000 (38). The block is variable-length (39/125/141, block_type 9 or 10), so
    the rate sits at a fixed offset inside a variable record -- do not assume the rest.
    This is the first entry in these batches with a PROVEN field rather than framing alone.
  * **0x2067 = the session identity record** (name + `2a 00 00 00`-tagged GUID), sitting between
    the 0x0003 header and the 0x2519 name table. It is what `session_self_name()` reads, i.e. the
    basis of the provenance filter that tells PT-authored files from our own output.
  * **The 0x26xx stub band** -- 0x2637 / 0x262C / 0x2632 / 0x262A / 0x2630 / 0x262E are SIX more
    universal slots and they are CONTIGUOUS, in two runs. With the nine already recorded that is
    **fifteen universal top-level stub slots**. `0x2632` is the pure case: span 13 with a single
    payload in all 180 sessions, never populated in any file seen. `0x262A` reaches 66,161 bytes
    and `0x262C` 74,966 when they are populated.

THE ADJACENCY IS THE NEW PART. The slots are not scattered: the `0x262x` ones form a contiguous
band, consistent with one feature area reserving a run of slots. That turns "why is there a
13-byte block here" into "this is the clip/fade area's slot run, unused in this session".

NOTE ON WHAT THESE BATCHES CAN AND CANNOT DO: four batches in, the entries are overwhelmingly
framing + position + universality + the empty/populated split, with PURPOSE unproven. That is
not a shortfall of effort -- it is what the corpus contains. 0x1028 is the exception that proves
it: its field is provable because an INDEPENDENT reader already decoded it and the values are
externally meaningful (44100/48000). Where no such anchor exists, structure is the ceiling.

## Queue, batch 5: the high-volume types, and a SECOND proven field
    entries 198 -> 206, mention-only 124 -> 116

Took the highest-instance-count types this time, on the reasoning that a type appearing 100,000
times deserves an entry more than one appearing twice.

  * **0x2434 = per-track display HEIGHT.** Span 18 in all 9,677 instances, and only TWO distinct
    payloads in the entire survey: `...3c 00` (u16 = **60**) x6,321 and `...40 00` (**64**) x180.
    60 is PT's default track height and 64 its next step; the 180 instances of 64 are exactly one
    per session (the 0x2023-parented copy), i.e. a session default rather than a per-track value.
    **This is also the block that forced `_ORD2015_ANCHOR` from 18 bytes to 16** -- the trailing
    `3C 00` that looked like framing is this height. Second proven field in these batches.
  * **0x2625 / 0x2626 = pure scaffolding, confirmed at scale.** 0x2625: span 18 in ALL 91,583
    instances, parent always 0x2627, child always 0x2626, ONE distinct payload. 0x2626: span 9 in
    ALL 100,001, payload EMPTY, one form. **191,584 instances, not one deviation.** The 8,418
    excess of 0x2626 over 0x2625 is the 0x2615-parented population -- the terminator is shared by
    a second family.
  * **0x2602 / 0x2601 pair 1:1** (20,494 each). 0x2602's payload opens with a length-prefixed
    PATH NAME (823 distinct); 0x2601 is a 13-byte stub in 18,858 of 20,494. So the I/O table
    lists every possible path and populates only the connected ones -- the stub majority IS the
    finding.
  * **0x1054 EXISTS TWICE PER SESSION** (top-level, and nested under 0x2428). Recorded explicitly
    because a count-fixer that takes "the first 0x1054" will sometimes take the wrong one.

WHY THIS BATCH WAS BETTER: I ordered by INSTANCE COUNT rather than by session coverage. A type
with 100,001 instances gives byte-constancy evidence no 180-instance type can, and the two most
useful results here (0x2434's field, the 191k-instance scaffolding proof) both depended on that
volume. Worth doing the remaining queue in that order.

## Queue, batch 6: eleven entries, a THIRD proven field, and 0x2506 finally has one
    entries 206 -> 216, mention-only 116 -> 106

  * **0x2506 has an entry at last.** 1.8M instances corpus-wide and it was the single biggest
    thing I settled today, yet it had no section -- the settled count rule, the two payload forms,
    the per-playlist bit and the refuted mono/stereo reading are now all in one place instead of
    scattered across a §5c note and a TODO.
  * **0x1001 carries a PER-FILE sample rate**, `u32` @payload+0. Values 48000/44100, equal to the
    SESSION rate in **9,182 of 9,183 instances**. Third proven field in these batches -- and note
    the ONE exception is the interesting datum: a file whose rate differs from its session's.
  * **0x1033 / 0x2106 are the constant companions of every 0x1003.** One each per audio-file
    record. 0x1033: span 9, EMPTY payload, one form in all 7,496. 0x2106: ONE distinct payload
    (all zeros) but VARIABLE span 144-254 -- **the size carries information even though the
    content does not**, which is worth flagging as a shape to expect elsewhere.
  * **The per-track routing sub-tree measured exactly:** `0x261B ⊃ 0x260D ⊃ {0x1029 (1:1, always),
    0x260C (2:1), 0x260E (split with 0x2613)}`.
  * **0x2038** -- span 19 in all 28,421, child always 0x2037, 3 distinct payloads.
  * **0x2580** -- the type that dominated the accidental length-prefixed name matches in the
    rename investigation; now documented as the dense-binary automation record it is.

PATTERN ACROSS BATCHES 5 AND 6: the proven fields (0x2434 height, 0x1028 session rate, 0x1001
file rate) all share one property -- **an externally meaningful value** (60/64 px, 44100/48000 Hz).
Where a field's values are recognisable from outside the format, the corpus can settle it; where
they are opaque integers, it cannot. That is a better predictor of "can I decode this" than
instance count or universality, and it is worth applying to choose future targets.

## Queue, batch 7: eight entries, and a whole CLASS named
    entries 216 -> 220, mention-only 106 -> 103   (documented 322 -> 323, phantoms 160 -> 159)

  * **THE EMPTY-TERMINATOR CLASS.** Four types -- 0x2037, 0x2626, 0x4301, 0x4420 -- share one
    shape: a leaf whose payload is empty or all-zero and IDENTICAL in every instance.
    **~164,000 instances between them carrying zero information.** Each has exactly TWO parent
    types, which is the useful part: a terminator is shared by two families, so "which
    terminator" does NOT identify the parent. Emit verbatim; a difference here would mean a
    parsing error, not a session difference.
  * **0x203B is 0x2038's near-twin** -- both fixed-size wrappers around a single 0x2037, both
    parented by view-chain records (0x2038 span 19 under 0x200A/0x2015; 0x203B span 22 under
    0x2580/0x2015). **0x203B is also the size-22-under-0x2015 shape that 0x203F collides with**,
    which is exactly why this codebase's locator pins the `3b 20` content-type bytes rather than
    the size. The collision now has both sides documented.
  * **0x2013** holds EXACTLY ONE 0x2020 and ONE 0x2021 in every session -- a fixed pair, not a
    list.
  * **0x1052 / 0x251A** are the two highest-volume length-prefixed NAME carriers (lane name and
    name-table detail), now entered with their parentage proven 1:1 to 0x1054 and 0x2519.

NOTE the phantom count moved 160 -> 159 and documented 322 -> 323: writing these entries
reclassified a type that had been counted as a phantom. That is the tracker working as intended
-- the two sets are not independent, and documenting a type removes it from the noise pile.

## Queue, batch 8: parentage settled for ten core types — and a note on not gaming the metric
    entries 220 -> 223, mention-only 103 -> 100

Measured CONTAINMENT for the ten types the track editors depend on. Every ratio below is the
full instance count of the child, so these are settled, not sampled:

    0x1003 -> 0x1004   7,496/7,497      0x1014 -> 0x1015   3,115/3,115
    0x102D -> 0x261B   4,047/4,047      0x2589 -> 0x258A   1,355/1,355
    0x2077 -> 0x2030   1,701/1,701      0x210B -> 0x2107   3,991/3,991
    0x261B -> 0x261C (2,915) / 0x261E (978)                0x258A -> 0x258B  109/109

THREE FACTS THAT CHANGE HOW CODE SHOULD BE WRITTEN:
  * **0x261B appears ONLY under track-record kinds** (0x261C/0x261E here, 0x261D in the wider
    survey) -- never MIDI/instrument/VCA. That is why `n(0x261B) == n(0x261C)+n(0x261E)+n(0x261D)`
    holds, and why a MIDI duplicate exercises ONLY the GUID identity path.
  * **0x2107 is NOT universal** -- 50 of 75 sessions here. A session without it has no playlist
    list at all, which is precisely the case the 0x2506 multiplier falls back to n(0x2015) for.
  * **0x258A's parent is 0x258B, NOT the top level.** Any "find the 0x258A container" step that
    assumes top-level will not find it.

ON THE METRIC, because I nearly mis-stepped: my first version of this section had a heading that
named no type, so the tracker did not count it -- correctly, since the detector reads headings.
I renamed the heading to name the ten. That IS accurate (the section documents them), but it
would be gaming if the section were thin, so the section now states its own SCOPE explicitly:
it settles CONTAINMENT and nothing else, and a table row is a narrower thing than a full entry.
Worth keeping in mind for the rest of the queue: the number is only honest while what it counts
stays honest.

## Queue, batch 9: clip/region/plugin chains settled, and TWO corrections to existing prose
    entries 223 -> 234, mention-only 100 -> 89   (biggest single batch so far)

Containment settled at full instance counts:

    0x1050 -> 0x1052, child ALWAYS 0x104F   30,282/30,282
    0x2629 -> 0x262A, child ALWAYS 0x2628   37,764/37,764
    0x261C -> 0x2624, child ALWAYS 0x261B    2,915/2,915
    0x200B -> 0x261C, child ALWAYS 0x200A    2,915/2,915
    0x2104 -> 0x2015, child ALWAYS 0x2103    2,683/2,683

TWO CORRECTIONS TO WHAT WAS ALREADY WRITTEN:
  1. **The plugin chain is one level deeper than the prose said.** Measured:
     `0x2627/0x2617 ⊃ 0x2616 ⊃ 0x2615 ⊃ {0x2613, 0x2626}`. Earlier notes had
     `0x2616 ⊃ 0x2615 ⊃ 0x2613 ⊃ 0x1038`, which is right about the order but wrong about the
     last link -- see (2).
  2. **0x2613's framed 0x1038 is NOT payload-aligned.** The payload opens `ff ff` and the
     `0x5A`-framed 0x1038 starts at payload+2, so a child-walk beginning at payload+0 reports
     0x2613 as a LEAF. That is exactly the kind of detail that makes a plugin-aware traversal
     silently return nothing, and it explains why the block walk never surfaced 0x1038 as a
     child despite the prose describing it as one.

ALSO: **0x2628 has a SECOND parent** -- 0x262B (57 instances), the crossfade path. A region
enumerator keyed only on 0x2629 misses those.

The batch was the largest yet (11 entries) because the clip/region/plugin area is densely
inter-referenced: once the parentage of one member is measured the rest fall out, whereas the
top-level stub slots each had to be profiled individually. Worth choosing DENSELY CONNECTED
areas next rather than isolated types.

## Queue, batch 10: three findings that directly affect traversal code
    entries 234 -> 241, mention-only 89 -> 82

  1. **The click/aux track record MIRRORS the audio one.** `0x261E ⊃ {0x261B, 0x200D}` is exactly
     `0x261C ⊃ {0x261B, 0x200B}` -- same shape, differing only in the view-chain type. Any
     per-track traversal should key on the PAIR, not on 0x261C alone.
  2. **The crossfade region path is a SECOND route to 0x2628:** `0x262C ⊃ 0x262B ⊃ 0x2628`,
     alongside `0x262A ⊃ 0x2629 ⊃ 0x2628`. Present in only 3 of 75 sessions, which is exactly why
     a region enumerator keyed on 0x2629 alone passes most corpora before failing.
  3. **The plugin cluster has THREE routes**, all under 0x2627:
         0x2627 ⊃ 0x2616                (3,200)   the common path
         0x2627 ⊃ 0x2617 ⊃ 0x2616       (483)     17 sessions
         0x2627 ⊃ 0x2618 ⊃ 0x2615       (18)      2 sessions -- SKIPS the 0x2616 level
     A traversal assuming `0x2627 -> 0x2616` directly misses 501 instances. **Key on the LEAF
     (0x2613), not on the route to it.**

Also: **0x1048 is a fifth empty-terminator** (span 13, one payload, parent always 0x1049), and
**0x2551 carries a length-prefixed default name** (`(untitled…`).

ALL THREE HEADLINE FINDINGS ARE OF THE SAME KIND: a structure reachable by more than one route,
where the rare route is the one that breaks code. The corpus is the only way to find these --
the common route works on every session you happen to test first. Worth deliberately looking for
SECOND PARENTS of every type already documented; two turned up in two batches (0x2628 via 0x262B,
0x2615 via 0x2618) without being sought.

## The rare-routes sweep — 60 second parents, found systematically instead of by accident
Two rare routes had turned up BY ACCIDENT in earlier batches (0x2628 via 0x262B, 0x2615 via
0x2618). Rather than keep stumbling on them, I swept EVERY type's parent distribution over 240
sessions and kept every parent holding <5% of that type's instances. **There are 60.**

    0x2628   FOUR parents: 0x2629 (main), 0x2633 (896), 0x262B (247), 0x262D (17)
    0x2037   SEVEN parents: 0x2038, 0x203B, + 0x203D/0x203C/0x2039/0x203F/0x203E
    0x2619   also under 0x2718/0x2719/0x271A/0x271B/0x271C -- 129 each
    0x260D / 0x102D / 0x2627   also under 0x2621 -- the MIDI track record
    0x260A under 0x2611 (233), 0x4420 under 0x2519 (128), 0x2011 under 0x4834/0x4841 (60 each)

THREE CONSEQUENCES:
  1. **0x2628 (the region record) is reachable FOUR ways.** My own entry two batches ago said
     "a second parent" -- corrected. Enumerate regions by CONTENT TYPE, never by walking a parent.
  2. **0x2037 is shared across the whole 0x203x family**, so a terminator identifies nothing about
     its parent -- the general property of the empty-terminator class, now demonstrated rather
     than assumed.
  3. **0x2621 (MIDI) carries 0x260D, 0x102D and 0x2627** -- the same per-track furniture audio
     has. MIDI tracks lack 0x261B SPECIFICALLY, not the rest of the subtree. That refines the
     "0x261B belongs to exactly three kinds" rule into something more useful for editors.

THE METHOD IS THE POINT AND IT GENERALISES: I found 2 of these by accident across two batches;
the sweep found 58 more in one run, INCLUDING two extra parents of a type I had just finished
documenting. **Any statement of the form "X always lives under Y" is only as good as the sample
it was measured on** -- the spec now says so explicitly, and every containment claim in it either
carries a full instance count or is qualified.

## I applied the rare-routes finding to the CODE — and it predicted a bug that isn't one
The sweep says "enumerate regions by content type, never by walking one parent", and
`region_list` walks `lists[0]` -- the first `0x262A`. That is the exact shape the finding warns
about, so I tested it: **`region_list` returns fewer records than there are `0x2628` blocks in
41 of 120 sessions.**

Before changing anything I classified the shortfall. In EVERY short session there is exactly ONE
`0x262A`, and the missing records are exactly the `0x2628`s reached via `0x2633`, `0x262B`
(crossfade) and `0x262D` -- 1 to 21 per session.

**So `region_list` is correct and I nearly "fixed" it.** Its purpose is the region LIST, which is
the index space `0x104F`'s `+11` field addresses; pulling crossfade-internal regions into it
would shift every index and break `replace_clip_region`. The narrower scope is the point.

WHAT WAS ACTUALLY MISSING was the SCOPE STATEMENT. The docstring said "every audio region in the
session", which is not what it returns. It now says exactly what it covers, quotes the 41/120
measurement, and points at `region_names()` for the all-routes enumeration (verified to see all
`0x2628` in 120/120 sessions).

THE LESSON IS ABOUT THE ORDER OF OPERATIONS: a general finding that predicts a defect is a
HYPOTHESIS, not a verdict. Measuring the shortfall took one script and turned "fix this function"
into "document its scope" -- and had I skipped it, I would have shifted the region index space
underneath a PT-confirmed editor. Two functions with the same shape can differ entirely in
whether the shape is a bug: `region_names` enumerates by type (right for "all regions"),
`region_list` walks a parent (right for "the index space").

232 tests green.

## Queue, batch 11: the rulers NAME THEMSELVES — the best decode of the whole exercise
    entries 241 -> 243, mention-only 82 -> 80

The five 0x271x containers each hold exactly two children: a `0x2619` carrying a length-prefixed
ASCII NAME, and one content container. The names are identical across all 50 sessions each:

    0x2718  "Tempo"          -> 0x2028 (tempo map)
    0x2719  "Meter"          -> 0x2029 (meter map)
    0x271A  "Markers"        -> 0x2030 (marker list)
    0x271B  "Key Signature"  -> 0x2433
    0x271C  "Chord Symbols"  -> 0x2437

**This is PROVEN, not inferred** -- Pro Tools writes its own ruler labels into the file, and they
are externally meaningful (they are the ruler names in its UI). **And it settles two previously
unidentified content types by association: 0x2433 is Key Signature data, 0x2437 is Chord Symbols
data** -- each the sole content child of a container that states its own name.

This is exactly the property flagged in batch 6 as the predictor of decodability: a field whose
VALUES are recognisable from outside the format. Every proven decode in this exercise has had it
(0x2434's 60/64 px, 0x1028's 44100/48000, and now five literal ruler names); nothing without it
has yielded a meaning.

ALSO: **the 0x203x family all wrap a single 0x2037** -- 0x2038, 0x203B, 0x203D, 0x2039, 0x203E,
0x203F, spans 19/22/21/19/63/22. So `0x203F`'s long-standing collision with `0x203B` (both size
22 under 0x2015) is not a coincidence but the family's defining property: **size does not
identify a member, only the content type does.** That retroactively justifies pinning the
`3b 20` content-type bytes in the view-volume locator rather than matching on size.

Presence note worth keeping: the rulers exist in 50 of 75 sessions, but the top-level
0x2028/0x2029/0x2030 maps exist regardless -- the ruler is a DISPLAY WRAPPER around data that
lives independently, which is why replace_tempo_map resizes the lane "only when present".

## The embedded-name sweep: FIFTEEN types identified in one run
    entries 243 -> 258, mention-only 80 -> 65   (the largest single gain of the whole exercise)

Applied the decodability rule systematically instead of type by type: scan every UNDOCUMENTED
type's payload for a length-prefixed printable-ASCII run. A name Pro Tools wrote is externally
meaningful by construction, so wherever one exists the type identifies ITSELF.

    0x0003  90/90   the PRODUCT string -- "Pro Tools HD" / "Studio" / "Ultimate"
    0x2587  65/65   "(untitled)" -- a default document name
    0x0F3C  34/34   VOLUME names -- "Macintosh HD", "ABTB ROAD 1", "Arts T1 SSD"
    0x0F3D  34/61   the same volume names
    0x2701  30/30   a codec/format tag -- "MPG3"
    0x1057  34/34   MIDI track names        0x2620  20/20  MIDI track names
    0x2621  14/14   INSTRUMENT track names  0x2423   7/7   track GROUP names
    0x2603  84/90   I/O OUTPUT path names   0x1004  62/90  the audio-file table
    0x2107  43/60   track names             0x2633  69/81  instrument/preset names
    0x200A  2382/4537  per-CHANNEL labels ("C CH L", "C CH R")
    0x203E  3/3     a PLUGIN name -- "ValhallaVintageVerb"

FIVE THINGS THIS SETTLES that individual profiling had not:
  * **0x0003 is the product/version header** -- the block core.parse_version reads. The string is
    the EDITION name, which is why the version field's meaning varies by product.
  * **0x0F3C/0x0F3D are VOLUME records.** Nothing else in the format names a disk, and it explains
    why they are among the types differing most between machines.
  * **0x2620 vs 0x2621 is MIDI vs INSTRUMENT tracks** -- a distinction the earlier "MIDI variant"
    note could not make.
  * **0x2423 = track GROUP names**, confirming it against an earlier guess that it was a fade type.
  * **0x200A carries per-CHANNEL labels**, which is why it is the view chain's per-channel record
    rather than a per-track one.

METHOD: **one sweep decoded more types than the preceding five batches of individual profiling
combined.** The rule it applies is the one established in batch 6 -- look for values meaningful
OUTSIDE the format -- and a length-prefixed name is its purest case, because the application has
already written down what the thing is. Where a type has no such name, five batches of evidence
say the corpus will not yield its meaning either.

## A MEASURED BOUNDARY on what the corpus can still decode (two negative sweeps)
Both sweeps are negative results, and together they make the remaining work list honest rather
than open-ended.

**1. No remaining undocumented type carries a DERIVED COUNT.** Every u32 in the first 48 payload
bytes of every entry-less type, tested against nine measured session quantities over 140
sessions, requiring the match to hold in >=95% of sessions AND the quantity to take >=5 distinct
values. **ZERO matches.**

    THE NAIVE VERSION REPORTS 12 CONFIDENT HITS AND ALL TWELVE ARE FALSE. Every one is a field
    holding `1` matching `n_tempo` -- which is 1 in 77 of 90 sessions. **A quantity that does not
    vary cannot be correlated against, and will appear to confirm anything.** I have now hit this
    trap in three different forms today (the 0x2506 multiplier's coinciding candidates, the
    0x2511/0x2595 never-changed flags, and this), so it is worth stating as a rule rather than an
    anecdote.

**2. Where a type has no embedded NAME, no meaning has ever been recovered from this corpus.**
Fifteen types were identified purely from a length-prefixed string PT wrote. Every other semantic
decode in the spec -- 0x2434's height, 0x1028/0x1001's sample rates, the five ruler names --
also rested on externally recognisable values. **Nothing has been decoded from opaque integers
alone across five batches of profiling.**

WHAT THIS BOUNDS: for the remaining entry-less types the corpus can still give framing,
parentage, universality, size distributions and the empty/populated split -- and those have been
done. It cannot give PURPOSE. That needs Pro Tools, and the experiment is always the same shape:
one session, saved twice, one setting changed.

This is the most useful thing to know before spending more time here: the remaining queue is not
"harder corpus work", it is a DIFFERENT KIND of work that the corpus cannot do.

## Queue, batch 12: ten more entries, and a scope overstatement found by applying one
    entries 258 -> 268, mention-only 65 -> 55

Ten types framed and parented at full instance counts. Three worth pulling out:
  * **0x1056 is to MIDI what 0x1050 is to audio** -- both placement wrappers holding a single
    0x104F, differing only in parent (0x1057 vs 0x1052).
  * **0x4833 lives inside the Markers ruler** (0x271A) and **0x4403 inside the region wrapper**
    (0x2629) -- both byte-constant. Their VALUE is nothing; their POSITION is the information.
  * **0x2721 is the SIXTEENTH universal top-level stub slot** -- span 13, one payload, all 50
    sessions, never populated (like 0x2632).

APPLYING 0x1056 TO THE CODE FOUND A SECOND SCOPE OVERSTATEMENT. `clips()` claimed "Every placed
clip in the session" but reaches placements through `clip_lanes`, which enumerates 0x1052 AUDIO
lanes. Measured on a session with 185 MIDI placements: **clips() returns 511 of the 696 0x104F
placements present, and the 185 missing are exactly the MIDI ones.**

`clip_lanes` itself is CORRECTLY scoped and says so ("every audio-playlist lane (0x1052)"). It
is only the aggregate reader that overstated. Docstring now states the limit with the measured
numbers, exactly as `region_list`'s did -- and for the same reason: the edit functions it feeds
take an audio lane zmark, so the narrow scope is right and only the claim was wrong.

TWO scope overstatements found in two attempts to apply a structural finding to the code. Both
were docstrings promising more than the function delivers, and neither was a defect. Worth
checking the rest of the read layer's docstrings against what it actually enumerates -- that is
a cheap, corpus-verifiable audit and it has a 2-for-2 hit rate so far.

POSSIBLE FUTURE FEATURE (not a defect): a MIDI-aware clip enumerator via 0x1057 -> 0x1056 ->
0x104F. The structure is now documented; nothing else blocks it.

## The docstring audit: 2-for-2 did NOT become 4-for-4 — the rest are accurate
Eleven public readers make a totality claim ("every", "all", "each") in their first docstring
line. Four have a clean one-to-one content type to check against, so I checked those:

    playlist_names   45/45 sessions   count == n(0x210B)   ACCURATE
    region_names    120/120 sessions  count == n(0x2628)   ACCURATE
    clip_positions  119/119 sessions  count == n(0x104F)   ACCURATE
    markers          69 match, 38 SHORT -- but short BY DESIGN and already documented:
                     it de-duplicates by (name, position) because PT writes a duplicate
                     0x2030 memory-locations list. 138 blocks -> 69 markers is exactly 2:1.

So the two overstatements I found (region_list, clips) were the only two. **I expected a third
and there wasn't one**, which is worth recording as clearly as the hits: a 2-for-2 streak is not
evidence of a systemic problem, and saying so stops a real finding from inflating into a
narrative about sloppy documentation.

SCOPE OF THIS AUDIT, stated because it is partial: 4 of the 11 claim-making readers were
checkable this way. The other 7 (clip_names, track_playlists, track_name_occurrences,
validate_pt_invariants, clip_lanes, region_list, clips) either take extra arguments, aggregate
several types, or are the two already corrected. A one-to-one content type is what makes the
check possible; where there is not one, the claim cannot be verified this cheaply.

## Queue, batch 13: the 0x2011 class closed at eight, and a real/phantom split inside ONE type
    entries 268 -> 278, mention-only 55 -> 45

  * **The 0x2011-child class is now complete at EIGHT members** -- 0x2016, 0x2017, 0x2019, 0x201A,
    0x206C, 0x2505, 0x2507, 0x259C. All top-level, all one-per-session, all holding exactly one
    0x2011. The record is 0x2011; the parent's content type is the only discriminator.
  * **The CROSSFADE pair hangs off the REGION.** 0x2523 -> parent ALWAYS 0x2628, child ALWAYS
    0x2526, both byte-constant. So a crossfade both REACHES a region by its own route
    (0x262C ⊃ 0x262B ⊃ 0x2628) and HANGS a 0x2523 pair off it. Present in 3 of 75 sessions.
  * **0x0F3E / 0x270A / 0x4530 are byte-constant leaves whose PARENT is the whole finding** --
    0x4530 under the SMPTE record (0x207B), 0x0F3E under the counter/nudge setting (0x2024). Both
    plausibly the "options" half of those settings, but a payload that never varies cannot show it.

**0x4300 IS ONE CONTENT TYPE COVERING TWO ENTIRELY DIFFERENT THINGS:**

    top-level form      span     11   block_type 1   50 sessions   -- the real block
    0x1038-parented    span 59,656   block_type 0    4 instances   -- almost certainly a PHANTOM

Different by three orders of magnitude in size, different parent, different block_type. This is
the clearest example in the corpus of why the phantom filter exists, and it has a practical
consequence: **a locator for the real 0x4300 must require TOP-LEVEL, not just the content type.**
Worth remembering that "filter by content type" -- the advice from the rare-routes finding -- is
necessary but NOT sufficient; type plus position is what identifies a block.

## Queue, batch 14: a PRODUCT-GATED block family — nine types that identify the writer
    entries 278 -> 287, mention-only 45 -> 36

0x4808, 0x4820, 0x4824, 0x4826, 0x4828, 0x4829, 0x4834, 0x4501 and 0x2567 appear in **exactly
the same 30 sessions**, and the 0x0003 product string of every one of those is **"Pro Tools
Studio"** (30/30). No session of another product carries any of them. Every one has exactly ONE
distinct payload -- the whole family is fixture, and what identifies it is not its content but
WHICH PRODUCT WROTE THE FILE.

THREE CONSEQUENCES:
  1. **This is a version/product gate, not a feature gate.** The blocks appear regardless of what
     the session contains. 0x0003's product string and this family agree perfectly, so either can
     identify the writer.
  2. **0x4828 and 0x4829 attach to the COUNT CONTAINERS** (0x1054 lanes, 0x1015 track entries).
     Anything rebuilding those on a Studio-written session must preserve them; anything
     synthesizing for Studio must emit them.
  3. **A synthesizer targeting a different product must NOT emit them** -- absent from every
     non-Studio session measured, so including them would be a tell that the file was not written
     by the product it claims.

ALSO A CORRECTION: I said last batch that the 0x2011-child class "closes at eight". **0x4834
makes it NINE.** Corrected in the spec. The claim was one batch old and already wrong -- worth
noting because "the class is complete" is exactly the kind of statement that should carry the
sample it was measured on, and mine did not.

METHOD NOTE: this family was found by noticing that ten unrelated-looking types all appeared in
"25 sessions" in the profiler output. **An identical, oddly specific session count across several
types is a signal worth chasing** -- it means they share a gate, and finding the gate explains all
of them at once.

## Queue, batch 15: ten more, and the "class is complete" claim stated as a FLOOR
    entries 287 -> 297, mention-only 36 -> 26

  * **0x1600 is a SIXTH empty-terminator** (span 8, empty payload, one form), parented by the
    plugin catalog 0x1017. Joins 0x2037/0x2626/0x4301/0x4420/0x1048.
  * **0x208E confirms the 0x206F decode from the other end** -- it is the inner block of the
    0x208C MIDI device record, so `0x206F ⊃ 0x208C (device name) ⊃ 0x208E` is now attested from
    both directions.
  * **0x2079 is the only type in this batch carrying real data** -- 17 distinct payloads in 17
    instances, parent 0x2020, whose own a/b/c/d fields matched no known session quantity. Both
    halves of that structure vary per session and neither is decodable from the corpus.
  * **0x483B/0x483D/0x4841 belong to the Pro Tools Studio family.** 0x483D attaches to the 0x2519
    NAME TABLE, which matters for anything rewriting it on a Studio-written session.

**I HAVE NOW BEEN WRONG TWICE ABOUT THE 0x2011 CLASS BEING COMPLETE** -- "eight" became nine
(0x4834) became ten (0x4841), each time because the sample widened. The spec now states it as
**AT LEAST TEN, explicitly a floor rather than a closure**, with the revision history in the
sentence. That is the honest form for any "the set is complete" claim measured on a sample, and
it is the third time today a completeness claim of mine has needed revising (the others: the
0x2628 parents, "the remaining latin1 decodes are not name readers").

USEFUL NEGATIVE FROM THE COHORT VIEW: grouping the remaining types by SESSION COUNT shows ~60 of
them share a single 31-session cohort with wildly varied hex values (0x7AA9, 0xDAFA, 0xE09D...).
That is the phantom signature -- one cluster of sessions whose plugin blobs frame noise -- not 60
real types awaiting documentation. The genuinely remaining work is much smaller than the raw
count suggests.

## Queue, batch 16: a THIRD placement chain — video
    entries 297 -> 305, mention-only 26 -> 18

**0x1053 is a VIDEO record.** Its payload opens with a length-prefixed name, and in all 9
instances that name is `Video Jun 12, 09 03 16_DNxHD36` -- **DNxHD is a video codec**, so the
identification is as solid as the ruler names were, and for the same reason: PT wrote the string.

**0x1051's parent is ALWAYS 0x1053 and its child ALWAYS 0x104F**, which makes a third placement
chain, structurally identical to the two already known:

    audio   0x1052 ⊃ 0x1050 ⊃ 0x104F
    MIDI    0x1057 ⊃ 0x1056 ⊃ 0x104F
    video   0x1053 ⊃ 0x1051 ⊃ 0x104F

Each is `lane/record ⊃ wrapper ⊃ placement`, differing only in the two OUTER content types; the
0x104F placement itself -- position, region ref, timebase -- is shared by all three.

CONSEQUENCE: `clip_lanes` enumerates 0x1052 only, so `clips()` sees neither MIDI nor video
placements. The limit was already documented on `clips()` after the 0x1056 finding; this supplies
the third chain a complete enumerator would need. Video appears in 9 of 240 sessions, which is
why the chain went unnoticed while the audio one was mapped in detail.

ALSO: **0x2105 is a SECOND 0x2103 holder** alongside 0x2104, and **0x2556/0x255C are two more
`0x2551 ⊃ … ⊃ 0x2552` routes** alongside 0x258B. Both are the same recurring shape -- a family of
wrappers around one shared inner record -- which now has four instances in this format (0x2037,
0x2103, 0x2552, 0x2011). **When a type appears under several wrappers, expect MORE wrappers than
you have found.**

## Queue, batch 17: the corpus-answerable queue is effectively CLEARED
    entries 305 -> 309, mention-only 18 -> 14

Of the 14 still without an entry, **13 match a documented PHANTOM signature** -- 61 more sit in
the single 31-session cohort whose plugin blobs frame noise. What genuinely remains is:

    0x2757   1 instance  / 1 session
    0x2557   1 instance  / 1 session

Two types, one instance each. **At one instance nothing can be claimed beyond "it exists", so
this is the floor of what the corpus can give.**

TWO RESULTS FROM THE TARGETED HUNT (880 sessions, aimed at the types too rare for a 75-session
sample):
  * **0x203C is a SEVENTH member of the 0x203x family** -- span 23 in all 164 instances, child
    always 0x2037, one payload. The family has now grown across three widenings, so the spec
    states seven as a FLOOR.
  * **0x2631's parent is ALWAYS 0x2632** -- and that CORRECTS my own entry from three batches ago,
    which called 0x2632 "span 13 with a single payload in every session, never populated in any
    file seen". True of the 180 sessions it was measured on; false in general. 0x2632 holds a
    0x2631 in 8 of 880 sessions.

**THE CORRECTION IS THE MOST INSTRUCTIVE PART.** 0x2632 was cited as the PURE example of the
"universal stub slot" pattern -- the one slot never seen populated. It turns out to have the
same shape as all the others. **A pattern's cleanest example is the most likely thing to be a
sampling artifact**, precisely because it is the case where no counterexample has been drawn yet.
That is the fourth completeness claim of mine revised by a wider sample today; all four
corrections are left visible in the spec rather than edited away.

## The "unproven" count WENT UP — and that is the documentation working
Re-measured after the queue work: **unsettled sections 26 -> 53.** Nothing was un-learned. ~160
new entries were written and **each states what it does NOT establish**; a type with no entry
contributes 0 to that count, a type with an honest entry contributes 1. **The number rising is
the honest-accounting mechanism doing its job**, and it is worth saying plainly because the
obvious reading is the opposite.

CLASSIFIED the 221 types those 53 sections cover, over 120 sessions:

    BYTE-CONSTANT (>=10 instances, ONE payload)   50   the corpus can NEVER decode these
    VARYING       (>=10 instances, >1 payload)   152   only with an EXTERNAL ANCHOR
    TOO RARE      (<10 instances)                 19   not on this evidence

**The 50 byte-constant types are a HARD FLOOR.** No amount of further corpus work, tooling or
sampling can decode a field that has never taken a second value -- the information is not in the
files. They are as documented as the corpus can make them (framing, span, parent, child,
universality) and their purpose is permanently out of reach here.

For the 152 varying types the two anchors that have ever worked are catalogued: a length-prefixed
name PT wrote (15 types + the 5 rulers), and a value meaningful outside the format (0x2434's
60/64 px, 0x1028/0x1001's 44100/48000). The derived-count sweep found ZERO further matches.

SO THE REMAINING DOCUMENTATION WORK IS NOW FULLY CHARACTERISED: 50 types permanently opaque,
152 needing an external anchor (in practice, Pro Tools), 19 too rare to judge, 2 with a single
instance, and 13+61 phantoms. There is no longer a category of "corpus work I have not done".

## SHIPPED: placement_lanes() — the reader now sees all THREE placement chains
The corpus documentation work is exhausted, but today's findings left a buildable IMPLEMENTATION
gap that needs no Pro Tools, so I built it.

    audio   0x1052 -> 0x1050 -> 0x104F     (what clip_lanes covers)
    MIDI    0x1057 -> 0x1056 -> 0x104F     (invisible before today)
    video   0x1053 -> 0x1051 -> 0x104F     (invisible before today)

`placement_lanes(data, kinds=(...))` returns `(zmark, name, n_placements, kind)` for all three.

MEASURED: accounts for **every 0x1050+0x1056+0x1051 wrapper in 120/120 sessions**, and recovers
**1,628 MIDI and 8 video placements** across the 38 sessions that have any. Before this, those
were simply not visible to the read layer -- `clips()` returned 511 of 696 placements on one
session and there was no function that could see the rest.

DELIBERATELY ADDITIVE: `clip_lanes` is untouched, so nothing that edits by lane zmark changes
behaviour. The `kind` field is returned precisely so a caller can avoid handing a MIDI holder to
`remove_clip`, which takes an AUDIO lane zmark. Making the existing function polymorphic would
have been the tempting change and the wrong one -- it feeds PT-confirmed editors.

233 tests green. The test pins the API and the audio-path equivalence on a local control file;
the MIDI/video coverage rests on the corpus measurement above, since the controls are audio-only.

THIS CLOSES THE LAST BUILDABLE GAP I FOUND TODAY. Remaining implementation work (editing MIDI or
video clips, not just reading them) would need PT confirmation, since it writes.

## SHIPPED: today's measured structure turned into VALIDATOR CHECKS
The documentation side is exhausted, but ~20 relations measured today were sitting in the spec
doing nothing. Two are now enforced in `validate_pt_invariants`:

  * **mandated-child** -- ten wrapper types each carry a specific child in 100% of untouched
    instances: the three placement chains (0x1050/0x1056/0x1051 -> 0x104F), 0x2629->0x2628,
    0x2625->0x2626, 0x2038/0x203B->0x2037, 0x261C->0x261B, 0x200B->0x200A, 0x2104->0x2103.
  * **terminator-span** -- the six empty-terminator types are byte-constant:
    0x2037=9, 0x2626=9, 0x4301=41, 0x4420=11, 0x1048=13, 0x1600=8.

CALIBRATION: **792,373 checks across 240 untouched corpus sessions, ZERO violations**, so the
rules are real and the checks cannot false-positive on PT-authored files. Verified silent on 240
sessions, and verified to BITE on both corruption shapes:
    widen one 0x2037 by 1 byte   -> "0x2037 at 36495 spans 10, not 9"
    blank a 0x1050's 0x104F child -> "0x1050 at 22034 has no 0x104f child"

WHY THESE TWO: they catch a splice that drops a child or resizes a terminator -- neither of which
the existing count-container or name-table checks can see. That is precisely the failure mode of
the 0x2107 bounds bug fixed this morning, which corrupted a neighbouring block's header and was
invisible to every check then in place.

NOTE SEVERITY, DELIBERATELY. 100% on PT-authored files says the rule is REAL; it does not say Pro
Tools REJECTS a file that breaks it, and "error" in this validator means a confirmed rejection.
Promote only if PT is observed refusing one. Edit audit unchanged (dup 72 clean, rm 105 clean),
233 tests green.

## Two more invariants shipped — and calibration caught a FIFTH overstated claim
Calibrated three more candidates on 240 untouched sessions. Two hold universally and are now
enforced; the third turned out to be my own error.

  * **ruler-shape** -- each 0x271x holds exactly one 0x2619 name and one content block.
    645 rulers measured, ZERO exceptions. Bites: blanking the Tempo ruler's 0x2619 fires it.
  * **one-2011-child** -- the ten known 0x2011 holders each hold exactly one. 2,040 measured,
    ZERO exceptions. Membership stated as a FLOOR, since it has been revised upward twice.

  * **studio-family all-or-nothing -- REJECTED, and it corrects batch 14.** I wrote that the nine
    Pro Tools Studio types "appear in exactly the same 30 sessions". At 240 sessions **one carries
    only 6 of the 9.** The family is STRONGLY co-present, not strictly all-or-nothing, so a
    validator must NOT treat a partial set as corruption -- which is exactly what I would have
    shipped had I not calibrated first.

**THAT IS THE FIFTH COMPLETENESS CLAIM OF MINE REVISED BY A WIDER SAMPLE TODAY** (the others:
0x2628's parents, the 0x2011 class twice, the latin1 name readers, 0x2632 "never populated").
All five corrections are left visible in the spec. The pattern is consistent enough to state as
a working rule: **any claim of the form "always / exactly / never / the same set" that I measure
on one sample will be wrong about 20% of the time at four times the sample size.**

CALIBRATION IS WHAT CAUGHT IT. Adding a check without first running it against untouched corpus
files would have shipped a false positive into a validator whose whole value is that it does not
cry wolf. **Calibrate, then add** -- and a candidate that FAILS calibration is not a wasted
experiment, it is a documentation correction.

233 tests green, silent on all 240 untouched sessions.

## SHIPPED: three readers turning today's decodes into API
Today decoded several things that never became callable. Now they are:

  * **session_product(data)** -- the Pro Tools EDITION that wrote the session, from the 0x0003
    header string. **Resolves on 240/240 sessions**: Pro Tools HD 106, Studio 61, Ultimate 56,
    plain "Pro Tools" 17. This is the field to branch on when synthesizing, because nine block
    types appear ONLY in Studio sessions.
  * **volumes(data)** -- every disk/VOLUME name the session references, from 0x0F3C.
    `ABTB ROAD 1`, `Macintosh HD`, `Arts T1 SSD`, ... Nothing else in the format names a volume.
  * **midi_devices(data)** -- `(name, kind)` for the session's MIDI devices.

THE MIDI READER NEEDED A CORRECTION BEFORE IT WAS RIGHT, and the correction is the interesting
part. My first version read every 0x208C and called them all "input devices", on the strength of
this morning's 0x206F decode. Checking parentage first showed **0x208C is used by TWO different
lists**:

    parent 0x206F   kind "input"   11 measured    Alesis Vi61, OXYGEN 8      -- physical controllers
    parent 0x2086   kind "port"   407 measured    Stylus RMX 1, Blue Cat's PatchWork Synth 1,
                                                  Network, M1 Mac            -- destinations

Conflating them mixes hardware you PLAY with destinations you SEND TO. The reader now returns the
kind, and the docstring records both parents (0x2086 itself hangs off 0x2050 or 0x2006).

**That is the same mistake shape as the morning's `0x206F` "invariant sentinel"**: a record type
that looks like one thing because the sample was dominated by one of its uses. The input list is
empty in all but 26 of 1,532 sessions, so a reader written from that population alone sees 11
instances and misses 407.

233 tests green.

## session_info() now reports the product, all three placement kinds, and machine context
Extended the consolidated inspector with today's readers:

    product        "Pro Tools Ultimate" / "Studio" / "HD" / "Pro Tools"
    placements     {"audio": 596, "midi": 1, "video": 0}   -- all three chains
    volumes        ["ABTB ROAD 1"]
    midi_inputs / midi_ports

Succeeds on 120/120 sessions. The docstring states explicitly that `n_clips` counts 0x1050 --
AUDIO only, matching clip_lanes/clips -- and points at `placements` for the MIDI and video chains
those two cannot see. That keeps the old field's meaning unchanged while making the gap visible
in the same object.

VERIFIED THE "port" LABEL RATHER THAN ASSUMING IT. Two of the first names printed were
`CLICK TRACK` and `TEMPO MAP`, which read like TRACK names -- so I checked all of them against
each session's own track list: **230 of 231 are NOT track names** (Stylus RMX 1, Micro Express
USB Port 1, Xpand2 1, Network M1 Mac ...). The label holds; those two are user-named ports. One
name coincidentally matches a track, which is what a 1-in-231 coincidence looks like.

That check took one script and would have caught a wrong label on a brand-new public API. Worth
doing every time a reader assigns a KIND to something, because the kind is the part callers will
trust without re-deriving.

233 tests green.

## THE ENTIRE READ LAYER WAS MISSING FROM THE PUBLIC API
Checking whether today's new readers reached the public surface turned up something bigger:
**none of the read layer was exported — not the new functions, and not the long-standing ones
either.** `clips`, `markers`, `tempo_map`, `meter_map`, `region_names`, `validate_pt_invariants`
were all reachable only as `ptxformatwriter.body_synth.<name>`.

The package docstring described workbench as "the curated session-construction toolkit", and
that was literally accurate: construction was curated and exported, INSPECTION was not. Half the
reimplementation -- the half that reads an arbitrary session and tells you what is in it -- was
effectively private.

FIXED: 22 readers now imported and exported by `workbench`, so they reach `ptx.<name>`:

    session_info, session_product, session_sample_rate, session_bit_depth, session_start_bar,
    clips, clip_lanes, clip_names, placement_lanes, playlist_names, track_playlists,
    region_list, region_names, markers, tempo_map, meter_map, base_tempo, base_meter,
    volumes, midi_devices, validate, validate_pt_invariants

`ptx.__all__` 82 -> 104, every export resolves, 233 tests green. The package docstring now
describes both halves instead of one.

WHY THIS MATTERED MORE THAN IT LOOKED: an API surface is part of the reimplementation, not
decoration. A caller following the package docstring would have concluded the library can BUILD
sessions but not INSPECT them -- which is wrong, and has been wrong for as long as the read layer
has existed. The test suite never caught it because the tests import from `body_synth` directly.
**Tests that import internals cannot detect a missing public export.**

---

## 2026-07-27 — the coverage tracker's "work queue" was 13/14 garbage

`gen_coverage.py` applied `is_phantom()` **only to UNDOCUMENTED types**. A phantom that happened
to be *mentioned* in the spec skipped the filter entirely and was reported as outstanding work.

That is self-defeating here, because **a phantom's mention is usually the spec explaining it as a
phantom.** The sentence deriving `0x0c00` from `256×12` is the very reason `0x0c00` counted as
"documented", and therefore the very reason it appeared on the list of things left to document.

Of the 14 types in the queue, **13 are phantoms and 1 (`0x2557`) is real.**

FIXED: `is_phantom` hoisted above the queue computation, and the tracker now splits the
mention-only list into phantoms and real types, reporting the real ones as the queue.

### Each phantom traced to the byte that produced it

Filters say *that* something is noise; they do not say *why*, and the missing "why" let a wrong
explanation stand: signature 3 claimed `0x0024`/`0x0033`/`0x0034`/`0x001c` came from the
`2A 00 00 00` GUID tag. They do not. All four are the **`ZY` in a track named `BASS CAB FUZZY`** —
one phantom, not four, in one session saved repeatedly.

The rest, all verified rather than asserted:

  * `0x2a00` (38 sessions) — the last letter of a name ending in `Z`. **38/38** confirmed: the
    zmark is the final byte of a length-prefixed string and `d[z+8] == 0x2A`. Two names produce
    every instance in the corpus: `DRUMZ` (31) and `ST PIZZ` (7).
  * `0x0c00` (34) — the `Z` in `iZotope`, plus a 12-byte string length.
  * `0x1900` (25) — a `Z` in an Antares Auto-Tune descriptor, plus the 25-byte length of
    `com.Antares.AutoTune81AAX`.
  * `0x0700` (1) — the 6th byte of a GUID, plus the 7-byte length of `Audio 6`.
  * `0x2757` (1) — the low byte of the file-descriptor COUNT (346) at the head of the `0x1004`
    payload. Proven: 347 children tile that payload exactly, and 346 of them are `0x1003`.

**A phantom's type number is a function of the user's text.** `0x2a00` exists because somebody
named a track `DRUMZ`. That is the deepest reason an unfiltered coverage number is meaningless:
it partly measures the corpus's naming habits.

## 2026-07-27 — REFUTED: "a container's children tile its payload exactly"

The `0x1004` result made this look like a general law, and a general law would have been worth a
lot: **phantoms could be eliminated structurally instead of filtered heuristically.**

They cannot. Only **28.5%** of container instances have an empty trailer. `0x261C` carries a
**542-byte trailer** after its single child. A walk that descends only where the payload tiles
exactly drops **80% of blocks** and **3,360 master-index targets**, and of the 59 types it
eliminates **0 are phantoms** — it deletes only real documented types.

**The first measurement said the opposite** ("83 of 88 container types tile at a constant `h`").
Two artifacts, both easy to repeat:

  1. **A degenerate solution that always succeeds.** `h = payload_length` tiles trivially with zero
     children, so the search never really failed — and for fixed-size blocks that meaningless `h`
     is a per-type constant, which got counted as confirmation. Fix: require ≥ 1 child.
  2. **A cap masquerading as a result.** The search stopped at `h ≤ 96`, so "never tiles" only
     meant "payload longer than 96 bytes".

Both inflated the same conclusion. It survived exactly as long as it was not asked to do any work.

### What survives — the real container law

    5A | block_type u16 | size u32 | content_type u16 | h header bytes | children… | trailer

  * first child at a per-type CONSTANT offset `h` — **119/129 container types**
  * trailer a per-type CONSTANT length — **114/129**
  * the 10 exceptions are exactly the types carrying a VARIABLE-LENGTH NAME in the header
    (`0x251A` 55/56/57, `0x2619` 91/92/93, `0x2602`, `0x2553`, `0x1041`, `0x202A`, `0x2633`) —
    the rule is parameterised by name length, not violated.

NOT acted on yet, deliberately: this `h` table would allow a stricter child enumeration (only
blocks on the chain from `h` are real children). `_size_driven_blocks` is load-bearing for every
PT-confirmed edit primitive, and I have just been wrong once today about a walk change. It is
recorded as measured data, to be adopted behind the same before/after corpus diff that caught the
`core.parsestring` regression.

## 2026-07-27 — the container-`h` law, corrected within the hour by a wider corpus

Recorded above as "119/129 container types have a constant first-child offset `h`", measured on 60
`control_files` sessions, with a note that it was worth adopting as a validator invariant.

Re-measured across all four corpora / 832 sessions: **136 of 260 (52%)**, not 92%. The wider corpus
holds twice as many container types and far more of them vary. And the containers with the MOST
instances all vary — `0x2602` (73,387 instances, every session), `0x251A` (48,592), `0x2106`
(36,849), `0x2628`, `0x1052`, `0x2619`, `0x2077`. The rule fails precisely where it would pay.

**NOT adopted.** The validator check would fire on ordinary untouched sessions, and "inert on
untouched sessions" has been the bar for every check added to `validate_pt_invariants`.

Also: `h` means "the first offset that begins a valid block chain", which inside an opaque payload
is a false zmark rather than a child — `0x4501` locks at h=6023 and `0x2023` at h=111, unanimously,
and neither is plausibly a child list. Unanimity is not realness.

**This is the FOURTH rule this session confirmed on `control_files` and refuted on a wider corpus**
(after the `0x2506` mono/stereo bit, `0x2628`'s parent set, and the `0x2011` class size). Standing
rule, earned four times over: a rule measured on `control_files` alone is a hypothesis. It is one
machine and one Pro Tools generation.

WHAT SURVIVES, and it is worth the pass on its own: **`0x1004` has h=4 in 831 of 832 sessions**,
and the one exception has h=0 — that exception being exactly the session whose file-descriptor
count is 346 = 0x0000015A, i.e. the session that produced the `0x2757` phantom. The count-field
explanation is confirmed corpus-wide, not just on the file it was dissected from.

## 2026-07-27 — do phantoms reach the public readers? Almost never, and the exception was luck

Today's phantom catalogue raises an obvious question about the reimplementation rather than the
documentation: every reader walks `_size_driven_blocks`, which by design frames noise inside leaf
payloads. If a phantom carries a READER-RELEVANT content type, a reader could return an object
built out of plug-in state.

Measured per instance over 522 sessions, for the 15 types the public readers key on
(`0x2619`, `0x2628`, `0x2629`, `0x2506`, `0x2028`, `0x2029`, `0x1014`, `0x2519`, `0x251A`,
`0x1052`, `0x1050`, `0x1057`, `0x1056`, `0x104F`, `0x2077`) — **1.1M instances**:

    phantom instances: 0    for 14 of the 15 types
    phantom instances: 10   for 0x251A, in 10 sessions

So the readers are clean, with one exception — and the exception was surviving on luck.

`_rekey_offsynth_positional` derived the track count as `len(by[0x251A]) // 2`, from a GLOBAL type
index. A phantom shifts the lane-group boundary and mis-numbers every ordinal in the second group.
In all 10 affected sessions (one project family; each phantom a 290-byte block inside a `0x1038`
audio payload) there is **exactly one** phantom and the total is **odd**, so `len // 2` absorbed it.
Nothing about a stray `0x5A` in audio data promises the next session will only have one.

FIXED: `_lane_blocks(by)` returns only `0x251A` blocks contained in the name table, falling back to
the unfiltered list if containment finds nothing.

Verified the way the `core.parsestring` regression taught:

  * lane-set comparison — removes exactly 1 entry from each of the 10, nothing from the other 434,
    derived count changes in **0 of 462**;
  * **A/B of the actual output** — `_rekey_offsynth_positional` hashed over 459 sessions with and
    without the filter: **byte-identical on all 459**, 0 errors. A counting argument is not an
    output measurement, and it was a counting argument that made `core.parsestring` look safe.

3 tests added, each verified to bite. 239 green.

Note the shape of this one: the bug was found by asking what today's DOCUMENTATION finding implied
for the CODE. The phantom catalogue was not written to find bugs.

## 2026-07-27 — remove_clip now works on all three placement chains

`remove_clip` was hardwired to the audio pair (`0x1052`→`0x1050`) and refused MIDI and video with
"no 0x1052 lane". None of the splice arithmetic was ever audio-specific: the three chains differ
only in the two OUTER content types and the `0x104F` placement record is shared. Only the two
constants were, and they are now looked up from `_PLACEMENT_CHAINS` via the holder's own type.

    audio   0x1052 -> 0x1050 -> 0x104F
    MIDI    0x1057 -> 0x1056 -> 0x104F
    video   0x1053 -> 0x1051 -> 0x104F

CORPUS BATTERY, 462 sessions, six structural properties per edit (index findable; every index
offset lands on a real block; exactly one lane's count changes; total `0x104F` drops by exactly 1;
no new validator error; the file shrinks):

    audio  373 clean / 0 failed / 0 refused
    midi    97 clean / 0 failed / 0 refused
    video    7 clean / 0 failed / 0 refused

**PT-CONFIRMED FOR AUDIO ONLY.** MIDI and video pass what audio passed BEFORE its PT run. `rc=0`
is not the oracle; PT is.

### The battery had a blind spot, and finding it was the useful part

477 clean edits and zero failures is exactly when to ask whether the battery can fail. Breaking
`remove_clip` three ways on purpose: skipping the enclosing-size shrink and skipping the
index-offset shift were both CAUGHT — but **skipping the count decrement was not**. Nothing
structural could catch it: `placement_lanes` counts wrappers by walking blocks, so a holder with a
stale u32 reads back perfectly.

That is this project's recurring failure shape — every reader returns `rc=0` and Pro Tools is the
one that objects.

NEW INVARIANT `placement-count`: a holder's declared count must equal the wrappers nested in it.
Read at the FIXED offset `first_wrapper - 4`, which equals the real count in **9423 of 9423** corpus
holders (9235 audio / 185 MIDI / 3 video, 100.00%). Fires on **0 of 432** untouched sessions.

Non-circular, unlike `_lane_count_pos`, which SEARCHES BACKWARDS for a u32 equal to the count it was
handed and so can only ever find a field that already agrees — it cannot detect a wrong one. The
100% fixed-position result also means its backward-scan fallback has never once been needed.

### A test that tested nothing

The first version of `ThreeChainClipRemovalTests` picked `placement_lanes(d)[0]`, which is an AUDIO
holder on every local session — so it passed with the fix reverted. Caught by running the bite check
rather than assuming it. `control_files` does hold 36 MIDI holders, so the test now selects a MIDI
holder explicitly and fails outright against the old hardwiring.

Video is NOT covered by a local test: `control_files` has zero video placements. It is exercised
only by the corpus battery (7/7 clean). Recorded rather than papered over with a test named for
video that quietly skips.

247 tests green.

## 2026-07-27 — the "saved twice" experiment, run on the corpus instead of on Pro Tools

Dozens of spec entries say a purpose cannot be settled from the corpus and name the experiment that
would: *one session, saved twice, one setting changed*. The corpus **already contains** it — PT
writes numbered `Session File Backups`, so every `.bak` chain is that experiment repeated:

    156 chains with >=2 generations, 2,103 consecutive pairs available
    330 pairs analysed (10 chains / 340 files, size-capped)

Per pair: which OBSERVABLES moved (tracks, names, markers, tempo, meter, regions, placements per
kind, playlists, rate/depth/start bar) vs which CONTENT TYPES changed count or bytes. Then both
directions of the implication.

### The first run was worthless and looked excellent

It "validated" the method against ground truth: `0x2629 <-> n_regions` 147/147 both ways,
`0x2077 <-> n_markers` 55/55, `0x1050 <-> place_audio` 140/140, `0x1014 <-> n_tracks`. Every one of
those is **CIRCULAR** — `region_list` READS `0x2628`/`0x2629`, `markers` reads `0x2077`,
`placement_lanes` counts `0x1050`. An observable computed FROM a type is not evidence ABOUT it.

Caught by asking why `0x1056 <-> place_midi` was exactly 21/21 before writing it down as settled.
The fix introspects each observable's reader for the types it mentions and drops those pairings
before scoring; 22 pairings went.

### What survives

**Non-circular, both directions >=0.90 — the per-track subtree, via a second method.** All track
`n_tracks`, which comes from `0x1014`/`0x1057` only:

    0x1052 0x2619 0x251A 0x2434 0x2015 0x200A 0x102D 0x2627 0x2626 0x261C 0x261B 0x200B
    P(type|tracks) 0.91-0.96, P(tracks|type) 0.98-1.00, n=50-55

Corroboration of what the structural walk already says, from "what moves when a user adds a track"
rather than "what is nested in what". First output of a new instrument should be the known answer.

**One-directional, hypotheses only:** `0x4403`->regions (18/18), `0x262F`->audio placements (47/47),
`0x261E`, `0x258E`, `0x202C`, `0x202A`, `0x200D` (13-15 each), all with weak converse (0.09-0.34).
10 chains from a few projects, and at n=13 this is close to the multiple-comparison budget. NOT
settled, and not written into the entries as settled.

**Two findings needing no correlation:**

  * **161 of 330 pairs changed NOTHING this library can read.** Half of all real saves alter mixer
    state, automation, plug-in or view state that the reimplementation does not see. That is a
    measurement of the remaining gap, not a guess at it.
  * **87 unsettled types never changed across any of the 330 saves.** Byte-stable across 330
    consecutive saves of 10 projects: they cannot encode anything those users touched. That is why
    corpus reading keeps failing on exactly these, and it is a real constraint on what they can be.

**Bonus, from a direction it was not designed for:** the types churning most in the 161 quiet pairs
are `0x2f00`, `0xb300`, `0x3300`, `0xaf00`, `0x5300`, `0x02c0`, `0x2ab0`... — the arithmetic shapes
of the phantom catalogue. Phantom counts fluctuate between saves of ONE session while the real
structure holds still, because the leaf payloads they live in are audio and plug-in data PT
rewrites. Independent evidence for the phantom classification.

### Does this close the PT gap?

No, and it should not be reported as if it did. It is a second instrument that makes some unsettled
entries narrower and tells us which ones are hopeless from the corpus (the 87). The 53 entries that
name a PT experiment still need one. What changed is that we can now say WHY for most of them.

## 2026-07-27 — per-FIELD semantics from a chain: no save counter exists, and that is the finding

I had written "I've taken the corpus-side work as far as I can see how to." That was wrong. The
pair analysis only asked WHETHER a type changed; a chain supports asking what each FIELD POSITION
does across saves. Never changes = constant. Strictly increases = counter or timestamp, one of the
few semantics that identifies itself.

3 chains x 24 generations, **383,975 field positions**:

    constant across all 24 saves    380,817   99.2%
    strictly monotonic                    0
    non-decreasing, >=3 values            17   all explained
    varies without order               3,141    0.8%

**No save counter and no timestamp in any stable-shaped block.** That is not a null result — it is
the reason a property this project depends on is achievable: **it is why an identity edit can be
byte-exact.** A format with a save counter or modified-time field could not round-trip to identical
bytes, and correctness property #2 would be unattainable rather than just hard.

### The 17 "stepped" hits were ASCII misread as integers

  * `0x2530`+79, `0x2581`+172, `0x258A`+181, `0x2066`+76, `0x2016`+766: 892353072 -> 808466992 is
    `'0:05'` -> `'0:00'`, an ASCII time display.
  * `0x2067`+245/275/287: `'k.00'` -> `'k.05'`, mid-string.

An integer-monotonicity scan over a format storing ASCII will always find "counters" that are text
drifting lexicographically. `'0:05'` as a little-endian u32 is 892,353,072 and decreases to `'0:00'`
exactly like a countdown.

### Fields that DO change every save, none a counter

`0x2097`+9 differs in all 20 generations of one chain, stepping by exactly 345,600 within a run —
but holds still for many saves in four other chains and jumps at discontinuities. `0x2067`+378 sits
inside a run of `0x01` filler. Not claimed as counters. Not identified.

### A flaw in the .bak method, found by using it

`.bak` numbering HAS GAPS — one chain runs 0,1,2,3,26,27,... — so "consecutive pair" was an
assumption, and a pair spanning a gap is many saves apart. Measured: **314 of 330 pairs are
genuinely adjacent**. Of the 14 non-circular relations, **9 survive** the adjacency restriction;
the 5 marginal ones (0x2627, 0x2626, 0x261C, 0x261B, 0x200B <-> n_tracks) sat at exactly 0.91 and
drop below the 0.90 threshold with 16 fewer pairs. Marginal, not refuted, and now reported as such.
Future passes should filter on `gap == 1`.

STILL NOT CLOSED: the 53 entries naming a PT experiment. What this pass adds is a reason to stop
looking for a save counter, a decoded explanation for six types' ASCII fields, and a corrected
method.

## 2026-07-27 — the controlled experiments I set up and then failed to collect

Last round found 18 pairs where exactly ONE observable changed and then never used them. In such a
pair every difference in the file is attributable to that one change. Collected now.

### 0x2629 is the AUDIO region detail; 0x2633 is the MIDI one; 0x2628 is shared

                                          0x2628   0x2629            0x2633
    7 pairs, only region count moved       +N      +N (always equal)  never present
    4 pairs, only MIDI clip count moved    +1      never present      +1

No exceptions in either direction. PT's complete footprint for adding ONE MIDI clip, in 4 of 4:

    0x104F +1 (placement)  0x1056 +1 (MIDI wrapper)  0x2628 +1 (region)  0x2633 +1 (MIDI detail)

`remove_clip` reverses only the first two, deliberately — it leaves the region in the clips bin,
PT-confirmed correct for AUDIO. This says the MIDI case has the same shape with 0x2633 playing
0x2629's role, which is the first evidence that the MIDI path's design is right rather than just
structurally sound.

### A divergence in rename_track, found without Pro Tools

Two pairs are pure renames. In BOTH, PT touched **0x2097** (I/O routing element handle); one added
a whole 0x2005/0x2006/0x2086/0x208C/0x2097 chain. Our `rename_track` rewrites name bytes and
changes NO block count at all.

PT appears to allocate a routing element on rename and we do not. Whether PT minds on reload is
exactly what only PT can answer — but it is now a SPECIFIC question, not a general doubt. Add it to
the staged-experiment list.

### A dead observable in my own instrument

`track_names` was `for n, _k in track_types(d)` — but `TrackInfo` is a dataclass, not a tuple, so it
raised TypeError on EVERY session, and the harness's `except: return None` folded that into
"unchanged". Dead in all 330 pairs, so a pure RENAME counted as a pair where nothing observable
changed.

FIXED: use `t.name`, and record a raised exception as a distinct value instead of None, so a broken
reader can never again masquerade as a stable one.

Effects: quiet pairs 161 -> 159 (48%); the "only n_tracks changed" pairs correctly became
two-observable (you cannot add a track without adding a name); and two genuine rename pairs
surfaced — which is where the rename_track finding above came from. **The published 161/330 figure
was slightly wrong and is corrected in the spec.**

### The caveat that bounds all of it

These footprints are COUNT deltas. A type whose BYTES changed while its count held still does not
appear — exactly what a rename does to the 0x2519 name table. Absence means "count unchanged",
never "untouched". A byte-level version of this pass is the obvious next instrument.

## 2026-07-27 — byte-level footprints, and the 12 types PT rewrites on every save

Built the byte-level pass I said was the obvious next instrument. Blocks aligned by
`(content_type, ordinal)` — position is useless, blocks slide — over the same 16 controlled pairs.

Raw result is noisy: a pure rename touches 40+ types. Most of that is not the rename.

**TWELVE TYPES CHANGE ON EVERY CONTROLLED PAIR, whatever the user did:**

    0x1038 0x2015 0x2016 0x2067 0x2613 0x2615 0x2616 0x261B 0x261C 0x261E 0x2624 0x2627

Plug-in state, view blobs, per-track view records. Useful twice: it EXPLAINS the 159 quiet pairs
(those saves changed only background), and it tells an editor which types are pointless to preserve
byte-exactly, because PT does not preserve them either.

Subtracting the background:

    markers        n=2   0x2030 0x2077                                      <- method check, correct
    MIDI clips     n=4   0x1056 0x1057 0x1058 0x2000 0x203A 0x2633 0x2634
    regions        n=7   (none unique -- shares 0x104F/0x1050/0x1052)
    rename         n=2   (none unique)
    audio clips    n=1   0x262F 0x2630                                      <- n=1, NOT evidence

The marker row is the method checking itself: 0x2030/0x2077 are the marker types and they are
exactly what falls out.

The MIDI row is the useful one. 0x1056/0x1057 are the known chain and 0x2633/0x2634 the MIDI region
records — the byte pass independently recovers the count-level finding. **0x1058, 0x2000 and 0x203A
are NEW**: bytes moved in all 4 MIDI pairs and in none of the other 12 controlled pairs.

For **0x2000** that is the first behavioural evidence of ANY kind. It occurs once per session in all
832, always top-level, and was the type this project's coverage tracker was caught calling
"documented" on four incidental mentions. Now there is something to say about it: it moves with MIDI
clips.

Leads at n=4, NOT settled. And stated plainly: at n=1 "changed in every pair" means "changed once",
which is no evidence; n=2 is weak; only the region (7) and MIDI (4) rows carry weight.

## 2026-07-27 — wider harvest: MIDI removal CONFIRMS remove_clip; two byte-level claims RETRACTED

Re-harvested controlled pairs for breadth: **34 pairs across 25 chains** (was 16 across ~6).

### STRENGTHENED — and it settles remove_clip's MIDI design

`0x2628`/`0x2629`/`0x2633` split now rests on 11 region-only and 7 MIDI-only pairs, still with zero
exceptions in either direction.

**The decisive pair: PT removes 35 MIDI clips at once.** `0x104F` -35, `0x1056` -35, and
`0x2628` **+1**, `0x2633` **+1**. The region records go UP, not down. Across all 7 MIDI pairs the
placement types equal the clip delta exactly (7/7, including the -35), while the region records
never follow it down.

`remove_clip`'s docstring says the clip's region records are "intentionally left untouched ... which
matches deleting a clip from the timeline" — PT-confirmed for AUDIO, assumed for MIDI. Pro Tools
deleting 35 MIDI clips without deleting one region record settles the assumption. This is the
strongest evidence yet obtained for the MIDI write path without running PT.

### RETRACTED — both from the commit I made an hour ago

  1. **"Twelve types PT rewrites on every save"** -> the real number is **4** (`0x2067`, `0x261B`,
     `0x261C`, `0x2624`). The other eight change on most saves but not all; they were an artifact
     of 16 pairs from ~6 chains. The weaker reading survives (PT rewrites a substantial background
     every save, which is why 51% of pairs look quiet), but the MEMBERSHIP was over-claimed.
  2. **"First behavioural evidence about 0x2000 — it moves with MIDI clips"** -> WITHDRAWN. At n=7
     its bytes change in every MIDI pair AND elsewhere, so it discriminates nothing. `0x203A` is not
     even in every MIDI pair. `0x2000` remains the type with no behavioural evidence of any kind.

I hedged that one explicitly ("leads at n=4, not settled") and the hedge was correct — but the
commit TITLE said "the first evidence about 0x2000", which was not hedged. A caveat in the body does
not rescue an over-claimed headline.

### SURVIVES

`0x1058` is unique to MIDI at n=7, alongside the known `0x1056`/`0x1057` chain. Genuinely new.

### NEW, on the same provisional terms

Playlist-count pairs (n=6) uniquely touch `0x2434`, `0x2515`, `0x2626`, `0x4420`. Offered on exactly
the terms the `0x2000` lead was offered on — which is the reason to treat it as provisional until a
wider harvest keeps or kills it.

## 2026-07-27 — widest harvest (74 pairs / 45 chains): playlist claim killed, 0x1058 confirmed

Ran the harvest I said would keep or kill the provisional playlist finding.

**KILLED.** `0x2434`/`0x2515`/`0x2626`/`0x4420` as playlist-specific: at 74 pairs across 45 chains,
not one is present in every playlist pair. Withdrawn.

That is the process working exactly as intended — flagged provisional, test named in advance, test
run, came back negative. It is also the second n=4-to-n=6 "finding" this instrument has produced
and then lost (after `0x2000`), which is a fact about the instrument worth remembering.

**CONFIRMED across three independent harvests** (n=16/34/74; 6/25/45 chains):

  * background is FOUR types: `0x2067` `0x261B` `0x261C` `0x2624` (stable at n=34 and n=74; the
    original twelve were an n=16 artifact);
  * `0x1056` `0x1057` `0x1058` MIDI-unique in all three. `0x1056`/`0x1057` are the known chain —
    **`0x1058` is the one genuinely new type this entire instrument has produced.**

**CALIBRATION, not discovery.** The tempo pairs (n=25, the largest group) uniquely select `0x2028`;
meter pairs (n=4) uniquely select `0x2029` and `0x2719`. All three are CIRCULAR — `tempo_map` reads
`0x2028`, `meter_map` reads BOTH `0x2029` and `0x2719`. I nearly wrote up `0x2719` as a new meter
association before checking. They prove nothing about those types, but they show the instrument
returning exactly the right answer and nothing else, at the largest n available, on a question whose
answer is known. That is calibration the circularity filter otherwise denies us.

**HONEST YIELD:** across 74 controlled pairs the instrument's only non-circular discovery is
`0x1058`. It mostly recovers what is already documented — reasonable for a new instrument, and a bad
reason to trust its outliers.

## 2026-07-27 — 0x2000 SETTLED: the slot rule made its own feature findable

The spec's slot rule says PT emits nine universal top-level slots unconditionally and fills each one
only when the corresponding feature is used — "an empty stub means feature absent, not block
missing". The RULE was documented; the FEATURES were not, and every one of those entries ended
"purposes UNPROVEN".

The rule itself makes them findable: split 612 sessions on whether the slot is populated, and ask
what the populated ones share.

**The slots co-populate in fixed GROUPS — the same sessions, not merely equal counts:**

    {0x2000, 0x2634}          the same 118 sessions
    {0x2424, 0x2426, 0x262C}  the same 18 sessions
    others                    singletons

**0x2000 and 0x2634 are the MIDI pair. All 93 sessions containing a MIDI clip populate both —
93/93, no exceptions.** Of the 118 populated, 93 have MIDI clips, 15 more have a MIDI/instrument
track with no clips; **10 are unexplained** and are worth a look.

That settles two entries that have said UNPROVEN since they were written, and gives `0x2000` — one
per session in all 832, always top-level — its first established meaning.

**0x2424/0x2426/0x262C: video REFUTED.** Leading candidate (10 of 13 video sessions populate the
group) but only 10 of 18 populated sessions contain video — fails both directions. The
co-population group is the new fact; the feature is still unknown.

**0x202E populated => MIDI, 19/19**, but only 20% of MIDI sessions populate it: a MIDI SUB-feature.
**0x2027** tracks session size (94% of >24-track sessions). **0x2073** has no clean correlate.

### On un-retracting

I withdrew "0x2000 moves with MIDI clips" earlier today. That withdrawal was correct for the
evidence it was based on — 4 byte-level pairs, and at n=7 its bytes moved elsewhere too. This is a
DIFFERENT test: slot population across 612 sessions. The conclusion returns in a much stronger form;
the same evidence was not re-asserted.

### The "53 unsettled sections" figure I have been quoting is inflated

    unsettled SECTIONS           54
      of which cover >1 type     26
    distinct TYPES named in them 120

A section is marked unsettled if ANY type in it is unproven, and 26 sections cover several types
each — two cover TEN. `0x1056` is counted unsettled while its own row in that table reads "**the
MIDI placement wrapper**" in bold. So "54 unsettled" is neither 54 types nor 120 types; it is 54
sections of mixed status, and the real per-type number is somewhere between.

Splitting those shared headings is the cheapest available reduction in the unsettled count and does
not need Pro Tools. Recorded as the next concrete task.

## 2026-07-27 — made the unsettled count PER-TYPE, and measured the inflation

The "53 unsettled" I quoted all session counts SECTIONS. A section is flagged when ANY type in it is
unproven, and 26 of 54 name several types — two name TEN. `0x1056` was counted unsettled while its
own row read "**the MIDI placement wrapper**".

FIXED: added machine-readable `**Purpose status (per type).**` lines to the two widest shared
sections, and `gen_coverage.py` now reads them, falling back to "every type in the heading" for
sections without one. So the reported per-type figure is an UPPER BOUND and is labelled as one.

    unsettled SECTIONS = 54 (26 multi-type)
    per-type: at most 114 unproven, 6 already settled inside flagged sections

Then checked how much of the remainder is inflation, by reading the four widest shared sections:

  * Pro Tools Studio family (9) — its own scope note says it "does NOT settle what any of them
    means — every one is byte-constant, so the corpus cannot". Genuinely all unproven.
  * 0x203x view-element family (6) — settled as a CLASS, individual roles unproven.
  * settings trees (6) and 0x1002/0x1005 group (6) — framing/parentage only, 1-3 instances each.

**So the inflation is real but small.** 6 types recovered; the widest remainder is genuinely
all-unproven. **114 is close to the true figure, not a loose bound** — worth knowing before anyone
spends effort attacking the COUNT rather than the types.

Side effect worth noting: `documented` rose 323 -> 331 and mention-only 13 -> 21, because prose I
wrote this session NAMES phantom type numbers (0x2f00, 0xb300, 0x02c0 ...) in the .bak churn list.
Under the "mention" standard, writing a hex string anywhere makes a type "documented". The queue
split still classifies all 21 as phantoms, so the real queue stays EMPTY — but it is a live
demonstration of why the mention standard is worthless on its own.

## 2026-07-27 — feature gates: what makes PT emit a type at all (14/114 explained)

Generalised the slot technique. It settled `0x2000` by splitting sessions on whether the slot was
populated; nothing in that is slot-specific. **Any type PT emits only when a feature is used betrays
the feature by being present in exactly those sessions.**

Applied to all **114** unproven types over **612 sessions** against 17 features, both directions,
with each feature's reader introspected so a reader mentioning the type under test cannot testify
about it.

**14 of 114 explained at 1.00 both directions. All 14 pairings non-circular.**

    VIDEO in the session   0x1002 0x1005 0x2039 0x2105   13/13 present, 0 of the other 599
    REGIONS present        0x1003 0x1033 0x2106          380/380 both directions
    MARKERS present        0x2506                        290/290
    product = PT Studio    0x483B 0x483D 0x4833 0x4809 0x4841   282-283/283

**NEW: the video row.** 13 of 612 sessions contain a video clip; all 13 carry those four types and
none of the other 599 does. `0x1002`/`0x1005` were filed under "all are rare (1-3 instances), so
these entries fix framing and parentage only" — **their rarity WAS the video feature's rarity.**

It also closes a loose end: video was REFUTED as the gate for the `0x2424`/`0x2426`/`0x262C` slot
group earlier today. Video's real footprint is these four; the slot group is something else.

**PROMOTED: the region row.** `0x1003`/`0x1033`/`0x2106` were a one-directional `.bak` result
("imply the audio placement set changed"); now two-directional at n=380.

**CALIBRATION: markers and Studio.** `0x2506` is the known marker matrix, the Studio family is
already documented product-gated — but both non-circular, so this is the instrument returning the
known answer at n=290 and n=283.

**A GATE IS NOT A MEANING.** This settles WHEN PT writes these types, not what their bytes say. For
`0x1002`/`0x1005` that is near identification; for `0x1003`/`0x1033`/`0x2106` it narrows without
answering. Entries updated to say gate-known / purpose-narrowed, NOT settled.

**100 of 114 are explained by none of the 17 features** — the useful negative. Their presence does
not track anything this library can measure, so the next advance there needs a new observable or
Pro Tools.

Per-type count: **at most 110 unproven** (from 114), 10 settled within flagged sections (from 6).

## 2026-07-27 — built a new observable (plugins) and it moved the gate count 14 -> 23

Last round ended: "100 of 114 explained by none of the 17 features ... needs a new observable or Pro
Tools." A new observable is buildable. Two gaps found while looking:

  1. `track_types` only ever reports mono/stereo/midi — no aux, master, VCA or folder kinds. A
     reader limitation worth its own fix; noted, not fixed here.
  2. **`0x1017`, the plug-in catalog, is fully decodable.** It is in LEAF_PAYLOADS so the WALK must
     not descend into it (plug-in names contain 'Z' and manufacture phantoms — the catalogue's
     0x0c00 is the Z in "iZotope"), but "do not descend" is not "cannot read".

        +9   u8   record kind (3 in 1537/1585; also 4, 8, 0xFF)
        +10  u32  display-name length      +14 .. name
        ..   12   three four-char codes (mfr/type/subtype, byte-reversed)
        ..   7    fixed                     .. u32 identifier length  .. identifier
        ..   ..   trailer (0 of 1534 blocks end at the identifier)

     **1534/1585 parse = 96.8%**; the 51 failures are the non-3 kinds and are skipped, not guessed.

SHIPPED: `plugins(data) -> [(display_name, identifier)]`, exported to the package root. Closes a
real gap — the library could not say what plug-ins a session uses. 5 tests, 252 green.

**Gate sweep re-run with the catalog as an observable: 14 -> 23 of 110 explained.** New, all
non-circular:

    ANY plug-in present   0x2715   408/408 both directions (and 0 of the other 204)
                          0x4530   0.99/1.00
                          0x270A 0x0F3E   1.00/0.97

`0x0F3E` is doubly interesting: it was in the set that NEVER changed across 330 consecutive saves,
so it is a session-invariant whose PRESENCE is nonetheless feature-gated.

The Studio family's `avid_only` hits (0.96/1.00) are NOT a separate gate — those types already match
`studio_product` at 1.00 and the populations largely coincide. Reported, not claimed.

### A test that skipped instead of failing

The first `PluginCatalogTests` looked for any control session with plug-ins and skipped if none had
them. Breaking the identifier offset by ONE BYTE made all four tests **skip** — and a skip is not a
pass. Fixed by pinning the fixture (`128 click tracks.ptx`, known to carry a populated catalog) so
"no plug-ins found" FAILS with a message saying the layout is wrong. Re-verified: the same one-byte
break now fails 4 tests.

Also corrected a test of mine that asserted `plugins(bytes(4096)) == []`. EVERY reader raises on
non-session input (`clips`, `markers`, `tempo_map`, `region_list`, `playlist_names`, `volumes` →
AttributeError; `track_types` → ValueError). `plugins` is consistent with them, so the TEST was
wrong. The library-wide behaviour is a wart worth fixing on its own terms, not by making one reader
special.

**87 of 110 still unexplained.** The ceiling moved because an observable was BUILT, not because more
sessions were read — so anything else this library learns to read is a candidate for another pass.

## 2026-07-27 — two predicted reader defects, both refuted by measurement

Building `plugins()` started from noticing `track_types` reports only mono/stereo/midi and uses
`flat_blocks`, the scan parser known to undercount. Two defects looked likely. Neither exists.

**No undercount.** `track_types` returns exactly the size-driven count of 0x1014 + 0x1057 in
**417/417** sessions. The `flat_blocks` undercount is real but hits PLAYLISTS, not track headers —
so the general warning about that parser does not transfer to this reader.

**No track-kind field to miss.** Every byte of a 0x1014 after the name, over 482 tracks:

    +0,+1     channel-map header: 01 02 stereo (83%), 00 01 mono (16%)
    +2,+3,+4  always 00
    +5        first channel index (144 distinct)
    +6        always 00
    +7        second channel index (105 distinct)
    +8,+9,+10 always 00
    +11       00, or the 2A GUID tag on mono where the shorter map shifts it forward

Everything is channel map or padding. **The three kinds are not a reader limitation — they are what
0x1014 encodes.** If aux/master/VCA are distinguished at all it is not here, and no corpus session
carries a track this reader can be shown to mislabel.

Recorded because a suspicion left unrecorded is a trap: the next person would go looking for a kind
byte that is not there. Also worth noting against my own track record this session — I have now
predicted a defect and been wrong twice (this and the phantom-contamination sweep, which found the
readers clean except one masked case), and been right twice (the 0x251A lane count, the missing
public exports). Predictions are cheap; the measurement is the work.

## 2026-07-27 — audio_files(): the library could not say what files a session needs

Nothing here could answer the question most often asked of a session file. The exports covered
WRITING wavs (`add_audio_clip`, `set_clip_wav`, `wrap_raw_wav`); none of them read the table.

`0x1004` opens with a u32 file count — the field whose low byte produced the `0x2757` phantom — and
one of its children is a `0x103A` name list of `u32 len | filename | 4-char format tag`, the tag
BYTE-REVERSED (`EVAW` = `WAVE`), the same convention the plug-in catalog uses.

SHIPPED: `audio_files(data) -> [(filename, format)]` and `audio_file_count(data)`, both exported.

**The reader checks itself against the file's own declaration** — `0x1004` says how many files there
are — and `audio_file_count` is exposed so callers can run the same check:

    sessions with a file table            462/462
    parsed count == DECLARED count        459  (99.4%)
    formats                               WAVE 29,153 · AIFF 53

### The bug it shipped with — third time this assumption has bitten

First version required every filename byte in 32..126. Filenames are UTF-8, macOS stores accents
DECOMPOSED, so `Butterbeans Café` and `Thalía with Dora the Explorer` were silently DROPPED — **20
of 462 sessions parsed fewer files than their own table declared**, every miss accented.

Caught only because the declared count disagreed. That is the whole argument for building the
self-check into the reader rather than adding it later.

After the fix: **260 non-ASCII filenames read**, agreement 95.7% -> 99.4%. The 4-byte format tag was
always the real validator; the ASCII test was redundant AND wrong.

Previous instances of the same assumption: the 0x2519 name-table walk (8 sessions parsed 29 of 180
entries) and the validator reading name offsets by CHARACTER count (23/440 false errors).

### Remaining 3 disagreements, reported not hidden

One parses 0 of 207; two parse 4 short. In those the format tag is not present for the missing
entries — a different record shape, not a missing tag value. Detectable by any caller via
`audio_file_count()`.

### A test of mine that failed for the wrong reason

`test_the_count_reader_is_independent_of_the_name_parse` searched the whole source of
`audio_file_count` for "audio_files" — and its DOCSTRING legitimately mentions it. Fixed by parsing
the AST and dropping the docstring before checking the body. Verified to bite: rewriting the count
reader as `len(audio_files(data))` now fails it.

257 tests green.

## 2026-07-27 — feeding audio_files() to the gate sweep explained NOTHING, and that is the finding

Applied the move that took gates 14 -> 23 (build an observable, re-run) to the new `audio_files()`
reader. Six features added: any files, many files, AIFF present, UTF-8 filenames, .L/.R stereo
pairs, file table disagrees.

**Zero extra types explained. Still 23 of 110.**

### Why — and it is measurable BEFORE running the sweep

    has_files  ~  regions        Jaccard 0.992   <- the same sessions
    audio_clips ~ regions        Jaccard 0.997
    has_plugins ~ elastic_audio  Jaccard 0.995
    studio_product ~ avid_only   Jaccard 0.956

A session with regions has files. A feature collinear with one already in the set carries no new
information and cannot separate anything new, whatever it measures.

**RULE for the next pass: compute a candidate feature's Jaccard against the existing set FIRST.**
High overlap means it cannot pay. `plugins` worked because it was orthogonal (a session can have
regions and no plug-ins); `audio_files` could not, because files and regions are the same sessions.

### A real negative worth keeping

Three of the six WERE orthogonal — `has_aiff` (n=18, closest 0.19), `utf8_filenames` (n=34, 0.38),
`file_table_disagrees` (n=7, 0.06) — and still explained nothing. So no unproven type's presence
tracks whether a session uses AIFF, carries accented filenames, or has a file table this library
parses imperfectly.

### A defective feature of my own, caught by the same matrix

`elastic_audio` overlaps `has_plugins` at **0.995**. I defined it as "any plug-in identifier
containing `fela`" — and `com.avid.aax.fela.2sola` is in almost every session, so it detected a
ubiquitous Avid plug-in rather than Elastic Audio use. Any conclusion resting on it would have been
"session has plug-ins" under a different name. None did, but only by luck — the overlap matrix is
now the check that would have caught it deliberately.

## 2026-07-27/28 — the EDIT layer was private too, and clip_positions had a live index bug

Fixed the reader exports earlier today and never checked the editors. **13 of 30 session-mutating
functions were not on the public surface**, including the flagship in-place primitives:

    duplicate_track  remove_track  remove_clip  move_clip  replace_clip_region
    replace_tempo_map  replace_meter_map  set_region_length  duplicate_region
    retarget_audio_file  duplicate_audio_file  set_waveform_view

The project's stated goal is surgically editing arbitrary existing sessions, and `duplicate_track`
was reachable only as `ptxformatwriter.body_synth.duplicate_track`. 13 now exported (2 kept
internal: `grow_one_midi_track`, `sync_marker_playlist_rows`). `ptx.__all__` 107 -> 119.

New test `test_no_session_mutating_function_is_accidentally_private` guards the CLASS, not the
list: any `body_synth` function whose signature is `(data: bytes, ...) -> bytes` must be exported or
named in an INTERNAL set with a reason. A new editor that nobody exports now fails a test.

### A live index bug in clip_positions / set_clip_position

Both used `flat_blocks`, the scan parser known to undercount. **Measured on 402 corpus sessions it
misses one 0x104F in `Tree Lighting 2019_R5` and three in `DWTS PT 2603 h.bak.006`.**

That is not cosmetic: `set_clip_position(lanes=(i,))` indexes POSITIONALLY into that list, so a
placement missing from the middle shifts every later index and the caller silently moves **the wrong
clip**, with nothing in the result to show it. Same defect class as the 0x251A lane count.

FIXED: both switched to `_size_driven_blocks`. Verified 402/402 count agreement, and an identity
rewrite (write every placement back its own value) is **byte-exact on all 402**.

### I wrote a function that already existed

I audited the edit surface, saw no `move_clip`, and wrote one — with lane+index addressing,
size-neutral, tests and all. **`move_clip` already existed at line 5188**, along with
`replace_clip_region`; my definition was silently shadowed by the later one, and the first sign was
a test failing with an error message my code could not produce.

Deleted mine. What the existing pair actually needed was not writing but two fixes:

  * **the export** (neither was public), and
  * **generalising `_locate_placement_104f` to all three chains** — it was hardwired to
    `0x1052`/`0x1050`, so `move_clip` and `replace_clip_region` refused every MIDI and video clip
    with a message naming the audio type. Same fix `remove_clip` needed. Verified by bite: with the
    locator reverted to audio-only a MIDI move is refused; with the fix it succeeds, size-neutral,
    and the position reads back.

The lesson is cheap to state and I paid for it anyway: **grep before you write.** The audit I ran
listed `move_clip` as unexported, which was true — I read it as absent.

### Two more tests of mine that checked the wrong text

`test_clip_positions_uses_the_size_driven_walk` searched `inspect.getsource` for `flat_blocks` and
found it in my own COMMENT explaining why it is not used. Same class as the `audio_file_count`
docstring case earlier. Both now parse the AST and compare `ast.unparse` output, which drops
comments and docstrings.

265 tests green.

## 2026-07-28 — swept every scan-parser user; found a second wrong-index bug

`clip_positions` was bitten by the `flat_blocks` undercount, so rather than wait to trip over the
next one I measured the whole exposure.

### How bad is the undercount, per type

    types UNDERCOUNTED by flat_blocks           80
      of them, in >=50% of sessions             35
    100% cases: 0x258C 372/372 · 0x2037 354/354 · 0x2580 333/333 · 0x2038 314/314
                0x203B 312/312 · 0x2555 284/284 · 0x4807 282/282

The undercount is not marginal — for several types the scan parser is short in EVERY session.

### It only matters where a POSITION is returned

Harmless when a function asks "does this exist". A silent data bug when it hands back an INDEX,
because a missing block shifts every later one. Two shipped functions did that, neither able to
report the error:

  * `clip_positions` / `set_clip_position` — 0x104F short in 2/402 sessions, so `lanes=(i,)` moved
    the WRONG CLIP (fixed in the previous commit);
  * **`selected_tracks` — 0x261C short in 9 of 312 sessions**, and it returns `i + 1`, so it handed
    back the WRONG TRACK NUMBERS. Fixed here. After: all returned indices lie within the track
    range on 342/342 sessions.

### Static analysis was not enough, three times over

  1. Searching sources for `flat_blocks` flagged `rename_track` and `set_clip_position` — both
     FALSE, the hits were in a COMMENT ("SIZE-DRIVEN throughout...") and a docstring. That is the
     THIRD time today a source-string search matched prose (after two tests). The fix each time is
     `ast.unparse`, which drops comments and docstrings.
  2. Matching hex literals missed everything, because the types arrive via constants
     (`_AUDIO_HDR`) or by argument.
  3. Instrumenting `_by_type` caught nothing — these functions iterate `flat_blocks` and compare
     `content_type` inline.

What worked was empirical: compare the scan count against the size-driven count per type, then
check the functions that return positions. Recorded because the wrong tool was tried three times
before the obvious one.

### Remaining users, deliberately not changed

18 private helpers still use `flat_blocks`, plus `overview_extent`, `session_name`,
`move_click_to_top`, `set_clip_wav`, `set_clip_wavs`, `track_types`. `track_types` is verified clean
(417/417). The others read single blocks or operate on synthesized donor data, and none returns a
positional index. Left alone rather than churned; the new test pins the rule for the three that do.

267 tests green.

## 2026-07-28 — validator is 100.00% inert on PT-authored sessions; its 6 hits were our own stale files

Audited the safety net itself. `validate_pt_invariants` guards every write path, so a false positive
there is worse than useless.

First pass, filtering our own output with an ad-hoc filename regex: **398 sessions, 392 clean, 6
firing `per-track-subtree-ordinal`**. The six looked like ours (`test_clickbottom_3stereo.ptx`,
`mixedmidi_click_top.ptx`) but my regex did not catch them.

**`session_is_probably_ours(data, path)` already existed** and is the sharp test — it compares the
session's self-recorded `.ptx` name in `0x2067` against the name on disk. Re-run with it:

    filtered out as OUR OWN output      85
    PT-authored checked                377
    zero errors                        377   ->  100.00%

**Every error invariant is perfectly inert on Pro Tools-authored sessions.** The 6 hits were all
our own output, which is exactly what a validator should do.

### Were the 6 a live bug? No — stale pre-fix artifacts

Each is the CLICK track taking per-track subtree ordinal 3 and colliding with a real track:
`[1,2,3,3]`, `[1..12,3]`, `[1,2,5,3,4,3]`. The validator's own comment already records the
calibration — *"strictly increasing in 1609/1609 Hack-drive, 250/250 Samples and 255/255 PT-authored
controls, and never repeat; only our own output does"* — so the check is right.

Tested the CURRENT path: `synthesize(spec, click=...)` for 3-stereo/12-stereo, click top and bottom,
and with no click — **ordinals distinct in every case**. Commit `09700fc` ("repeated 0x2015 ordinal
is now a validator ERROR, and it found the click path") is where this was caught and fixed. The six
files are untracked local artifacts predating it.

Worth leaving written down, because the next person to run the validator over `control_files` will
see 6 errors and needs to know they are historical.

### Second time today I rebuilt something that already existed

The ad-hoc `ours` regex was a worse `session_is_probably_ours`; earlier I wrote a `move_clip` that
was already there. Both cost a round trip. The check is cheap — grep the module for the concept
before writing it — and I did not do it either time.

## 2026-07-28 — parallel correctness audit: 25 confirmed bugs. plugins() FIXED, the rest queued

Ran a six-way parallel audit (identity / inverse pairs / self-checking readers / positional indices /
validator calibration / reader robustness), each finding adversarially verified by an independent
agent instructed to refute it. **25 confirmed at high confidence, 2 refuted.** 34 agents, 3.6M tokens.

### FIXED NOW — plugins(), which I shipped YESTERDAY with a flattering number

`plugins()` returned **[] on 128 of 407 PT-authored sessions (31.4%)**, 32 of them provably carrying
plug-in names in their own `0x1017` records.

Cause: v1 REQUIRED the identifier. Many kind-3 records carry only a display name, so any session
whose plug-ins are all identifier-less returned nothing. Record kinds measured: 3 (5940), 0xFF (588,
an EMPTY STUB), 4 (449), 0 (295), 8 (39) — v1 ignored the kind byte entirely.

It shipped on *"1534 of 1585 blocks parse, 96.8%"* — **25 sessions from one drive, quoted per BLOCK
when a caller experiences it per SESSION.** Both errors are ones this file has repeatedly warned
about. I wrote the warning and then made it the same day.

FIXES: accept identifier-less records (identifier reads `""`); skip the `0xFF` stub; and **scope to
the `0x1018` catalog** — every session has exactly one, plus exactly TWO `0x1017` outside it, and
reading those broke the oracle invariant on 24/407.

NEW ORACLE `plugin_count()`: `0x1018`'s u32 == its direct `0x1017` child count, 45/45 mine, 357/357
the audit's. Invariant `len(plugins) <= plugin_count`.

    plugins() == []                     128/407 (31.4%)  ->  0/407
    invariant violated                        24/407     ->  0/407

**`control_files` contains NEITHER failing case** — no identifier-less record, no `0x1017` outside a
catalog. That is exactly why it shipped: the local fixture pool cannot express the bug. The
regression test therefore BUILDS the case, blanking a real record's identifier-length u32 and
requiring the plug-in to survive. Verified to bite.

### CONFIRMED, NOT YET FIXED — in severity order

1. **`set_markers(d, markers(d))` DESTROYS marker data.** Byte-identical on 0 of 58 PT-authored
   sessions with markers; 56 LOSE blocks. Median -2,530 bytes, worst **-227,176 bytes / -12,950
   `0x2506` records**. A session with TWO top-level `0x2030` blocks has only one replaced by the
   template. **`markers(out) == markers(d)` and `validate()` reports no new issue**, so neither the
   reader nor the validator notices. This is the composition the docstring advertises.
2. **`retarget_audio_file` writes the sample count to the WRONG OFFSET.** It writes `0x1003+15`,
   which holds only 8448 or 4352 in **14,470 of 14,470** corpus records — a structural constant it
   therefore clobbers. The real sample count is at `+28` (matched 224/224). Identity 0/224.
3. **`set_tempo_map(d, tempo_map(d))`** differs on 297/342 and LOSES a `0x2028` block on 285 of
   them: the inlined `0x2718` template carries a stale nested block_size.
4. **`session_bit_depth()` returns 0** — not a bit depth — on 314/803 (all 221 PT Studio sessions).
5. **`selected_tracks()` is structurally blind outside PT Studio**: non-empty on 75/803, and `[]`
   is indistinguishable from "nothing selected".
6. **`duplicate_track`→`remove_track` loses 2 bytes** of the `0x2519` name table when the source is
   the last entry: 2 of 1421 round trips.
7. `_audio_file_list()` stops at the first FOLDER marker (`0x103A` interleaves folder `0x01` and
   file `0x02` entries) — short on 71/188, and `duplicate_audio_file` indexes into it.
8. `session_name()` indexes `[0]` of a `flat_blocks` list that prepends the index-pointer record.
9. `_FI.offset_holes` silently drops holes whose target the scan parser cannot type, leaving stale
   master-index offsets in `set_waveform_view`; `reindex_after_resize` inherits the path.
10. Validator: error-severity `audio-file-list` fires on 1 PT-authored session (a `0x5A` phantom).
11. `track_types()` misses an unnamed MIDI track header on 2 sessions.
12. `add_clip` rejects every MIDI/video lane `remove_clip` accepts — the pair is not closed.

None of these is speculative: each was measured against the corpus and survived an adversarial
refutation attempt. They are recorded here rather than half-fixed.

272 tests green.

## 2026-07-28 — set_markers no longer destroys the marker/playlist matrix (it refuses)

Worst confirmed bug from the audit, now closed by refusal rather than corruption.

### Mechanism, measured exactly

A session carries TWO top-level `0x2030` blocks, each holding N `0x2077` records, and the settled
relation `n(0x2506) == n(0x2077) x n_playlists` means each record owns one row PER PLAYLIST. The
inlined `0x2030` template holds exactly ONE row per record, so `_grow_block_into` replacing one of
the two blocks drops `N x (P-1)` rows.

`M12 MAGIC CARPET RIDE_MIX`: 2 markers, 4 `0x2077` records, 189 playlists.
`0x2506` 756 -> 380 — and 756 - 380 = 376 = 2 records x 188 lost playlists, exactly.

Before the guard: `set_markers(d, markers(d))` — the composition the docstring advertises — was
byte-identical on **0 of 58** PT-authored sessions with markers, and 56 LOST blocks. Median -2,530
bytes, worst **-227,176 bytes / -12,950 rows**. `markers()` still returned the right markers and
`validate()` reported no new issue, so nothing in the library noticed.

### Why refusal and not repair

Syncing the rows back afterwards is NOT a fix: `sync_marker_playlist_rows` adds to every marker in
EVERY `0x2030`, including the block that was never replaced. Measured: 380 -> 1132 where 378 is
correct. The real fix is to rewrite the existing `0x2030` IN PLACE instead of splicing a template
over it. Until that exists, refusing is the only honest behaviour for a write that would otherwise
destroy data it cannot restore.

    sessions with markers        134
    refused (would destroy)      132
    proceeded, no row loss         2
    proceeded and LOST rows        0   <- was 56

### FOURTH time the local fixture pool could not express the bug

`control_files` has no multi-playlist session carrying markers, so the corpus-driven test passed
**with the guard removed entirely**. Same as `plugins()` (no identifier-less record), the MIDI
clip-removal test (audio-only lanes) and the plug-in fixture search (skipped instead of failing).

Fix, as with `_lane_blocks`: extract the decision into `_marker_rows_per_record(blocks)` and test
THAT on synthetic block lists — `(4x 0x2077, 756x 0x2506) -> 189`. Verified to bite: stubbing the
helper to `return 1` fails the test.

**This is now a structural problem, not four coincidences.** `control_files` is one machine and one
generation; most bugs this project finds live on the wider corpora, and regression tests for them
cannot be written from the local pool. The workable pattern is the one used twice today: push the
decision into a small pure helper and test the helper.

277 tests green.

## 2026-07-28 — retarget_audio_file wrote the sample count to a CONSTANT, and overran short records

Queue item #2, closed. Two defects, both measured before changing anything.

### 1. The sample count went to the wrong field

`+15` is not the sample count. Across **17,900 `0x1003` records in 372 sessions** it takes exactly
two values — 8448 (17,890x) and 4352 (10x) — and does not vary with file length: 8448 appears on a
14,460,559-sample WAV and on an 8,448-sample one alike. It is a structural constant, and writing the
sample count there CLOBBERED it while leaving the real field untouched.

`+28` is the field: **3,320 distinct values** over the same records, and equal to
`wav_clip_identity(wav)[0]` on **40 of 40** (record, on-disk WAV) pairs — where `+15` matched **0**.

### 2. `+292`/`+301` overran the majority of records

`0x1003` spans VARY. Over 9,922 records the commonest span is **210 bytes** (318/320/314/321 also
common), so the fixed offsets `+292` and `+301` fall OUTSIDE most records. Measured with the bounds
checks removed, on a real 210-span record: **9 bytes written up to 93 bytes past the record end**,
inside the following block. Third instance of this class after `0x2107` and `0x251A`.

Both writes are now bounded. Verified on real 209/210-span sessions: bytes changed outside the
record = **0**, and only `+28`/`+44` are written there.

`+292`/`+301` are KEPT but flagged UNPROVEN: `+44` holds the umid exactly, while `+292` holds
structural-looking data (`00 13 00 ...` 3,418x, `01 0f 00 13 ...` 2,252x) that is not a
`00`-prefixed umid — and those two fields are the sole remaining reason that retargeting a record
to the WAV it already names is not byte-exact. Removing a write Pro Tools may require is not
something the corpus can justify; containing it is.

### The existing test ENCODED the bug

`test_retarget_audio_file_writes_the_wav_identity` read the sample count from `+15` and asserted a
retarget changed it. It passed only because the writer and the test agreed on the wrong offset —
fixing the writer is what surfaced it. Now reads `+28`.

### Fifth fixture-pool failure, same remedy

`control_files` has no `0x1003` record shorter than 311 bytes, so no local session can make the
unbounded version overrun. The write list is now the pure function `_retarget_field_writes(z, e,
...)` and is tested directly at spans 209-400. Verified to bite: returning the list unfiltered fails
two tests.

282 tests green.

## 2026-07-28 — set_tempo_map / set_meter_map no longer drop blocks (verify, then commit)

Queue item #3, closed the same way as `set_markers`: the SYNTHESIS writer must not quietly damage
an arbitrary session.

`set_tempo_map` splices the inlined `0x2028`/`0x2718` conductor templates over whatever is there.
Where the session's conductor blocks carry more than the template does, that DROPS blocks — an
identity `set_tempo_map(d, tempo_map(d))` differed on **297 of 342** sessions and lost a `0x2028`
in **285**, because the inlined `0x2718` ruler carries a stale nested-`0x2028` block_size.
`set_meter_map` differed on 14 of 57. Nothing noticed: the readers returned the right map from what
survived and `validate()` reported no new issue.

`replace_tempo_map` / `replace_meter_map` are the IN-PLACE editors for an arbitrary session and are
byte-identical on an identity replace (342/342). So the fix is to refuse and name them.

NEW PRIMITIVE `_blocks_lost_by_type(before, after)` -> `{content_type: count}`. A write that removes
blocks it cannot restore is the most damaging thing this library does and the least visible; this
makes it a one-line post-hoc check on any writer that splices templates.

    sessions with a tempo map     362
    refused (would drop blocks)   342
    proceeded, nothing lost        20
    proceeded and LOST blocks       0   <- was 285

### The guard had to be SCOPED, and the test suite is what said so

The first version fired on `set_tempo_map(unt, [(123,0)], t140, unt)` — the explicit
`tempo_ref`/`clean_ref` OVERRIDE path, which grows from a matched clean/ref donor pair rather than
from the inlined template. That is the synthesis mechanism, it legitimately produces a different
block count, and it has its own tests. The guard now applies only when `tempo_ref is None`, which
is exactly where the 297/342 damage was measured.

Worth noting which way that went: an existing test caught my over-broad guard, where earlier today
an existing test *encoded* the `retarget_audio_file` bug. The suite is not uniformly a rubber stamp
or a trap — it depends entirely on whether the test asserts a MEASURED property or a remembered one.

285 tests green.

## 2026-07-28 — session_bit_depth returned 0 on EVERY local control session

Queue item #4. The `0x1028` bit-depth field has TWO layouts: `+(size+2)` in one, `+(size+1)` in the
other. The reader knew only the first.

    over 432 corpus sessions      returned 0 on 314
    over control_files            returned 0 on 282 of 282   <- every local control session

The two offsets are complementary — `size+2` is zero in 318 of 432 and `size+1` carries `0x18` in
exactly those 318 — so preferring `size+2` and falling back to `size+1` when it reads zero yields a
plausible depth in **all 432**: 24-bit x416, 16-bit x16, and **0 x0**.

### What the old docstring claimed

*"PT-corpus-confirmed across all 26 sessions."* Meanwhile the function returned `0` — not a bit
depth, not a value of any kind — for all 282 sessions in `control_files`.

A confirmation that survives that cannot have looked at the RETURN VALUE. It checked that the call
did not raise. This is the same shape as the `retarget_audio_file` test that asserted the sample
count landed at `+15`: a claim of verification standing on something other than the answer.

Third recorded instance today of a "confirmed" number that was never really checked, after
`plugins()`'s 96.8% (per-block on 25 sessions from one drive) and that test.

### The test bites hard here

`control_files` is 282/282 the layout the old code got wrong, so reverting the fallback makes the
new test fail on the first session it touches — the rare case where the local corpus is the ideal
fixture rather than the obstacle it has been five times today.

287 tests green.

## 2026-07-28 — selected_tracks was trustworthy on 53 of 302 sessions and silent about the rest

Queue item #5. The selection flag is located by a fixed byte anchor
(`ef ff df bf ef ff df bf 02`) inside each `0x261C`, and the anchor is mostly absent.

    anchor in EVERY block        53   the only case the answer can be trusted
    anchor in SOME blocks        70   PARTIAL: tracks it cannot see are reported NOT SELECTED
    anchor in NO block          179   [] -- indistinguishable from "nothing is selected"

Trustworthy on 53 of 302; silently wrong on 249. The failures span every product — Studio 160,
HD 74, Ultimate 44, Pro Tools 16 — so this is not a product gate, the anchor simply is not there.

FIX: `strict=True` by default. It raises with the count of unreadable blocks rather than return an
answer it cannot stand behind, and names `strict=False` for callers who want the old behaviour.

    strict=True   answered 113, refused 249
    strict=False  answered 362  (old behaviour preserved)

### Two existing tests were passing VACUOUSLY

`test_no_stray_selection` asserts a synthesized session has nothing selected. The anchor is absent
from **all 3** of a synth session's `0x261C` blocks, so `selected_tracks` returned `[]` regardless of
what the flag said. **The test never verified "no stray selection" — it verified that an unreadable
flag reads as unset.** It has been green since it was written and proved nothing.

Both tests now pass `strict=False` explicitly, with the reason written where the call is. The stray-
selection one is kept because a regression that made the anchor APPEAR with a set flag would still
trip it, but it is no longer described as evidence for the property in its name.

That is the third distinct way a test in this suite has failed to test its subject today, after
"skips instead of failing" and "asserts the buggy offset". Worth a standing question at review time:
**if the subject were broken, which line fails?**

290 tests green.

## 2026-07-28 — duplicate->remove lost 2 bytes when the source was the LAST name-table entry

Queue item #6, and the one that touches a stated correctness property: the inverse
`remove_track(duplicate_track(d, src, new), new) == d` must hold byte for byte.

It did not when `src` was the last entry: `robust_audio_mixed12.ptx` 106859 -> 106857, block-type
counts unchanged, the entire 2 bytes lost from the `0x2519` name table.

### Cause: an invariant assumed instead of detected

`remove_track` truncated the PREDECESSOR's ordinal from 4 bytes to 2 whenever the removed entry was
last, reasoning that the last entry carries the SHORT special-last form and removing it strands the
predecessor with a full-width one.

**That form is not universal.** In these sessions the last entry's tail is the full
`0c 00 00 00 00`, and `duplicate_track` preserves it (`0d 00 00 00 00` after duplication). So the
truncation cut 2 bytes that nothing ever restored.

FIX: detect the form rather than assume it — compare the last entry's tail against its
predecessor's, and truncate only when the last one is genuinely shorter
(`0 < last_tail < prev_tail`).

    fixtures, every track      24/24 byte-exact  (was 22/24)
    corpus sweep, first+last   601 byte-exact, 0 differ, 23 refused, over 312 sessions

### The harness lied to me first

My first reproduction reported "dup refused 12/12, TypeError" and I nearly recorded the bug as
unreproducible. The TypeError was mine — `duplicate_track(data, source, new_name)` takes three
arguments and I passed two. Fifth harness artifact mistaken for a result in this project, after
`duplicate_track`'s "40% refusal rate", the allowlist sample, and `reorder_tracks`' permutation
length and meaning.

The tell was uniformity: 12 of 12 refusing with the SAME exception is a caller bug, not a data
property. Worth making the standing check — **when every case fails identically, suspect the
harness before the subject.**

292 tests green.

## 2026-07-28 — _audio_file_list stopped at the first folder marker (short on 34 of 121)

Queue item #7. The `0x103A` entry shape is uniform for both kinds:

    <u8 kind><u32 index><u32 namelen><name><4-byte tag>      stride 13+namelen
    kind 0x02 = FILE   (tag EVAW / FFIA)
    kind 0x01 = FOLDER (tag 00 00 00 00), e.g. 01 94 00 00 00 | 0b 00 00 00 | "Audio Files" | 00…

The walk tested `data[i:i+4] == b"\x02\x00\x00\x00"` — demanding kind 0x02 **and index 0**, which is
true only of the first group. The first file after a folder marker carries a NON-ZERO index
(`02 45 00 00 00 ...`), so the walk stopped dead there.

**Only the KIND byte identifies an entry; the u32 after it is an index, not padding.**

    walk == declared file count    87 of 121  ->  121 of 121

`duplicate_audio_file` range-checks against this list, so valid indices were refused and an index
past the cut addressed the wrong entry. `GENE TUNE.bak.011` walked 128 of the 352 files its own
table declares.

### And a fourth UTF-8 slip, caught by cross-reading

With the walk fixed it still disagreed with `audio_files()` on 10 of 121 sessions — same bytes,
same COUNT, different TEXT. `_audio_file_list` decoded latin1, turning `Café` into `CafeÌ\x81`.
Switched to `_decode_name`; agreement 111 -> 119 of 121.

Fourth appearance of this assumption, after the `0x2519` name-table walk, the validator's ordinal
offsets, and `audio_files()` itself. **Two readers of the same bytes disagreeing is a cheap, strong
check** — it found this without any external oracle.

### Sixth fixture-pool failure, same remedy

No `control_files` session contains a folder marker, so restoring the old condition left the corpus
test GREEN. The walk is now the pure function `_walk_audio_file_entries(data, start, end)`, tested
on a synthetic buffer containing a folder marker and a non-zero index. Verified to bite: the old
condition fails two tests.

298 tests green.

## 2026-07-28 — session_name read the file-header pointer record on 2 of 417 sessions

Queue item #8. The record at body offset 20 is framed like a block but is not one: the u16 read as
its content_type is the LOW HALF OF THE MASTER-INDEX OFFSET, so it takes a session-specific value in
every file. When that value happens to equal `0x2067`, it sorts FIRST in a `flat_blocks` scan and
`blocks[0]` is the 11-byte pointer record instead of the session-info block.

Measured: **2 of 417** — `mixedmidi_reorder_SMm.ptx` and `multiple track types no click fresh.ptx`,
both of which now return a name where they returned nothing.

Which sessions are hit is pure luck; it depends on the low 16 bits of an intermediate offset. The
hazard was ALREADY documented in `_blocks_by_type`, where the identical collision wrote into the
FILE HEADER and broke duplicate->remove on 2 of 213 controls. The note was there; this call site
just never got the same treatment.

FIXED: `session_name` uses the size-driven walk (which skips `z < 31`), and `_session_info_block`
filters `b.offset - 7 >= 31`.

**That is now FOUR readers fixed for the same root cause** — `clip_positions`, `set_clip_position`,
`selected_tracks` and `session_name` — all indexing `[0]` or `[i]` of a scan-parser list that can
contain a block that is not one. Worth stating as a rule rather than a series of incidents:

> **Never take `[0]` or `[i]` of a `flat_blocks` result.** The scan parser both undercounts real
> types (80 of them) and prepends a pseudo-block whose type is data-dependent.

301 tests green.

## 2026-07-28 — add_clip: MIDI/video refused, and it was the last private mutator

Queue item #9. `add_clip` was hardwired to the audio pair (`0x1052`/`0x1050`) and refused every MIDI
and video lane, so the inverse its docstring claims — *"the exact inverse of `remove_clip`,
validated by round-trip = byte-identity"* — held on ONE of the three chains.

Generalised via `_PLACEMENT_CHAINS`, the same fix `remove_clip`, `move_clip` and
`replace_clip_region` each needed. Round trip `remove_clip(add_clip(d, lane, pos), lane, n)`:

    audio   51 lanes   byte-exact
    midi    34 lanes   byte-exact   (previously refused outright)

### My own audit missed it, and the reason is worth keeping

The edit-surface audit that exported 13 mutators used `inspect.getsource(fn).split("\n")[0]` and
tested for `"data: bytes"` and `"-> bytes"` in that FIRST LINE. `add_clip`'s parameters wrap, so
`-> bytes` sits on line two — it stayed private while the audit reported the surface complete, and
the api-layer test I wrote to "guard the CLASS, not the list" inherited the same blind spot and
stayed green.

FIXED: `test_no_session_mutating_function_is_accidentally_private` now uses `inspect.signature`,
which reads the real thing. Re-run with it, `add_clip` was the ONLY remaining omission — so the
earlier fix was right about everything it could see.

**A structural check has to read structure.** Grepping source text for a signature is the same
mistake as grepping it for `flat_blocks` and matching a comment, which happened three times today.

305 tests green.

## 2026-07-28 — track_types missed an UNNAMED MIDI header, and my fix broke 114 tests first

Queue item #10. A `0x1057` header whose u32 name length is **0** is skipped by the scan parser, so
`track_types` under-reported by one on `Alabama.bak.019/020/022` (101 of 102, 102 of 103).

    track_types == size-driven count    519 of 522  ->  522 of 522

Fifth reader moved off the scan parser, after `clip_positions`, `set_clip_position`,
`selected_tracks` and `session_name`.

### A clean sample is not a proof — I had already "verified" this reader

Earlier today I measured `track_types` against the size-driven walk on **417 sessions, 417/417
clean**, and recorded it as sound: *"two predicted reader defects in track_types, both refuted by
measurement"*. A different sample of 522 found three failures. The refutation of the UNDERCOUNT
claim stands; the conclusion that the reader was therefore fine did not.

### And the fix broke the synthesis path

Switching unconditionally raised `AttributeError` in **114 tests**: `grow_one_track` calls this via
`channel_count` on a MID-CONSTRUCTION body that has no master index yet, and the size-driven walk
needs one. Every real session has an index; only this library's own intermediate buffers do not, and
those carry no unnamed header. So the walk is tried first and the scan parser is the fallback.

My test then failed too — it asserted `flat_blocks` never appears, which was written before I knew a
fallback was needed. Rewritten to assert the real property: the size-driven walk is the TRY body and
the scan parser appears only in the except branch, checked structurally on the AST.

**The corpus and the synthesis path are different oracles.** A change verified on 522 real sessions
still broke 114 tests, because none of those sessions is a half-built buffer.

309 tests green.

## 2026-07-28 — session_is_probably_ours globbed the ENTIRE FILESYSTEM

Found while chasing the last validator false positive, and worth more than the thing I was chasing.

`session_is_probably_ours(data, path)` defaulted `siblings` by walking UP:

    root = p.parent
    while root.parent != root and root.name not in ("control_files",):
        root = root.parent
    siblings = {q.name for q in root.rglob("*.ptx")}

Inside `control_files` that stops at the corpus. **Anywhere else it stops at `/`** and rglobs the
whole filesystem. Exactly the failure `tools/gen_coverage.py` documents for `glob("**/*.ptx")` on an
external volume: *"it stalls for HOURS at ~0% CPU, indistinguishable from a hang."*

A corpus sweep hung for **over half an hour** on `12 120 E.ptx` — a 0.09 MB session. The file is
innocent: it records its own name as **`12 120 E .ptx`, with a stray space**, so `recorded !=
p.name` and the unbounded branch fired. Every session before it recorded its name exactly and
returned early, which is why the cost was rare and catastrophic rather than uniform.

FIXED: the sibling set is now the file's own directory, its parent, and any
`Session File Backups` subfolder of either — the scope the docstring already implies ("the donor is
right there"). `control_files` keeps whole-corpus scope, since the 85-vs-4 separation was calibrated
with it. Verified: **85 ours / 197 PT-authored, unchanged**, and the pathological call is 0.00s.

### Three wrong diagnoses before the right one

  1. "The validator has a pathological case" — it does not; validate runs in 0.0s on that file.
  2. `signal.alarm` around the validate call — did not fire, so I concluded the hang was in
     `PTFFormat.load`. Also wrong.
  3. Alarm around the load too — still did not fire, because `alarm` cannot interrupt a long
     C-level call, and `rglob` is one.

What worked was the dullest possible instrument: **print the filename before touching it.** The same
lesson `gen_coverage.py` already carries in a comment — *"a long job that cannot say where it is
cannot be distinguished from a hung one"* — and I re-derived it the expensive way. I also misread
the log once, blaming index 172 when the print happens BEFORE the work, so the culprit was 171.

312 tests green.

## 2026-07-28 — the last two queue items: offset_holes dropped holes; the validator FP is gone

### #12 offset_holes silently dropped holes (FIXED)

`offset_holes` types each hole's target via `block_layout`, which is built from
`writer._flatten_block_bounds` — the SCAN parser, which undercounts 80 content types. Its inner
`add()` silently RETURNS when the target is not in that map, so a hole pointing at a block the scan
parser missed is **dropped** — and a dropped hole is never shifted on a resize, leaving a stale
master-index offset that nothing reports.

    sessions measured   321
    dropping holes        2     DWTS PT 2603 h.bak.006 (-3), 2805 PT V2a (-1)
    holes lost            4

`reindex_after_resize`'s `_resolve_holes_sized` fallback does NOT cover it: that path runs only on
an exception, and a dropped hole raises nothing.

FIXED by AUGMENTING rather than replacing — the scan layout stays the base so every rank that
already agreed is unchanged, and `_block_bounds_sized` (already in `final_index`, no new import)
adds what it missed. Verified: **342/342 sessions now match the size-driven reference**, and
duplicate->remove is byte-exact on 296 with 0 differing.

### #11 the validator false positive no longer reproduces

The audit reported error-severity `audio-file-list` firing on 1 PT-authored session. Re-measured
after today's `_audio_file_list` folder-marker fix: **406 PT-authored sessions, ZERO error
invariants firing.** The false positive was a symptom of the truncated file-list walk (the audit's
own note said "a 0x5A phantom in the 0x1..."), and fixing the walk removed it.

That is a useful shape to notice: **two of the twelve items were the same defect seen from
different ends.**

### All twelve closed

    1  set_markers destroyed the marker/playlist matrix        refuse
    2  retarget_audio_file wrote a constant / overran          +28, bounded
    3  set_tempo_map / set_meter_map dropped blocks            refuse
    4  session_bit_depth returned 0 on every local session     two layouts
    5  selected_tracks answered from unreadable data           strict=True
    6  duplicate->remove lost 2 bytes on the last entry        detect the form
    7  _audio_file_list stopped at the first folder marker     kind byte only
    8  session_name read the file-header pointer record        size-driven
    9  add_clip refused MIDI/video; last private mutator       chains + export
    10 track_types missed an unnamed MIDI header               size-driven
    11 validator audio-file-list false positive                gone with #7
    12 offset_holes dropped holes                              augment the map

Plus `plugins()` (empty on 31.4% of sessions) found and fixed during the same pass, and
`session_is_probably_ours` globbing the entire filesystem, found while chasing #11.

314 tests green.

## 2026-07-28 — AUDIT ROUND 2: 26 more confirmed defects on six new dimensions

The first audit covered identity, inverses, self-checking readers, positional indices, validator
calibration and robustness, and its twelve items are all closed. Round 2 took six dimensions it
never touched. **35 agents, 3.8M tokens, 26 confirmed at high confidence, 1 refuted.**

### FIXED IMMEDIATELY — a docstring I invalidated myself, today

`set_markers` still advertised *"`set_markers(d, markers(d))` composes"*. That sentence outlived the
guard I added hours earlier to stop the function destroying data: of 59 PT-authored sessions, 48
carry a marker and the call now raises on **48 of 48**. The composition it advertised works on
exactly the sessions where it is a no-op. Docstring corrected to say one-playlist-only and why.

### CONFIRMED, QUEUED — highest severity first

STATUS 2026-07-31: items 1-10 are CLOSED (see the sections above for each). Items 6 and 9 turned
out to be REFUTED rather than fixed, and item 5's proposed fix ("refuse a name collision") was
refuted by measurement — Pro Tools writes duplicate names itself — so the real defect there was
the name-based LOOKUP acting on every match. Remaining: the three synthesis items below.

**Silent corruption** — all CLOSED
 1. ~~`set_track_channels` — no bounds validation~~ CLOSED (`f2a06c1`)
 2. ~~`rename_track` with an empty `old_name`~~ CLOSED (`f2a06c1`)
 3. ~~`duplicate_region`/`duplicate_audio_file` splice INSIDE the previous last entry~~ CLOSED (`c671f34`)
 4. ~~`add_clip_to_track`/`add_clips_to_tracks` silently DROP clips~~ CLOSED (`c671f34`)
 5. ~~The duplicate family never checks `new_name` is free~~ CLOSED (`edbe345`) — reframed: PT
    writes duplicate names, so the defect was that removal/duplication acted on EVERY match.

**Wrong answers** — all CLOSED or REFUTED
 6. ~~`clips()`/`region_list()` use DIFFERENT region index spaces~~ **REFUTED** — 0 disagreements
    over 139 sessions (`7f855ad`)
 7. ~~`markers()` collapses distinct markers 209 -> 112~~ CLOSED (`7f855ad`) — real for 12 of 73
    sessions; for the other 61 the old reader was correct
 8. ~~`session_info()['n_tracks']` counts `0x261B`~~ CLOSED (`10c3319`) — 114 of 352 undercounted
 9. ~~`_lane_track_name()` returns `'Z'`~~ **REFUTED** at the source (`10c3319`)
10. ~~`validate()` fires on 33% of PT-authored sessions~~ CLOSED (`b67b35f`) — two causes, both
    name-table; 36/317 -> 0/317

**Synthesis**
11. ~~`synthesize()` raises `ValueError` at exactly **82 tracks**~~ **CLOSED** — the null pointer
    was real; cause was `_label_maps` using the scan parser (see the section above). All three
    specs now clean across 8..140, with 41/42 outputs byte-identical to before.
12. ~~From **n=83** every stereo synthesis trips the validator's CONTAINMENT error, including 2
    errors at n=418~~ **REFUTED** — swept 8..140 plus 200 and 418: zero validator issues at every
    n. The n=418 PT-confirmed record and the containment claim were never in conflict, because
    the containment error does not occur.
13. **Every click-bearing synthesis produces duplicated `0x2589` view-chain ordinals** (39/39 click
    cases; 0/35 non-click). I checked `per-track-subtree-ordinal` earlier today and found the click
    path clean — a DIFFERENT ordinal, so that check did not cover this.
14. `click='top'` silently reinterprets `display_order` against the post-click order.
15. `duplicate_track`->`remove_track` is not byte-identical on synthesized mono+stereo mixed specs,
    nor pure mono at N=41..64 (byte-exact at N=8..40).

**Documentation**
16. `set_region_length`: "2 bytes in ~94% of corpus records" — measured **6.3%**; 84.7% are 3 bytes,
    over 53,927 records. And it promises twice that it "raises rather than guessing" / is
    "size-neutral" while silently WIDENING the field and growing the body, 21 of 21.
17. `track_name_occurrences`: "a track name lives in 8 places" — true for 36 of 784 names (4.6%);
    the mode is 5.

**Verified CLEAN, worth recording**
 - `duplicate_track`->`remove_track` byte-identity on real sessions **170/170** (today's fix holds)
 - `replace_tempo_map` identity 59/59 · `plugin_count` 390/390 · `region_names` 342,767/342,767
 - the three-chain placement arithmetic 390/390 · the nine Studio-only types 0 violations in 390
 - `plugins()` non-empty 390/390 and `session_bit_depth` plausible 390/390 — both fixed today

### The shape of it

Round 1 found defects in the EDIT paths. Round 2 found them mostly in the SYNTHESIS path and in the
DOCUMENTATION — the two places least covered by the corpus-diffing habits this project has built,
because neither can be checked against a real session.

Nothing here is speculative: each survived an independent agent instructed to refute it, and the
two I re-measured by hand (`validate()`'s false positives, the `set_markers` docstring) both held.
