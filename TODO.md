# ptxformatwriter — TODO

Open research / feature items. Each entry should say what we want, what's unknown,
and why it matters. (Resolved items kept briefly for provenance.)

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

