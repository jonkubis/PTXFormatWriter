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
