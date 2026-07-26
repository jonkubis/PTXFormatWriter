"""Tests for the empty-stereo body synthesizer (`ptxformatwriter.body_synth`).

`synthesize_stereo_session` grows a donor session to N tracks. Validation is
structural (byte-exact is impossible across separate saves — FILETIMEs/session
metadata differ): the synthesized session must have the same per-type block
counts as the real N-track control and must re-parse cleanly. A 3-track result
from this pipeline is confirmed to open in Pro Tools.
"""
from collections import Counter
from pathlib import Path
import tempfile
import collections
import pathlib
import unittest

from ptxformatwriter.core import PTFFormat
from ptxformatwriter import body_synth as BS, writer as W, final_index as FI

ROOT = Path(__file__).resolve().parents[1]
CONTROL_DIR = ROOT / "control_files" / "lots of stereo tracks"
MONO_DIR = ROOT / "control_files" / "lots of mono tracks"
MIX_DIR = ROOT / "control_files" / "mixed tracks"
MTT_DIR = ROOT / "control_files" / "multiple track types"
MTT_NOCLICK = MTT_DIR / "multiple track types no click.ptx"
BIG_MONO = ROOT / "control_files" / "512 tracks" / "512 mono tracks.ptx"


def _control(n: int) -> Path:
    return CONTROL_DIR / f"{n} stereo tracks.ptx"


def _mono_control(n: int) -> Path:
    return MONO_DIR / f"{n} mono tracks.ptx"


def _mono_available() -> set[int]:
    return {n for n in range(1, 17) if _mono_control(n).exists()}


def _load_path(p: Path) -> bytes:
    ptf = PTFFormat()
    ptf.load(str(p), 48000)
    return ptf.unxored_data()


def _available() -> set[int]:
    return {n for n in range(1, 17) if _control(n).exists()}


def _load(n: int) -> bytes:
    ptf = PTFFormat()
    ptf.load(str(_control(n)), 48000)
    return ptf.unxored_data()


def _type_counts(data: bytes) -> Counter:
    # Exclude the first top-level block: its "content_type" is actually the low 16
    # bits of the stored index-offset pointer (session-specific), not a structural
    # block type, so it legitimately differs between synth and the real control.
    blocks = BS.flat_blocks(BS.parse(data))
    first = min(blocks, key=lambda b: b.offset)
    counts: Counter = Counter()
    for b in blocks:
        if b is first:
            continue
        counts[b.content_type] += 1
    return counts


@unittest.skipUnless(_available(), "stereo-track control files not present")
class BodySynthTests(unittest.TestCase):
    def _pairs(self):
        have = _available()
        return [(b, t) for (b, t) in [(2, 3), (2, 4), (3, 8)] if b in have and t in have]

    def test_structural_match(self) -> None:
        """Synthesized session has identical per-type block counts to the real
        control (using the target control as the per-track library)."""
        pairs = self._pairs()
        self.assertTrue(pairs, "need adjacent stereo controls")
        for base, target in pairs:
            synth = BS.synthesize_stereo_session(
                _load(base), base, target, library_data=_load(target), library_total=target
            )
            self.assertEqual(
                _type_counts(synth),
                _type_counts(_load(target)),
                msg=f"block-count structure mismatch for {base}->{target}",
            )

    def test_reloads_clean(self) -> None:
        """The synthesized + encrypted session re-loads without error."""
        for base, target in self._pairs():
            synth = BS.synthesize_stereo_session(
                _load(base), base, target, library_data=_load(target), library_total=target
            )
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(synth))
                path = tmp.name
            try:
                reloaded = PTFFormat()
                self.assertEqual(reloaded.load(path, 48000), 0, msg=f"reload failed for {base}->{target}")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_overview_order_matches(self) -> None:
        """The synthesized session reproduces the real control's overview
        display-order permutation exactly (a permutation of 0..N-1). Growing the
        body track-by-track would otherwise leave a corrupt sequence with
        duplicate values; synthesize rewrites it from the target control."""
        for base, target in self._pairs():
            synth = BS.synthesize_stereo_session(
                _load(base), base, target, library_data=_load(target), library_total=target
            )
            got = BS.overview_order(synth)
            want = BS.overview_order(_load(target))
            self.assertEqual(got, want, msg=f"overview order mismatch for {base}->{target}")
            self.assertEqual(
                sorted(got), list(range(target)), msg=f"overview not a 0..N-1 permutation for {base}->{target}"
            )

    def test_no_stray_selection(self) -> None:
        """A synthesized session opens with no tracks selected. Growing inherits
        the donor's last-track selection AND the appended last track's selection;
        the synthesizer must normalise that away (else two tracks open selected)."""
        for base, target in self._pairs():
            synth = BS.synthesize_stereo_session(
                _load(base), base, target, library_data=_load(target), library_total=target
            )
            self.assertEqual(
                BS.selected_tracks(synth), [], msg=f"stray track selection for {base}->{target}"
            )

    def test_multidigit_names_no_phantom(self) -> None:
        """Growing past 9 tracks (multi-digit names "Audio 10"...) must (a) build
        a name table whose 0x2519 size matches the real control, and (b) not forge
        phantom 0x0003 blocks (the selection fix used to corrupt an adjacent
        size-2 0x2103 block's content type on multi-digit tracks)."""
        have = _available()
        if not ({6, 16} <= have):
            self.skipTest("need n6 + n16 controls")
        synth = BS.synthesize_stereo_session(_load(6), 6, 16, library_data=_load(16), library_total=16)
        real = _load(16)

        def _size_2519(data: bytes) -> int:
            return next(b.block_size for b in BS.flat_blocks(BS.parse(data)) if b.content_type == 0x2519)

        def _count(data: bytes, ct: int) -> int:
            return sum(1 for b in BS.flat_blocks(BS.parse(data)) if b.content_type == ct)

        self.assertEqual(_size_2519(synth), _size_2519(real), msg="0x2519 name-table size mismatch")
        self.assertEqual(_count(synth, 0x0003), _count(real, 0x0003), msg="phantom 0x0003 block(s)")
        # Overview scroll-extent: 0 for <=9 tracks, non-zero once tracks overflow
        # the window (n13-16=602). The grow leaves it 0; synthesize copies it from
        # the target control. A wrong value here loads in ptxformatwriter but Pro Tools
        # rejects it ("magic ID does not match").
        self.assertEqual(
            BS.overview_extent(synth), BS.overview_extent(real), msg="overview scroll-extent mismatch"
        )
        self.assertNotEqual(BS.overview_extent(synth), 0, msg="extent should be non-zero at 16 tracks")

    def test_window_state_and_size(self) -> None:
        """Window state must match the target: (a) no body-size shift (the
        session-info 0x2067 size is matched to the target, else a 1-byte
        session-name-length difference shifts every later block and Pro Tools
        rejects it at >=10 tracks); (b) the visible-track marker (0x261c run1)
        pattern matches the real control (no stray donor marker)."""
        have = _available()
        if not ({6, 16} <= have):
            self.skipTest("need n6 + n16 controls")
        synth = BS.synthesize_stereo_session(_load(6), 6, 16, library_data=_load(16), library_total=16)
        real = _load(16)
        self.assertEqual(len(synth), len(real), msg="body size shifted vs real n16")

        run1 = bytes.fromhex("efffdfbf")

        def visible(data: bytes) -> list[int]:
            from ptxformatwriter import final_index as FI
            body = data[: FI.final_index_ref(data).start]
            blks = sorted(
                (b for b in BS.flat_blocks(BS.parse(data)) if b.content_type == 0x261C),
                key=lambda b: b.offset,
            )
            return [i + 1 for i, b in enumerate(blks) if run1 in body[b.offset : b.offset + b.block_size]]

        self.assertEqual(visible(synth), visible(real), msg="visible-track marker pattern mismatch")

    def test_rename_track(self) -> None:
        """rename_track replaces a track's name in all 8 length-prefixed slots
        (shorter or longer), removes the old name, leaves other tracks untouched,
        keeps the index offsets valid, and re-parses cleanly."""
        from ptxformatwriter import final_index as FI
        if 3 not in _available():
            self.skipTest("need 3-track control")
        base = _load(3)
        name_block_types = {0x2519, 0x251A, 0x1052, 0x1014, 0x210B, 0x2619}

        def in_name_blocks(data: bytes, name: str) -> int:
            ptf = BS.parse(data)
            blocks = BS.flat_blocks(ptf)
            n = 0
            for j in BS.track_name_occurrences(data, name):
                owner = max((b for b in blocks if b.offset - 7 <= j < b.offset + b.block_size),
                            key=lambda b: b.offset, default=None)
                if owner is not None and owner.content_type in name_block_types:
                    n += 1
            return n

        def bad_offsets(data: bytes) -> int:
            ref = FI.final_index_ref(data); z2t, bt = FI.block_layout(data)
            recs = FI.parse_records(ref.data, set(bt), set(z2t))
            vals = []
            for r in recs:
                for c in r.child_refs: vals.append(c.offset)
                for el in r.elements: vals += el.offsets
            return sum(1 for v in vals if v and (v >= len(data) or data[v] != 0x5A))

        def index_ptr(data: bytes) -> tuple[int, int]:
            first = min(BS.flat_blocks(BS.parse(data)), key=lambda b: b.offset)
            return int.from_bytes(data[first.offset:first.offset+4], "little"), FI.final_index_ref(data).start

        for new in ("Kick", "Bass Guitar DI Box (comp)"):
            out = BS.rename_track(base, "Audio 2", new)
            self.assertEqual(in_name_blocks(out, new), 8, msg=f"{new!r} not in 8 name slots")
            self.assertEqual(BS.track_name_occurrences(out, "Audio 2"), [], msg="old name remains")
            self.assertEqual(in_name_blocks(out, "Audio 1"), 8, msg="Audio 1 disturbed")
            self.assertEqual(in_name_blocks(out, "Audio 3"), 8, msg="Audio 3 disturbed")
            self.assertEqual(bad_offsets(out), 0, msg="index offset doesn't land on a ZMARK")
            # the first block's stored index-offset pointer must track the moved index
            ptr, start = index_ptr(out)
            self.assertEqual(ptr, start, msg=f"stale index-offset pointer for {new!r} ({ptr} != {start})")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(out)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"reload failed for {new!r}")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_set_session_name(self) -> None:
        """The embedded session name (the primary `.ptx` string in 0x2067) can be
        read and changed. Changing its length shifts the body, so this exercises
        the index-offset pointer fix on the 0x2067 block (the original
        session-name "magic ID" saga field) and must leave track names intact."""
        from ptxformatwriter import final_index as FI
        if 3 not in _available():
            self.skipTest("need 3-track control")
        base = _load(3)
        self.assertEqual(BS.session_name(base), "3 stereo tracks.ptx")
        for new in ("My Song", "A Much Longer Session Title Here"):
            out = BS.set_session_name(base, new)
            self.assertEqual(BS.session_name(out), new + ".ptx", msg=f"session name not set to {new!r}")
            first = min(BS.flat_blocks(BS.parse(out)), key=lambda b: b.offset)
            ptr = int.from_bytes(out[first.offset:first.offset+4], "little")
            self.assertEqual(ptr, FI.final_index_ref(out).start, msg="stale index-offset pointer after session rename")
            for k in (1, 2, 3):
                self.assertEqual(len(BS.track_name_occurrences(out, f"Audio {k}")), 8, msg=f"Audio {k} disturbed")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(out)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"reload failed for session {new!r}")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_emitter_round_trips(self) -> None:
        """apply_insertions with no insertions reproduces the body exactly."""
        from ptxformatwriter import final_index as FI
        for n in sorted(_available())[:4]:
            data = _load(n)
            body = data[: FI.final_index_ref(data).start]
            ptf = BS.parse(body)
            self.assertEqual(BS.apply_insertions(body, ptf, []), body, msg=f"emitter round-trip n={n}")

    def test_track_types_stereo(self) -> None:
        """track_types reads every clean-series track as stereo, and
        channel_count == 2N == the session's 0x1054+2 field."""
        for n in sorted(_available())[:6]:
            data = _load(n)
            tracks = BS.track_types(data)
            self.assertEqual([t.kind for t in tracks], ["stereo"] * n, msg=f"n={n} kinds")
            self.assertEqual(BS.channel_count(data), 2 * n, msg=f"n={n} channel_count")

    @unittest.skipUnless(_mono_available(), "mono control files not present")
    def test_mono_synthesis(self) -> None:
        """synthesize_mono_session grows a mono donor to N mono tracks: identical
        per-type block counts to the real mono control, re-parses cleanly, yields
        an all-mono track list, and channel_count == N (one channel per track)."""
        def mono(n: int) -> bytes:
            ptf = PTFFormat(); ptf.load(str(_mono_control(n)), 48000); return ptf.unxored_data()
        have = _mono_available()
        pairs = [(b, t) for (b, t) in [(2, 3), (2, 4), (3, 5), (3, 6)] if b in have and t in have]
        self.assertTrue(pairs, "need adjacent mono controls")
        for base, target in pairs:
            synth = BS.synthesize_mono_session(mono(base), base, target, mono(target), target)
            self.assertEqual(_type_counts(synth), _type_counts(mono(target)),
                             msg=f"mono block-count mismatch {base}->{target}")
            self.assertEqual([t.kind for t in BS.track_types(synth)], ["mono"] * target,
                             msg=f"not all-mono {base}->{target}")
            self.assertEqual(BS.channel_count(synth), target, msg=f"mono channels {base}->{target}")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(synth)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"mono reload {base}->{target}")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_set_track_channels(self) -> None:
        """set_track_channels rewrites a track's 0x1014 channel indices (mono or
        stereo) at the marker/name-relative positions, reads back correctly, and
        re-parses cleanly. (The channel-map rewrite that mix-and-match uses to place
        a track at its cumulative channel allocation.)"""
        if 2 not in _available():
            self.skipTest("need 2-track stereo control")
        data = _load(2)
        t0 = BS.track_types(data)[0]  # stereo Audio 1, channels [0,1]
        self.assertEqual(BS.track_channel_indices(data, t0.offset), [0, 1])
        out = BS.set_track_channels(data, t0.offset, [3, 4])
        self.assertEqual(BS.track_channel_indices(out, t0.offset), [3, 4], msg="stereo rewrite")
        # only the channel bytes changed (same length, re-parses)
        self.assertEqual(len(out), len(data))
        self.assertEqual(len(BS.flat_blocks(BS.parse(out))), len(BS.flat_blocks(BS.parse(data))))

    @unittest.skipUnless((MIX_DIR / "stereo mono stereo.ptx").exists(), "mixed control not present")
    def test_mixed_channel_allocation(self) -> None:
        """track_channel_indices reads the cumulative channel allocation from a real
        mixed control: stereo+mono+stereo -> [0,1], [2], [3,4]."""
        data = _load_path(MIX_DIR / "stereo mono stereo.ptx")
        got = [BS.track_channel_indices(data, t.offset) for t in BS.track_types(data)]
        self.assertEqual(got, [[0, 1], [2], [3, 4]])

    @unittest.skipUnless((MIX_DIR / "stereo mono stereo.ptx").exists() and _mono_available() and _available(),
                         "mixed + mono + stereo controls needed")
    def test_synthesize_mixed(self) -> None:
        """synthesize_mixed_session composes mono+stereo in any order: the result
        has the requested per-track types, the cumulative channel allocation, and
        re-parses cleanly. Covers a 2-track (no-growth) and 3-track (grown) case in
        both orderings."""
        def mono(n):
            p = MONO_DIR / f"{n} mono tracks.ptx"; ptf = PTFFormat(); ptf.load(str(p), 48000); return ptf.unxored_data()
        mono_lib = (mono(3), 3)
        stereo_lib = (_load(3), 3)
        cases = [
            ([2, 1], "stereo mono.ptx", "stereo mono.ptx"),
            ([1, 2], "mono stereo.ptx", "mono stereo.ptx"),
            ([2, 1, 2], "stereo mono.ptx", "stereo mono stereo.ptx"),
            ([1, 2, 1], "mono stereo.ptx", "mono stereo mono.ptx"),
        ]
        for specs, donor_fn, order_fn in cases:
            donor = _load_path(MIX_DIR / donor_fn)
            ov = BS.overview_order(_load_path(MIX_DIR / order_fn))
            synth = BS.synthesize_mixed_session(specs, donor, mono_lib, stereo_lib, ov, target_leaf="mixed tracks")
            kinds = [(t.kind, t.channels) for t in BS.track_types(synth)]
            self.assertEqual(kinds, [("stereo" if c == 2 else "mono", c) for c in specs], msg=f"types {specs}")
            chans, base = [], 0
            for c in specs:
                chans.append(list(range(base, base + c))); base += c
            got = [BS.track_channel_indices(synth, t.offset) for t in BS.track_types(synth)]
            self.assertEqual(got, chans, msg=f"channel allocation {specs}")
            self.assertEqual(BS.channel_count(synth), sum(specs), msg=f"channel count {specs}")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(synth)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"reload {specs}")
            finally:
                Path(path).unlink(missing_ok=True)

    @unittest.skipUnless({2, 3} <= set(_available()) and {2, 3} <= set(_mono_available()),
                         "need 2/3-track mono + stereo controls")
    def test_synthesize_mixed_arbitrary_default_order(self) -> None:
        """synthesize_mixed_session composes a config with NO matching control,
        using the default (identity) overview order — proving arbitrary mix-and-match
        needs no control. [stereo, stereo, mono] from a 2-stereo donor."""
        def mono(n):
            p = MONO_DIR / f"{n} mono tracks.ptx"; ptf = PTFFormat(); ptf.load(str(p), 48000); return ptf.unxored_data()
        specs = [2, 2, 1]
        synth = BS.synthesize_mixed_session(specs, _load(2), (mono(3), 3), (_load(3), 3))  # order defaults to identity
        self.assertEqual(BS.overview_order(synth), [0, 1, 2], msg="default identity order")
        self.assertEqual([(t.kind, t.channels) for t in BS.track_types(synth)],
                         [("stereo", 2), ("stereo", 2), ("mono", 1)])
        self.assertEqual([BS.track_channel_indices(synth, t.offset) for t in BS.track_types(synth)],
                         [[0, 1], [2, 3], [4]], msg="cumulative channel allocation")
        with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
            tmp.write(W.encrypt_session_data(synth)); path = tmp.name
        try:
            self.assertEqual(PTFFormat().load(path, 48000), 0)
        finally:
            Path(path).unlink(missing_ok=True)

    @unittest.skipUnless(BIG_MONO.exists() and 12 in _available(),
                         "need 512-mono universal library + 12-stereo control")
    def test_synthesize_mixed_high_n_512lib(self) -> None:
        """Arbitrary mix at N>9 sourcing mono units from the 512-mono control as a
        universal library: 9 mono (index 9 is past the old 0-8 mono lib) + 3 stereo,
        from a 2-mono donor. PT-confirmed at N=16/20 (test_stressA/B, 2026-05-30).
        Guards: track order/types, cumulative channel allocation, a clean final index
        (every offset hole resolves to a real block — no zeros/danglers), clean reload."""
        mono512 = _load_path(BIG_MONO)
        specs = [1] * 9 + [2] * 3            # mono at index 9 requires a >8-track mono lib
        synth = BS.synthesize_mixed_session(specs, _load_path(_mono_control(2)),
                                            (mono512, 512), (_load(12), 12))
        self.assertEqual([t.channels for t in BS.track_types(synth)], specs, msg="track order/types")
        self.assertEqual(BS.channel_count(synth), sum(specs))
        base, want = 0, []
        for ch in specs:
            want.append(list(range(base, base + ch))); base += ch
        self.assertEqual([BS.track_channel_indices(synth, t.offset) for t in BS.track_types(synth)],
                         want, msg="cumulative channel allocation")
        ref = FI.final_index_ref(synth)
        z2t, by_type = FI.block_layout(synth); zmarks = set(z2t)
        recs = FI.parse_records(ref.data, set(by_type), zmarks)
        offs = [c.offset for r in recs for c in r.child_refs] + \
               [o for r in recs for e in r.elements for o in e.offsets]
        self.assertTrue(offs and all(o in zmarks for o in offs), msg="all index offsets resolve")
        with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
            tmp.write(W.encrypt_session_data(synth)); path = tmp.name
        try:
            self.assertEqual(PTFFormat().load(path, 48000), 0)
        finally:
            Path(path).unlink(missing_ok=True)

    @unittest.skipUnless((CONTROL_DIR / "512 stereo tracks.ptx").exists() and 8 in _available(),
                         "need 512-stereo universal library + 8-stereo donor")
    def test_name_table_special_last_non_matching(self) -> None:
        """REGRESSION (PT-confirmed 2026-06-19, macpair N=30): with no matching N-track
        control, synthesize_mixed_session must rebuild the 0x2519 name table as
        [header][N-1 normal][1 special-last]. The grow path otherwise strands the donor's
        SPECIAL last entry mid-table (only base_n entries parse) -> Pro Tools throws
        'end of stream encountered'. Guards: all N name entries present + clean reload."""
        N = 30
        lib512 = _load_path(CONTROL_DIR / "512 stereo tracks.ptx")
        synth = BS.synthesize_mixed_session([2] * N, _load(8), (b"", 0), (lib512, 512))
        blk = BS._by_type(BS.parse(synth), 0x2519)[0]
        first_child = min(c.offset - 7 for c in blk.child)
        n_entries = len(BS._name_table_entries(synth[blk.offset:first_child]))
        self.assertEqual(n_entries, N, msg="0x2519 must contain all N name entries (special-last fix)")
        self.assertEqual(len(BS.track_types(synth)), N)
        with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
            tmp.write(W.encrypt_session_data(synth)); path = tmp.name
        try:
            self.assertEqual(PTFFormat().load(path, 48000), 0)
        finally:
            Path(path).unlink(missing_ok=True)

    @unittest.skipUnless(MTT_NOCLICK.exists(), "multiple-track-types control not present")
    def test_track_types_mixed(self) -> None:
        """track_types distinguishes mono / stereo / MIDI in the mixed control:
        Audio 1 (mono, 1ch), Audio 2 (stereo, 2ch), MIDI 1 (midi, 0ch); total
        audio channels = 3 = the 0x1054+2 field."""
        data = _load_path(MTT_NOCLICK)
        got = {t.name: (t.kind, t.channels) for t in BS.track_types(data)}
        self.assertEqual(got.get("Audio 1"), ("mono", 1), msg="Audio 1 should be mono")
        self.assertEqual(got.get("Audio 2"), ("stereo", 2), msg="Audio 2 should be stereo")
        self.assertEqual(got.get("MIDI 1"), ("midi", 0), msg="MIDI 1 should be midi")
        self.assertEqual(BS.channel_count(data), 3, msg="total audio channels")


class StereoInlineTests(unittest.TestCase):
    """The library-free, no-external-files stereo synthesizer (`synthesize_stereo_inline`):
    inlined 8-track scaffold + two inlined 512-control unit templates + `synth_stereo_unit`
    replace BOTH the per-N stereo library and the donor file. These tests need NO control
    files (they exercise the inlined assets) -- so they run everywhere."""

    def test_inline_builds_with_no_external_files(self) -> None:
        """`synthesize_stereo_inline(n)` builds a valid n-stereo session from inlined assets
        alone: correct track count, all n name-table entries, clean reparse + reader load.
        n=12 and n=30 are byte-identical to PT-confirmed files (verified at integration)."""
        for n in (8, 17, 30, 63):
            synth = BS.synthesize_stereo_inline(n)
            self.assertEqual(len(BS.track_types(synth)), n, msg=f"n={n} track count")
            blk = BS._by_type(BS.parse(synth), 0x2519)[0]
            first_child = min(c.offset - 7 for c in blk.child)
            n_entries = len(BS._name_table_entries(synth[blk.offset:first_child]))
            self.assertEqual(n_entries, n, msg=f"n={n}: all name entries present")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(synth)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"n={n} reload")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_inline_guards(self) -> None:
        """n below the scaffold (8) has no grow path; n above the validated ceiling (418)
        hits the 0x5A-in-data scan-parse limit -- both raise ValueError, never silent
        breakage."""
        with self.assertRaises(ValueError):
            BS.synthesize_stereo_inline(5)
        with self.assertRaises(ValueError):
            BS.synthesize_stereo_inline(BS._STEREO_INLINE_MAX + 1)

    def test_inline_dup_mode_also_builds(self) -> None:
        """`unique=False` (per-track GUID/blob duplicated) is also PT-valid (confirmed:
        nolib30_dup) -- guard that it still produces a well-formed n-track session."""
        synth = BS.synthesize_stereo_inline(12, unique=False)
        self.assertEqual(len(BS.track_types(synth)), 12)

    def test_generated_free_fields_are_magic_free(self) -> None:
        """REGRESSION: the block parser scans payloads for the 0x5A magic, so the GENERATED
        free fields (per-track 8-byte handle + 160 B blob) must contain no 0x5A -- otherwise
        they form phantom blocks when the grow re-parses (handle 0xE000+346 = 0x..5A broke
        N>=347). The deterministic fields match a real control, so only these need guarding."""
        from ptxformatwriter import _stereo_data as SD
        t1, t2, t3 = SD.stereo_templates()
        for k in (90, 346, 512):   # k where the generated handle/blob would carry a 0x5A
            u = BS.synth_stereo_unit(k, t1, t2, t3)
            M = {1: BS._STEREO_MAP_1, 2: BS._STEREO_MAP_2, 3: BS._STEREO_MAP_3}[len(str(k))]
            a, b = M["b261c"]["blob"]
            self.assertNotIn(0x5A, u.b261c[a:b], msg=f"k={k}: blob carries the 0x5A magic")
            blocks = BS._stereo_unit_blocks(u)
            for key, spec in M.items():
                for off in spec.get("guid", []):
                    self.assertNotIn(0x5A, blocks[key][off:off + 2],
                                     msg=f"k={k} {key}@{off}: handle carries the 0x5A magic")


@unittest.skipUnless((CONTROL_DIR / "512 stereo tracks.ptx").exists(),
                     "need the 512-stereo control to validate the deterministic re-key")
class SynthStereoUnitTests(unittest.TestCase):
    """`synth_stereo_unit`'s DETERMINISTIC re-key (name, lane indices, track #, the ten
    element ids, the k/k-1 counters) must reproduce the real 512-control unit byte-for-byte
    -- the property that lets the generator replace the library. Only the self-contained
    GUID/blob are regenerated (PT needs them only present), so those sites are excluded."""

    def test_deterministic_rekey_reproduces_512_unit(self) -> None:
        lib512 = _load_path(CONTROL_DIR / "512 stereo tracks.ptx")
        for k in (3, 9, 10, 30, 63):                       # both digit ranges
            ref = BS.extract_track(lib512, k, 512, channels=2)
            ref_blocks = BS._stereo_unit_blocks(ref)
            M = BS._STEREO_MAP_1 if len(str(k)) == 1 else BS._STEREO_MAP_2
            for key in BS._STEREO_UNIT_KEYS:
                spec = M.get(key)
                if spec is None:
                    continue
                buf = bytearray(ref_blocks[key])           # start from the REAL unit
                o, w = spec["name"]
                buf[o:o + w] = str(k).encode()
                for off, wd, fn in spec["lin"]:
                    buf[off:off + wd] = int(fn(k)).to_bytes(wd, "little")
                self.assertEqual(bytes(buf), ref_blocks[key],
                                 msg=f"k={k} {key}: deterministic re-key must equal the real unit")


_MONO512 = ROOT / "control_files" / "512 tracks" / "512 mono tracks.ptx"


@unittest.skipUnless(_MONO512.exists(), "need the 512-mono control to validate the mono re-key")
class SynthMonoUnitTests(unittest.TestCase):
    """`synth_mono_unit`'s deterministic re-key must reproduce the real 512-mono unit
    byte-for-byte (the property that lets the generator replace a mono library). Mono is
    the audio structure with ONE 0x1052 lane and channel index k-1."""

    def test_deterministic_rekey_reproduces_512_mono_unit(self) -> None:
        d = _load_path(_MONO512)
        for k in (3, 9, 10, 99, 100, 256, 300, 511):     # both digit ranges + the u16 transition
            ref_blocks = BS._mono_unit_blocks(BS.extract_track(d, k, 512, channels=1))
            M = {1: BS._MONO_MAP_1, 2: BS._MONO_MAP_2, 3: BS._MONO_MAP_3}[len(str(k))]
            for key, spec in M.items():
                buf = bytearray(ref_blocks[key])
                o, w = spec["name"]
                buf[o:o + w] = str(k).encode()
                for off, wd, fn in spec["lin"]:
                    buf[off:off + wd] = int(fn(k)).to_bytes(wd, "little")
                self.assertEqual(bytes(buf), ref_blocks[key],
                                 msg=f"k={k} {key}: mono re-key must equal the real unit")

    def test_synth_mono_unit_is_one_lane(self) -> None:
        d = _load_path(_MONO512)
        t = tuple(BS.extract_track(d, kk, 512, channels=1) for kk in (2, 10, 100))
        u = BS.synth_mono_unit(30, *t)
        self.assertEqual(len(u.b1052), 1, msg="mono unit must have exactly one 0x1052 lane")
        self.assertEqual(u.name_entry[10:12], b"30", msg="track-30 name digits")


class MonoInlineTests(unittest.TestCase):
    """`synthesize_mono_inline` builds an n-mono session from inlined assets alone (8-track
    scaffold + 512-mono templates in `_mono_data`), no external control files."""

    def test_inline_mono_builds(self) -> None:
        for n in (8, 30, 100):
            synth = BS.synthesize_mono_inline(n)
            self.assertEqual(len(BS.track_types(synth)), n, msg=f"n={n} tracks")
            self.assertEqual(BS.channel_count(synth), n, msg=f"n={n} mono = 1 channel/track")
            blk = BS._by_type(BS.parse(synth), 0x2519)[0]
            first_child = min(c.offset - 7 for c in blk.child)
            self.assertEqual(len(BS._name_table_entries(synth[blk.offset:first_child])), n)
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(synth)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"n={n} reload")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_inline_mono_no_chimera(self) -> None:
        """The 512-mono templates' folder leaf is normalized to the scaffold's (no chimera)."""
        from ptxformatwriter import _mono_data as MD
        synth = BS.synthesize_mono_inline(30)
        self.assertEqual(BS.track_name_occurrences(synth, MD.TEMPLATE_LEAF), [],
                         msg="template folder leaf must be renamed away")

    def test_inline_mono_guards(self) -> None:
        with self.assertRaises(ValueError):
            BS.synthesize_mono_inline(5)
        with self.assertRaises(ValueError):
            BS.synthesize_mono_inline(BS._STEREO_INLINE_MAX + 1)


class MixedInlineTests(unittest.TestCase):
    """`synthesize_mixed_inline` builds an arbitrary mono+stereo ORDER from inlined assets
    (4 start-pair donors in `_mixed_data` + the stereo/mono unit templates), no external files."""

    def test_arbitrary_order_builds(self) -> None:
        from ptxformatwriter import _mixed_data as XD
        for spec in ([2, 1, 2, 1, 2, 1, 1, 2, 2, 1], [1, 2, 1, 2, 1, 2, 2, 1, 1, 2],
                     [2] * 8, [1] * 8, [2, 1] * 10):
            synth = BS.synthesize_mixed_inline(spec)
            self.assertEqual([t.channels for t in BS.track_types(synth)], spec, msg=f"spec {spec}")
            self.assertEqual(BS.channel_count(synth), sum(spec), msg="total channels")
            base, want = 0, []
            for ch in spec:
                want.append(list(range(base, base + ch))); base += ch
            self.assertEqual([BS.track_channel_indices(synth, t.offset) for t in BS.track_types(synth)],
                             want, msg="cumulative channel allocation")
            donor = XD.mixed_donor(spec[0], spec[1])
            dl = BS._folder_leaf(donor)
            for tl in (XD.STEREO_TEMPLATE_LEAF, XD.MONO_TEMPLATE_LEAF):
                if tl != dl:
                    self.assertEqual(BS.track_name_occurrences(synth, tl), [],
                                     msg=f"template leaf {tl!r} must be normalized (no chimera)")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(synth)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"reload {spec}")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_mixed_guards(self) -> None:
        with self.assertRaises(ValueError):
            BS.synthesize_mixed_inline([2])           # < 2 tracks (no grow anchor)
        with self.assertRaises(ValueError):
            BS.synthesize_mixed_inline([2, 3])        # 3 = invalid channel count


class SynthesizeTests(unittest.TestCase):
    """The unified `synthesize(spec, click=...)`: arbitrary mono+stereo order + an optional
    single Click track (top/bottom), all from inlined assets (no external control files)."""

    def test_synthesize_variants(self) -> None:
        cases = [([2] * 12, "top"), ([1] * 12, "bottom"), ([2, 1, 2, 1, 2, 1, 1, 2], "bottom"),
                 ([2, 1, 2, 1], None), ([1, 2, 1], "top")]
        for spec, click in cases:
            out = BS.synthesize(spec, click=click)
            self.assertEqual([t.channels for t in BS.track_types(out)], spec, msg=f"spec {spec}")
            self.assertEqual(bool(BS.track_name_occurrences(out, "Click 1")), click is not None,
                             msg=f"click {click} for {spec}")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
                tmp.write(W.encrypt_session_data(out)); path = tmp.name
            try:
                self.assertEqual(PTFFormat().load(path, 48000), 0, msg=f"reload {spec} click={click}")
            finally:
                Path(path).unlink(missing_ok=True)

    def test_synthesize_click_guard(self) -> None:
        with self.assertRaises(ValueError):
            BS.synthesize([2, 2], click="middle")

    def test_no_volume_view_blocks(self) -> None:
        """REGRESSION: every track's edit-window view must be WAVEFORM, not volume. The click
        splice (and a click-converted donor/scaffold track) can leave volume-view blocks (0x203b
        under 0x2015/0x2589); `synthesize` runs `set_waveform_view` last to force waveform."""
        for spec, click in (([2, 1, 2, 1, 2, 1, 1, 2], "bottom"), ([2] * 12, "top"),
                            ([1, 2, 1], None)):
            out = BS.synthesize(spec, click=click)
            ptf = BS.parse(out)
            ctype: dict = {}
            par: dict = {}

            def _rec(b, p):
                z = b.offset - 7
                ctype[z] = b.content_type
                par[z] = p
                for c in b.child:
                    _rec(c, z)

            for b in ptf.blocks:
                _rec(b, None)
            vol = sum(1 for z, ct in ctype.items()
                      if ct == 0x203b and ctype.get(par[z]) in (0x2015, 0x2589)
                      and out[z:z + len(BS._VIEW_VOLUME)] == BS._VIEW_VOLUME)
            self.assertEqual(vol, 0, msg=f"spec {spec} click={click}: {vol} volume-view blocks left")


_MIDI8 = CONTROL_DIR / "8 MIDI tracks.ptx"


@unittest.skipUnless(_MIDI8.exists(), "need the 8-MIDI control")
class MidiTests(unittest.TestCase):
    """MIDI track synthesis: `synth_midi_unit` (its own block set, no audio lanes) +
    `grow_one_midi_track` + the MIDI index path (`final_index.add_midi_track`). Grow the 8-MIDI
    control to 9 and build the index; every index offset must resolve (no danglers) + reload."""

    def test_midi_grow_and_index(self) -> None:
        d = _load_path(_MIDI8)
        body = d[:FI.final_index_ref(d).start]
        tmpl = BS.extract_midi_unit(d, 2, 8)
        body9 = BS.grow_one_midi_track(body, 8, BS.synth_midi_unit(9, tmpl))
        self.assertEqual(len(BS._by_type(BS.parse(body9), 0x2620)), 9, msg="9 MIDI playlist blocks")
        body9 = BS._fix_name_table_special_last(body9, 9)
        idx = FI.compose_index(d, body9 + d[FI.final_index_ref(d).start:], 8, 9,
                               channels=[0] * 9, track_names=[f"MIDI {i + 1}" for i in range(9)],
                               midi_tracks={9})
        out = BS._set_index_offset(body9 + idx)
        ref = FI.final_index_ref(out)
        z2t, by_type = FI.block_layout(out)
        zmarks = set(z2t)
        recs = FI.parse_records(ref.data, set(by_type), zmarks)
        offs = [c.offset for r in recs for c in r.child_refs] + \
               [o for r in recs for e in r.elements for o in e.offsets]
        self.assertTrue(offs and all(o in zmarks for o in offs), msg="all MIDI index offsets resolve")
        with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as tmp:
            tmp.write(W.encrypt_session_data(out)); path = tmp.name
        try:
            self.assertEqual(PTFFormat().load(path, 48000), 0, msg="9-MIDI reload")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_synth_midi_unit_single_digit_gate(self) -> None:
        tmpl = BS.extract_midi_unit(_load_path(_MIDI8), 2, 8)
        self.assertIn(b"MIDI 7", BS.synth_midi_unit(7, tmpl)["name_entry"])
        with self.assertRaises(ValueError):     # multi-digit MIDI is gated (no >=10-track control)
            BS.synth_midi_unit(10, tmpl)

    def _name_entry_sizes(self, body: bytes) -> list[int]:
        """Per-entry byte sizes of the 0x2519 own-byte name table (start-to-start)."""
        b = BS._by_type(BS.parse(body), 0x2519)[0]
        fc = min(c.offset - 7 for c in b.child)
        region = body[b.offset:fc]
        starts = [m.start() - 4 for m in BS._NAME_RE.finditer(region)]
        return [(starts[i + 1] - starts[i]) if i + 1 < len(starts) else (len(region) - starts[i])
                for i in range(len(starts))]

    def test_midi_name_table_special_is_last(self) -> None:
        """PT-confirmed root cause (2026-06-20): the grow path strands the donor's SPECIAL (short)
        last name entry mid-table; PT reads at the normal stride, misaligns, and runs off the block
        -> "end of stream encountered". `_fix_name_table_special_last` must move the short entry to
        the LAST position. It silently no-ops on MIDI/Click if it hardcodes "Audio ", so assert it
        actually fired (the short entry is last, not stranded) and stayed size-preserving."""
        d = _load_path(_MIDI8)
        body = d[:FI.final_index_ref(d).start]
        body9 = BS.grow_one_midi_track(body, 8, BS.synth_midi_unit(9, BS.extract_midi_unit(d, 2, 8)))
        before = self._name_entry_sizes(body9)
        self.assertEqual(len(before), 9, msg="9 name entries")
        self.assertLess(before.index(min(before)), 8, msg="precondition: short entry is stranded mid-table")
        fixed = BS._fix_name_table_special_last(body9, 9)
        self.assertNotEqual(fixed, body9, msg="fix must fire on MIDI (a no-op re-opens the EOS bug)")
        after = self._name_entry_sizes(fixed)
        self.assertEqual(after.index(min(after)), 8, msg="SPECIAL (short) entry must be LAST")
        self.assertEqual(after[:8], [max(after)] * 8, msg="all but last are full-size")
        self.assertEqual(sum(after), sum(before), msg="size-preserving")


_MTT_FRESH = MTT_DIR / "multiple track types no click fresh.ptx"


@unittest.skipUnless(_MTT_FRESH.exists() and _MIDI8.exists(),
                     "need the multiple-track-types donor + 8-MIDI control")
class MixedMidiTests(unittest.TestCase):
    """Arbitrary mono+stereo+MIDI synthesis via `synthesize_mixed_midi` (the audio+MIDI weave on
    top of `synthesize_mixed_session`). The donor is a `multiple track types` control (internal
    order [mono, MIDI, stereo] = [1, 0, 2]); spec is INTERNAL order. Validates structure that our
    lenient reader would miss — channel multiset, the per-name SPECIAL-last name table, every index
    offset resolving to the right block type (incl. MIDI 0x2620 playlists), 0x2589 == n-1, no path
    chimera — then a reload. PT-confirmed at internal specs [1,0,2,1] / [1,0,2,1,0] (2026-06-20)."""

    SPECS = ([1, 0, 2, 1], [1, 0, 2, 1, 0], [1, 0, 2, 2, 0, 1], [1, 0, 2, 1, 2, 1, 0, 2, 0])

    def setUp(self) -> None:
        self.donor = _load_path(_MTT_FRESH)
        self.tmpl = BS.extract_midi_unit(_load_path(_MIDI8), 2, 8)

    def _build(self, spec):
        return BS.synthesize_mixed_midi(spec, self.donor, self.tmpl)

    def test_donor_internal_order(self) -> None:
        self.assertEqual(BS._internal_channel_order(self.donor), [1, 0, 2])

    def test_structure_and_reload(self) -> None:
        from ptxformatwriter import _mixed_data as _XD
        for spec in self.SPECS:
            with self.subTest(spec=spec):
                out = self._build(spec)
                n = len(spec)
                # channel multiset (display order differs from internal spec)
                self.assertEqual(Counter(t.channels for t in BS.track_types(out)), Counter(spec))
                # name table: per-name full sizes, SPECIAL (-2) on the last entry only
                b = BS._by_type(BS.parse(out), 0x2519)[0]
                fc = min(c.offset - 7 for c in b.child)
                region = out[b.offset:fc]
                ms = list(BS._NAME_RE.finditer(region))
                starts = [m.start() - 4 for m in ms]
                sizes = [(starts[i + 1] - starts[i]) if i + 1 < len(starts) else (len(region) - starts[i])
                         for i in range(len(starts))]
                fulls = [4 + (m.end() - m.start()) + BS._NAME_ENTRY_SUFFIX for m in ms]
                self.assertEqual(len(sizes), n)
                for i, (s, f) in enumerate(zip(sizes, fulls)):
                    self.assertEqual(s, f - 2 if i == n - 1 else f, msg=f"entry {i} size")
                # every index offset is a live ZMARK; MIDI playlists/lanes are referenced
                ref = FI.final_index_ref(out)
                z2t, by_type = FI.block_layout(out)
                zmarks = set(z2t)
                recs = FI.parse_records(ref.data, set(by_type), zmarks)
                offs = [c.offset for r in recs for c in r.child_refs] + \
                       [o for r in recs for e in r.elements for o in e.offsets]
                self.assertTrue(offs and all(o != 0 and o in zmarks for o in offs), msg="offsets resolve")
                cts = Counter(z2t[o] for o in offs)
                self.assertEqual(cts.get(0x2620, 0), spec.count(0), msg="one 0x2620 per MIDI track")
                self.assertGreater(cts.get(0x251a, 0), 0, msg="lanes referenced")
                # overview/counters + no path chimera
                self.assertEqual(len(BS._by_type(BS.parse(out), 0x2589)), n - 1, msg="0x2589 == n-1")
                for tl in (_XD.STEREO_TEMPLATE_LEAF, _XD.MONO_TEMPLATE_LEAF):
                    self.assertFalse(BS.track_name_occurrences(out, tl), msg=f"leaf {tl!r} normalized")
                # reload
                with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as t:
                    t.write(W.encrypt_session_data(out))
                    p = t.name
                try:
                    self.assertEqual(PTFFormat().load(p, 48000), 0, msg=f"reload spec {spec}")
                finally:
                    Path(p).unlink(missing_ok=True)

    def test_misuse_guards(self) -> None:
        with self.assertRaises(ValueError):          # spec[:base_n] must match donor internal order
            self._build([2, 0, 1, 1])
        with self.assertRaises(ValueError):          # grown MIDI must sit at internal position <= 9
            self._build([1, 0, 2] + [1] * 7 + [0])   # MIDI at internal position 11

    def test_inline_path_byte_identical(self) -> None:
        """The no-external-files `synthesize_mixed_midi_inline` (inlined donor + MIDI template) is
        byte-identical to the control-file donor path — so PT-confirmed by transitivity — and the
        top-level `synthesize` routes MIDI specs to it."""
        for spec in self.SPECS:
            with self.subTest(spec=spec):
                ctrl = self._build(spec)
                inline = BS.synthesize_mixed_midi_inline(spec)
                self.assertEqual(inline, ctrl, msg="inline == control-file path")
                self.assertEqual(BS.synthesize(spec), inline, msg="synthesize() routes MIDI to inline")

    def test_synth_next_kind_chain_is_clean(self) -> None:
        """A SYNTHESIS bug, not an editing one: each GROWN MIDI track left its predecessor's
        `next_kind` still tagged audio, so `[1, 0, 2, 0]` emitted `Audio 2 -> MIDI 2` as 0x0000
        instead of 0x0100. Only the donor template's own tracks were correct.

        This is why 10 of the 282 control files fail a chain check and the other 272 pass — the 10
        are exactly our own synth output, and the violation count equals the number of MIDI tracks
        beyond the first (1, 2 and 3 for the sessions with 2, 3 and 4 MIDI tracks).

        A violation is NOT known to be fatal: `mixedmidi_click_{top,middle,bottom}` and
        `mixedmidi_reorder_MIDImiddle` all carry one and are PT-confirmed to OPEN. The point of
        this test is that we should write what Pro Tools writes (3379/3379 consecutive pairs over
        40 real sessions), not that a violation crashes anything.
        """
        KIND = {0x261C: 0, 0x2620: 1, 0x261E: 2, 0x263B: 5, 0x2621: 7, 0x261D: 11}

        def violations(d):
            bl = BS._size_driven_blocks(d)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            end = min((cz for cz, _ce, _cc in bl if z < cz < e), default=e)
            slots = BS._name_entry_ordinal_slots(d, z, end)
            kinds = [next((KIND[cc] for zz, ee, cc in bl
                           if cc in KIND and d[b + 10 : b + 18] in d[zz:ee]), None) for b in slots]
            return [i for i in range(len(slots) - 1)
                    if kinds[i + 1] is not None
                    and int.from_bytes(d[slots[i] + 20 : slots[i] + 22], "little") != (kinds[i + 1] << 8)]

        for spec in ([1, 0, 2], [1, 0, 2, 0], [1, 0, 2, 0, 0], [1, 0, 2, 1, 0]):
            for click in (None, "top", "bottom"):
                with self.subTest(spec=spec, click=click):
                    self.assertEqual(violations(BS.synthesize(spec, click=click)), [],
                                     msg="every entry must name the NEXT entry's real kind")

    def test_click_plus_midi(self) -> None:
        """A click coexists with MIDI — top, bottom, and (via display_order over the N+1 tracks)
        anywhere in between. Every index offset resolves (the click's lane refs AND the MIDI
        playlists — the bug was `_finish_click` rebuilding the index for audio tracks only), and
        the session reloads."""
        def ok(data, expect_display):
            self.assertEqual(self._display_names(data), expect_display)
            ref = FI.final_index_ref(data)
            zt, bt = FI.block_layout(data)
            zmarks = set(zt)
            recs = FI.parse_records(ref.data, set(bt), zmarks)
            offs = [c.offset for r in recs for c in r.child_refs] + \
                   [o for r in recs for e in r.elements for o in e.offsets]
            self.assertTrue(offs and all(o != 0 and o in zmarks for o in offs),
                            msg="all offsets resolve (no zeros/danglers)")
            with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as t:
                t.write(W.encrypt_session_data(data)); p = t.name
            try:
                self.assertEqual(PTFFormat().load(p, 48000), 0, msg="reload")
            finally:
                Path(p).unlink(missing_ok=True)
        ok(BS.synthesize([1, 0, 2, 1, 0], click="bottom"),
           ["Audio 1", "MIDI 1", "Audio 2", "Audio 3", "MIDI 2", "Click 1"])
        ok(BS.synthesize([1, 0, 2, 1, 0], click="top"),
           ["Click 1", "Audio 1", "MIDI 1", "Audio 2", "Audio 3", "MIDI 2"])
        ok(BS.synthesize([1, 0, 2, 1, 0], click="bottom", display_order=[0, 1, 5, 2, 3, 4]),
           ["Audio 1", "MIDI 1", "Click 1", "Audio 2", "Audio 3", "MIDI 2"])

    def _display_names(self, data):
        """Edit-window display order = the 0x2624 container list (what PT reads), as track names."""
        name_at = {}
        for ct in (0x261c, 0x261e, 0x2620):
            for b in BS._by_type(BS.parse(data), ct):
                blk = data[b.offset - 7:b.offset + b.block_size]
                m = BS._NAME_RE.search(blk)
                name_at[b.offset - 7] = m.group().decode() if m else "?"
        return [name_at.get(o, "?") for o in BS._container_display_offsets(data)]

    def test_display_order(self) -> None:
        """`synthesize(spec, display_order=...)` sets the edit-window order independently of build
        order (spec-relative indices), placing MIDI anywhere — including the literal middle of an
        audio run. Index-only (body unchanged); identity is a no-op; reorders reload."""
        spec = [1, 0, 2, 1, 0]  # build: A1(mono), MIDI 1, A2(stereo), A3(mono), MIDI 2
        base = BS.synthesize(spec)
        self.assertEqual(self._display_names(base),
                         ["Audio 1", "MIDI 1", "Audio 2", "Audio 3", "MIDI 2"], msg="build order")
        self.assertEqual(self._display_names(BS.synthesize(spec, display_order=[0, 2, 1, 3, 4])),
                         ["Audio 1", "Audio 2", "MIDI 1", "Audio 3", "MIDI 2"], msg="MIDI to middle")
        self.assertEqual(self._display_names(BS.synthesize(spec, display_order=[4, 3, 2, 1, 0])),
                         ["MIDI 2", "Audio 3", "Audio 2", "MIDI 1", "Audio 1"], msg="reverse")
        # Identity display_order keeps the display order (it normalizes the count==4 instance
        # ordinals to display-position/ground-truth form, which is a byte change but same display).
        ident = BS.synthesize(spec, display_order=[0, 1, 2, 3, 4])
        self.assertEqual(self._display_names(ident), self._display_names(base), msg="identity display")
        body0 = base[:FI.final_index_ref(base).start]
        reordered = BS.synthesize(spec, display_order=[0, 2, 1, 3, 4])
        self.assertEqual(reordered[:FI.final_index_ref(reordered).start], body0, msg="index-only")
        with tempfile.NamedTemporaryFile(suffix=".ptx", delete=False) as t:
            t.write(W.encrypt_session_data(reordered)); p = t.name
        try:
            self.assertEqual(PTFFormat().load(p, 48000), 0, msg="reordered reload")
        finally:
            Path(p).unlink(missing_ok=True)

    def test_display_order_matches_control(self) -> None:
        """A synthesized MIDI-in-middle reorder structurally matches the real PT control of the same
        order (A2 M1 A1): same 0x2624 container type-sequence + same count==4 instance (ordinal,
        childtype) pairs — the instance ordinals skip the MIDI display slot exactly as PT writes."""
        def structure(data):
            type_at = {}
            for ct in (0x261c, 0x261e, 0x2620):
                for b in BS._by_type(BS.parse(data), ct):
                    type_at[b.offset - 7] = ct
            ref = FI.final_index_ref(data)
            zt, bt = FI.block_layout(data)
            cont, insts = [], []
            for r in FI.parse_records(ref.data, set(bt), set(zt)):
                if r.content_type == 0x2624 and r.count == 1 and r.flag == 1:
                    cont = [type_at.get(e.offsets[0]) for e in r.elements[1:] if e.offsets]
                elif r.content_type == 0x2624 and r.count == 4 and len(r.child_refs) >= 2:
                    insts.append((r.ordinal, r.child_refs[0].child_type))
            return cont, sorted(insts)
        mine = BS.synthesize([1, 0, 2], display_order=[2, 1, 0])  # display: stereo, MIDI, mono
        ctrl = _load_path(MTT_DIR / "multiple track types no click A2 M1 A1.ptx")
        self.assertEqual(structure(mine), structure(ctrl), msg="0x2624 structure matches PT control")

    def test_click_midi_waveform_view(self) -> None:
        """The click splice embeds a VOLUME-view block chain inside the click's 0x261e (the
        source control saved the click in volume view), nested too deep for the block parser to
        reach. set_waveform_view scrubs it (raw scan) so no audio track renders in volume view when
        it lands at the click's display row. Assert zero non-automation view-volume blocks remain,
        and that real 0x2580 volume-automation is preserved (not over-converted)."""
        for kw in (dict(click="bottom"), dict(click="top"),
                   dict(click="bottom", display_order=[0, 1, 5, 2, 3, 4])):
            with self.subTest(**kw):
                out = BS.synthesize([1, 0, 2, 1, 0], **kw)
                self.assertEqual(BS._view_volume_offsets(out, FI.final_index_ref(out).start), [],
                                 msg="no non-automation view-volume blocks remain")
                autos = sum(1 for b in BS.flat_blocks(BS.parse(out)) if b.content_type == 0x203b)
                self.assertGreater(autos, 0, msg="real 0x2580 volume-automation preserved")

    def test_clean_per_type_names(self) -> None:
        """Grown tracks get clean per-type counters (no internal-position gaps): the audio tracks
        are named Audio 1..A and the MIDI tracks MIDI 1..M, each a gap-free 1..k run."""
        for spec in self.SPECS:
            with self.subTest(spec=spec):
                names = [t.name for t in BS.track_types(self._build(spec))]
                audio = [int(n.split()[1]) for n in names if n.startswith("Audio ")]
                midi = [int(n.split()[1]) for n in names if n.startswith("MIDI ")]
                self.assertEqual(audio, list(range(1, len(audio) + 1)), msg="Audio 1..A no gaps")
                self.assertEqual(midi, list(range(1, len(midi) + 1)), msg="MIDI 1..M no gaps")


class ContainerCountValidatorTests(unittest.TestCase):
    """`body_synth.validate` replicates Pro Tools' container read to catch a stale child
    count (the 'end of stream' class of failure). remove_track/duplicate_track now rewrite
    those counts, so their output must validate clean."""

    def _base(self):
        d = BS.synthesize_stereo_inline(8)
        return BS.set_track_names(d, ["Kick", "Snare", "Bass", "Gtr", "Keys", "Vox", "Perc", "FX"])

    def test_valid_session_is_clean(self):
        self.assertEqual(BS.validate(self._base()), [])

    def test_track_edits_validate_clean(self):
        b = self._base()
        self.assertEqual(BS.validate(BS.remove_track(b, "Gtr")), [])
        self.assertEqual(BS.validate(BS.duplicate_track(b, "Bass", "Bas2")), [])
        # the FULL per-track block-set round-trips: duplicate then remove is byte-identical
        self.assertEqual(BS.remove_track(BS.duplicate_track(b, "Bass", "Bas2"), "Bas2"), b)

    def test_remove_last_track_is_byte_identical_to_valid(self):
        """DEFINITIVE: removing the LAST track of synth(N+1) yields output byte-identical
        (mod random GUIDs) to synth(N) -- i.e. it IS a real valid session's bytes."""
        from collections import defaultdict
        def norm(b):
            b = bytearray(b); k = 0
            while True:
                k = b.find(b"\x2a\x00\x00\x00", k)
                if k < 0:
                    break
                if k + 12 <= len(b):
                    b[k + 4 : k + 12] = b"\x00" * 8
                k += 1
            return bytes(b)
        def bt(d):
            m = defaultdict(list)
            for z, e, c in BS._size_driven_blocks(d):
                m[c].append(norm(d[z:e]))
            return m
        n = ["Kick", "Snare", "Bass", "Gtr", "Keys", "Vox", "Perc", "FX"]
        base9 = BS.set_track_names(BS.synthesize_stereo_inline(9), n + ["Aux"])
        synth8 = BS.set_track_names(BS.synthesize_stereo_inline(8), n)
        removed = BS.remove_track(base9, "Aux")
        A, B = bt(removed), bt(synth8)
        diffs = [(c, i) for c in set(A) | set(B)
                 for i in range(min(len(A.get(c, [])), len(B.get(c, []))))
                 if c < 0x4b00 and A[c][i] != B[c][i]]
        self.assertEqual(diffs, [])
        # WHOLE FILE (the 0x0002 master index included): the size-walk above EXCLUDES the
        # trailing index, so assert full byte-identity mod GUID here. Guards the index
        # rank-rebuild (drop dead per-track records + re-rank the track-count ordinal +
        # derive the packed-table tag1) that `remove_track` must do beyond offset-shifting.
        self.assertEqual(norm(removed), norm(synth8))

    @staticmethod
    def _norm_guid(b):
        b = bytearray(b); k = 0
        while True:
            k = b.find(b"\x2a\x00\x00\x00", k)
            if k < 0:
                break
            if k + 12 <= len(b):
                b[k + 4 : k + 12] = b"\x00" * 8
            k += 1
        return bytes(b)

    def test_remove_middle_track_index_matches_valid(self):
        """MIDDLE-track removal: the rebuilt 0x0002 master index is byte-identical (mod GUID)
        to synth(N)'s index. The BODY block order differs from a fresh synth (removing a
        middle track leaves survivors in a different physical arrangement), so only the index
        can be asserted equal -- but it MUST be exact, guarding the per-track ordinal re-rank
        (`rebuild_after_track_drop` decrements every lane/playlist instance ordinal above the
        removed position; a last-track removal never exercises this)."""
        norm = self._norm_guid
        names = ["Kick", "Snare", "Bass", "Gtr", "Keys", "Vox", "Perc", "FX",
                 "Aux", "Bus", "Subz", "Mon", "Cue", "Ref", "Alt", "Sum", "Mixx", "Dir", "Sndz", "Rtn"]
        for rmpos in (1, 10, 15):
            full = names[:rmpos] + ["Zed"] + names[rmpos:]
            base = BS.set_track_names(BS.synthesize_stereo_inline(21), full)
            synth = BS.set_track_names(BS.synthesize_stereo_inline(20), names)
            removed = BS.remove_track(base, "Zed")
            rr = FI.final_index_ref(removed)
            rs = FI.final_index_ref(synth)
            self.assertEqual(norm(removed)[rr.start:], norm(synth)[rs.start:],
                             msg=f"index differs for middle removal at pos {rmpos + 1}")

    def test_duplicate_index_is_byte_identical_to_valid(self):
        """`duplicate_track` rebuilds a CANONICAL master index -- the trailing 0x0002 block is
        byte-identical (mod GUID) to a fresh synth(N+1) of the same track layout. Guards
        `final_index.canonicalize_after_track_add` (split the doubled per-track
        elements/instance records into synth's separate body-rank-ordered ones + re-rank the
        ordinals). The BODY positional renumber (below) makes the rest of the file match too;
        this test isolates the INDEX rebuild. The round-trip back to the original is byte-exact."""
        norm = self._norm_guid
        for src in ("Gtr", "Vox"):     # 3-char names, so 'Zed' matches the length constraint
            b8 = self._base()
            dup = BS.duplicate_track(b8, src, "Zed")
            synth9 = BS.set_track_names(BS.synthesize_stereo_inline(9),
                                        [t.name for t in BS.track_types(dup)])
            rd = FI.final_index_ref(dup)
            rs = FI.final_index_ref(synth9)
            self.assertEqual(norm(dup)[rd.start:], norm(synth9)[rs.start:],
                             msg=f"duplicate index of {src!r} != synth(9) index")
            # and the round-trip back to the original is byte-exact
            self.assertEqual(BS.remove_track(dup, "Zed"), b8)

    def test_duplicate_body_matches_valid(self):
        """The BODY positional renumber makes a stereo duplicate byte-identical (mod GUID) to a
        fresh synth(N+1) of the same display layout -- the WHOLE file, not just the index. It
        fixes the copy's (and every downstream track's) position-dependent BODY fields as a
        function of display slot: 0x1014/0x1015 stereo channel indices (the collision that
        crashed Pro Tools on close), the 0x200a/0x200b/0x2015 + 0x2519 + 0x251a display
        ordinals, the 0x2589 overview index (and its 0x2551/0x2587/0x258a/0x258b containers),
        the 0x261b/0x261c/0x2624 element-id pool + k/k-1 counters, the grow-path 0x2104
        view-scale, and the 0x1054 lane order. Verified at N=8 (every insert row) and N=20, and
        the duplicate->remove round-trip stays byte-exact (the renumber is self-inverting)."""
        from collections import defaultdict
        norm = self._norm_guid

        def per_type_diffs(a, b):
            """[(hex(type), rank)] where the two sessions' i-th block of a real type differ."""
            def bt(d):
                m = defaultdict(list)
                for z, e, c in sorted(BS._raw_block_bounds(d, FI.final_index_ref(d).start)):
                    m[c].append(norm(d[z:e]))
                return m
            A, B = bt(a), bt(b)
            out = []
            for c in sorted(set(A) | set(B)):
                if c >= 0x4b00:
                    continue
                la, lb = A.get(c, []), B.get(c, [])
                for i in range(min(len(la), len(lb))):
                    if la[i] != lb[i]:
                        out.append((hex(c), i))
                if len(la) != len(lb):
                    out.append((hex(c), "COUNT"))
            return out

        # N=8: duplicate at several display rows (top / middle / near end).
        base8 = self._base()
        for src in ("Kick", "Bass", "Perc"):          # 4-char names, so 'Bas2' matches the length
            dup = BS.duplicate_track(base8, src, "Bas2")
            layout = [t.name for t in BS.track_types(dup)]
            ref9 = BS.set_track_names(BS.synthesize_stereo_inline(9), layout)
            self.assertEqual(per_type_diffs(dup, ref9), [],
                             msg=f"per-type body diffs for {src!r}")
            self.assertEqual(norm(dup), norm(ref9),
                             msg=f"duplicate BODY of {src!r} != synth(9) whole file")
            self.assertEqual(BS.remove_track(dup, "Bas2"), base8,
                             msg=f"round-trip for {src!r}")
        # N=20: a middle duplicate matches synth(21) whole-file and round-trips.
        names20 = [f"Trk{i:02d}" for i in range(20)]
        b20 = BS.set_track_names(BS.synthesize_stereo_inline(20), names20)
        dup = BS.duplicate_track(b20, "Trk09", "Dpx09")
        ref21 = BS.set_track_names(BS.synthesize_stereo_inline(21),
                                   [t.name for t in BS.track_types(dup)])
        self.assertEqual(norm(dup), norm(ref21), msg="N=20 duplicate BODY != synth(21)")
        self.assertEqual(BS.remove_track(dup, "Dpx09"), b20, msg="N=20 round-trip")

    def test_duplicate_mono_matches_valid(self):
        """MONO generalization: duplicating a track in an all-mono synth is byte-identical (mod
        GUID) to a fresh synth(N+1) of the same display layout -- WHOLE file, index included --
        and the duplicate->remove round-trip is byte-exact. The renumber picks the per-kind
        offsets (mono 0x1014 SITE2 @ +30/u16, tighter 0x261c counters, mono element-id pool)
        purely from each track's channel count, so the same code that keeps all-stereo
        byte-identical also handles mono. Validates against the definitive oracle: equality to a
        fresh `synthesize_mono_inline`."""
        norm = self._norm_guid
        names = ["Audio A", "Audio B", "Audio C", "Audio D",
                 "Audio E", "Audio F", "Audio G", "Audio H"]
        base8 = BS.set_track_names(BS.synthesize_mono_inline(8), names)
        for src in ("Audio A", "Audio C", "Audio G"):          # top / middle / near end
            dup = BS.duplicate_track(base8, src, "Audio Z")
            ref9 = BS.set_track_names(BS.synthesize_mono_inline(9),
                                      [t.name for t in BS.track_types(dup)])
            self.assertEqual(BS.validate(dup), [], msg=f"mono dup {src!r} not eos-clean")
            self.assertEqual(norm(dup), norm(ref9),
                             msg=f"mono duplicate of {src!r} != synth(9) whole file")
            self.assertEqual(BS.remove_track(dup, "Audio Z"), base8,
                             msg=f"mono round-trip for {src!r}")

    def test_duplicate_mixed_matches_valid(self):
        """MIXED generalization: duplicating either a stereo OR a mono track in a mono+stereo
        synth is byte-identical (mod GUID) to a fresh synth(N+1) whose spec has the copy's
        channel count inserted at its display slot -- WHOLE file -- and round-trips byte-exact.
        This exercises the CUMULATIVE channel base (each track's channels start at the running
        sum, not a uniform stride), the two SEPARATE per-kind element-id pools (stereo low, mono
        high) the mixed synth assigns, and the block-END-anchored 0x261c C4 (a variable-length
        folder-leaf sits before it in mixed sessions). Definitive oracle: equality to a fresh
        `synthesize_mixed_inline`."""
        norm = self._norm_guid
        names = ["Trk1", "Trk2", "Trk3", "Trk4", "Trk5", "Trk6", "Trk7", "Trk8"]
        spec = [2, 1, 2, 1, 2, 1, 2, 1]                        # stereo/mono alternating
        base8 = BS.set_track_names(BS.synthesize_mixed_inline(spec), names)
        # (source display slot, source name) -- one stereo, one mono; both in the grown range.
        for slot, src in ((2, "Trk3"), (3, "Trk4")):
            dup = BS.duplicate_track(base8, src, "TrkZ")
            layout = [t.name for t in BS.track_types(dup)]
            refspec = spec[:slot + 1] + [spec[slot]] + spec[slot + 1:]  # copy's kind at its slot
            ref9 = BS.set_track_names(BS.synthesize_mixed_inline(refspec), layout)
            self.assertEqual(BS.validate(dup), [], msg=f"mixed dup {src!r} not eos-clean")
            self.assertEqual([t.channels for t in BS.track_types(dup)], refspec,
                             msg=f"mixed dup {src!r} channel layout")
            self.assertEqual(norm(dup), norm(ref9),
                             msg=f"mixed duplicate of {src!r} != synth(9) whole file")
            self.assertEqual(BS.remove_track(dup, "TrkZ"), base8,
                             msg=f"mixed round-trip for {src!r}")

    def test_track_edits_match_valid_structure(self):
        """A duplicate's per-track block delta equals a real N+1-track session's (real block
        types only; the size-walk's phantom 0x4b48/0x5xxx artifacts are ignored)."""
        from collections import Counter
        real = lambda d: Counter(c for _z, _e, c in BS._size_driven_blocks(d)
                                 if c in {0x1014, 0x1052, 0x261b, 0x261c, 0x2589, 0x210b,
                                          0x2037, 0x2038, 0x203b, 0x2580, 0x251a, 0x2627})
        b8 = self._base()
        b9 = BS.set_track_names(BS.synthesize_stereo_inline(9),
                                [t.name for t in BS.track_types(b8)] + ["Bas2"])
        add = {t: real(b9)[t] - real(b8)[t] for t in set(real(b9)) | set(real(b8))}
        dup = BS.duplicate_track(b8, "Bass", "Bas2")
        got = {t: real(dup)[t] - real(b8)[t] for t in set(real(dup)) | set(real(b8))}
        self.assertEqual(got, add)

    def test_catches_corrupted_container_count(self):
        b = bytearray(self._base())
        for z, _e, c in BS._size_driven_blocks(bytes(b)):
            if c == 0x2624:                                  # bump the total-track count high
                cur = int.from_bytes(b[z + 9 : z + 13], "little")
                b[z + 9 : z + 13] = (cur + 1).to_bytes(4, "little")
                break
        self.assertTrue(any(p["content_type"] == 0x2624 for p in BS.validate(bytes(b))))

    def test_catches_misframed_name_entry(self):
        # the ported EOS walk: a bad name_length misframes the inline name-entry list
        # (the exact 0x2519 fault the pre-fix track edits produced).
        b = bytearray(self._base())
        for z, _e, c in BS._size_driven_blocks(bytes(b)):
            if c == 0x2519:                                  # first entry length @ payload+0x14 (v>=8)
                b[z + 9 + 0x14 : z + 9 + 0x18] = (100000).to_bytes(4, "little")
                break
        self.assertTrue(any(p.get("content_type") == 0x2519 for p in BS.validate(bytes(b))))

    @unittest.skipUnless((ROOT / "control_files" / "various" / "120 to 140bpm.ptx").exists(),
                         "tempo control not present")
    def test_trackless_session_validates_clean(self):
        """A trackless session (a tempo/meter-only control) stores name-entry count 0. The EOS
        name walk must respect that count and read 0 entries -- the old until-first-child walk
        instead misread the following bytes as a bogus name_length and false-positived on 17
        corpus sessions. set_tempo_map on such a session must also validate clean."""
        data = _load_path(ROOT / "control_files" / "various" / "120 to 140bpm.ptx")
        self.assertEqual([t.name for t in BS.track_types(data)], [], msg="expected a trackless control")
        self.assertEqual(BS.validate(data), [], msg="trackless session must validate clean")
        out = BS.set_tempo_map(data, [(90.0, 0), (120.0, 4 * 960000)])
        self.assertEqual(BS.validate(out), [])
        self.assertEqual(BS.tempo_map(out), [(90.0, 0), (120.0, 3840000)])


class NonSynthDuplicateChannelTests(unittest.TestCase):
    """A duplicate on a NOT-synth-shaped session (the path real sessions take) must still give
    the copy free channels: the copy is spliced from the source, so without this it inherits the
    source's channel indices, and two tracks claiming the same channels is what crashes Pro Tools
    on close. A MIDI-bearing session fails `_is_synth_shaped`, so it exercises that path."""

    def _session(self):
        return BS.synthesize_mixed_midi_inline([1, 0, 2, 2, 1, 2])

    def _collisions(self, data):
        seen, clashes = {}, []
        for name, _off, chans in BS._track_1014_rows(data):
            for c in chans:
                if c in seen:
                    clashes.append((c, seen[c], name))
                seen[c] = name
        return clashes

    def test_session_is_not_synth_shaped(self):
        self.assertFalse(BS._is_synth_shaped(self._session()))

    def test_duplicate_gives_copy_free_channels(self):
        for source in ("Audio 2", "Audio 1"):            # stereo source, then mono source
            with self.subTest(source=source):
                dup = BS.duplicate_track(self._session(), source, "Audio X")
                self.assertEqual(self._collisions(dup), [])
                chans = dict((n, c) for n, _o, c in BS._track_1014_rows(dup))
                self.assertNotEqual(chans["Audio X"], chans[source])
                self.assertEqual(BS.validate(dup), [])


class ViewChainAttributionTests(unittest.TestCase):
    """duplicate -> remove(copy) must be a PERFECT INVERSE: the copy's block-set is exactly what
    the duplicate added, so remove must delete exactly that and nothing else. This is the oracle
    that caught remove_track deleting a SURVIVING track's 0x2589 view chain (attributed by zmark
    rank, but those blocks are not in display order) -- and because each 0x2589 nests its
    children, one wrong pick silently dropped ~18 blocks while eos/rc/validate() all passed."""

    def _hist(self, data):
        ref = FI.final_index_ref(data)
        return Counter(c for _z, _e, c in BS._raw_block_bounds(data, ref.start))

    def _non_synth_session(self):
        # A MIDI-bearing session fails _is_synth_shaped -> takes the real-session path.
        # Normalised through `_rekey_offsynth_positional` because our mixed-MIDI SYNTH emits
        # per-track ordinals that do not follow the PT convention (its 0x261C C4 runs
        # [0,1,3,4,5] where every one of 43 discriminating PT-authored sessions uses the
        # audio-only index [0,1,2,3,4]). Without this the fixture is not a faithful stand-in for
        # a real session, and the editors' repair pass would show up as a round-trip difference
        # that has nothing to do with the behaviour under test. Repairing the SYNTH writer itself
        # is the proper fix, but that path is PT-confirmed and is tracked separately.
        return BS._rekey_offsynth_positional(
            BS.synthesize_mixed_midi_inline([1, 0, 2, 2, 1, 2]))

    def test_duplicate_remove_is_a_perfect_inverse_off_the_synth_path(self):
        data = self._non_synth_session()
        self.assertFalse(BS._is_synth_shaped(data))
        dup = BS.duplicate_track(data, "Audio 2", "Audio X")
        back = BS.remove_track(dup, "Audio X")
        h0, h1, h2 = self._hist(data), self._hist(dup), self._hist(back)
        for ct in sorted(set(h0) | set(h1) | set(h2)):
            if ct in (0x229D, 0x23A9, 0x2A00, 0x2C29):     # file-header / 0x5A-in-data artifacts
                continue
            added = h1.get(ct, 0) - h0.get(ct, 0)
            deleted = h1.get(ct, 0) - h2.get(ct, 0)
            self.assertEqual(added, deleted,
                             f"0x{ct:04x}: duplicate added {added} but remove deleted {deleted}")

    def test_round_trip_preserves_every_body_block(self):
        data = self._non_synth_session()
        back = BS.remove_track(BS.duplicate_track(data, "Audio 2", "Audio X"), "Audio X")
        self.assertEqual(len(back), len(data))
        ref = FI.final_index_ref(data)
        for z, e, _c in sorted(BS._raw_block_bounds(data, ref.start)):
            self.assertEqual(data[z:e], back[z:e], f"block at {z} changed across the round-trip")
        self.assertEqual(BS.validate(back), [])

    def test_duplicate_with_a_different_length_name(self):
        """duplicate_track no longer requires new_name to match the source length. A longer OR
        shorter name duplicates under a same-length placeholder and then rename_track absorbs the
        size delta. The copy must exist under the real name, validate clean, and remove_track must
        still invert it byte-exact -- for both a longer and a shorter target name."""
        data = self._non_synth_session()
        src = "Audio 2"                       # 7 bytes
        for new in ("Audio 2 with a much longer name", "A2"):   # longer, then shorter
            self.assertNotEqual(len(new), len(src))
            dup = BS.duplicate_track(data, src, new)
            self.assertIn(new, [t.name for t in BS.track_types(dup)],
                          msg=f"the copy must carry the real name {new!r}")
            self.assertEqual(BS.validate(dup), [])
            self.assertEqual(BS.remove_track(dup, new), data,
                             msg=f"duplicate->remove must invert byte-exact for {new!r}")

    def test_clip_and_region_primitives_across_real_sessions(self):
        """`remove_clip` and the region/audio-file primitives, swept over real PT sessions.

        The track editors had three defects that only a breadth sweep exposed (a shared 0x261B id
        range, a click-only session being uneditable, and a phantom-block write into the file
        header). These primitives had never been swept the same way. Bounded to a sample so the
        suite stays usable -- the full sweep is 46 sessions per operation, all clean.

        `out[:27]` is asserted because the header write found in the track editors was
        data-dependent: it could have hit any caller that patches blocks by type.
        """
        import re as _re
        ours = _re.compile(r"(mixedmidi_|synth_|test_|robust_|midi9)")
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if ours.search(path.name) or checked >= 8:
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            if len(data) > 600_000:
                continue
            try:
                regions = BS.region_list(data)
                lanes = [(z, n, k) for z, n, k in BS.clip_lanes(data) if k > 0]
            except Exception:
                continue
            if not regions or not lanes:
                continue
            with self.subTest(session=path.name):
                z, _n, k = lanes[0]
                for out, what in (
                    (BS.remove_clip(data, z, 0), "remove_clip"),
                    (BS.duplicate_region(data, 0, "ZZ Region"), "duplicate_region"),
                    (BS.set_region_length(data, 0, max(1, BS.region_length(data, 0)) + 4800),
                     "set_region_length"),
                ):
                    self.assertEqual([v for v in BS.validate_pt_invariants(out)
                                      if v["severity"] == "error"], [], msg=what)
                    self.assertEqual(out[:27], data[:27],
                                     msg=f"{what} must never write the file header")
                self.assertEqual(sum(kk for _z, _n2, kk in BS.clip_lanes(BS.remove_clip(data, z, 0))),
                                 sum(kk for _z, _n2, kk in BS.clip_lanes(data)) - 1,
                                 msg="remove_clip must drop exactly one clip")
            checked += 1
        self.assertGreaterEqual(checked, 4, msg=f"expected a sample, got {checked}")

    def test_every_pt_authored_control_survives_duplicate_then_remove(self):
        """The whole PT-authored corpus, swept: duplicate a track and remove it again.

        This sweep is what found the header corruption. `_blocks_by_type` was including the
        file-header index-pointer record at body offset 20 -- framed like a block but not one, its
        "content_type" being the low half of the stored index OFFSET. When that session-specific
        value happened to equal a type a pass patches, the record was written to as a real block of
        that type; sitting at (20, 31), the end-anchored `0x261C` C4 write (`end - 19`) landed on
        byte 12 and zeroed the four header bytes `0101`.

        It hit 2 of 213 controls, and which ones is pure luck -- it depends on the low 16 bits of an
        intermediate index offset -- so it was a latent, data-dependent header corruption for every
        caller. Only a sweep across many real sessions was ever going to surface it.
        """
        import re as _re
        ours = _re.compile(r"(mixedmidi_|synth_|test_|robust_|midi9)")
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if ours.search(path.name):
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            if len(data) > 600_000:          # the 512-track giants: covered by the scale test
                continue
            bl = BS._size_driven_blocks(data)
            nt = [(z, e) for z, e, c in bl if c == 0x2519]
            if not nt:
                continue
            names = [n for n, _p in BS._inline_name_entries(data, nt[0][0], nt[0][1], bl)]
            if len(names) < 2:
                continue
            with self.subTest(session=path.name):
                dup = BS.duplicate_track(data, names[-1], "ZZ Probe")
                self.assertEqual(BS.remove_track(dup, "ZZ Probe"), data,
                                 msg="duplicate->remove must invert byte-exact")
                # Bytes 0..26 only: the u32 at 27 is the stored index offset and MUST change when
                # the body grows. Byte 12 is inside this range -- it is what the phantom-block
                # write zeroed.
                self.assertEqual(data[:27], dup[:27],
                                 msg="the file header must never be written to")
            checked += 1
        # 94 on this checkout: 213 PT-authored files minus the single-track fixtures, the ones
        # with no name table, and the >600 KB giants the scale test covers separately.
        self.assertGreater(checked, 85, msg=f"expected a broad sweep, only {checked} sessions")

    def test_editors_work_at_scale_and_on_click_only_sessions(self):
        """Two things the small fixtures never exercised, both on PT-AUTHORED sessions.

        SCALE: `512 stereo tracks.ptx` is a genuine 512-track / 1.4 MB Pro Tools session. Duplicate
        and remove must hold there, not just on 4-track toys.

        CLICK-ONLY: `128 click tracks.ptx` is a real 128-track session whose tracks are ALL clicks —
        and every edit on it used to be refused with "cannot remove the session's only track",
        because the guard counted `track_types()`, which does not report click/aux tracks and so
        returned 0. Any session whose tracks are all of a kind track_types omits was uneditable.

        That file is also a Pro Tools ORACLE for this project's own fix: its 128 tracks are named
        `Click 1`, `Click 1.dup1` … `Click 1.dup127`, i.e. PT's own Duplicate Track run 127 times,
        and PT allocated their `0x261B` ranges as 24001, 24011, … 25271 — distinct, contiguous,
        stepping exactly 10. Our allocator continues that sequence with 25281.
        """
        for rel, n_expected in (("512 tracks/512 stereo tracks.ptx", 512),
                                ("512 tracks/128 click tracks.ptx", 128)):
            path = ROOT / "control_files" / rel
            if not path.exists():
                continue
            data = _load_path(path)
            bl = BS._size_driven_blocks(data)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            names = [n for n, _p in BS._inline_name_entries(data, z, e, bl)]
            self.assertEqual(len(names), n_expected, msg=f"{rel}: unexpected track count")

            for label, tgt in (("last", names[-1]), ("middle", names[len(names) // 2])):
                with self.subTest(session=rel, case=label):
                    dup = BS.duplicate_track(data, tgt, "ZZ Probe")
                    ranges = [f for _z, f, _l in BS.track_id_ranges(dup)]
                    self.assertEqual(len(ranges), len(set(ranges)),
                                     msg="a duplicate at scale must still get its own id range")
                    self.assertEqual([v for v in BS.validate_pt_invariants(dup)
                                      if v["severity"] == "error"], [])
                    self.assertEqual(BS.remove_track(dup, "ZZ Probe"), data,
                                     msg="duplicate->remove must invert byte-exact at scale")
            BS.remove_track(data, names[-1])      # must not raise on a click-only session

    def test_duplicate_adds_a_track_list_entry_rather_than_consuming_one(self):
        """Duplicating a track must INCREASE the 0x1014 count, not hold it steady.

        `set_track_channels` writes the post-ID channel indices at `marker + 19` / `marker + 23`,
        offsets taken from the control corpus. On real sessions the marker can sit legitimately
        inside the block while those offsets fall PAST its end -- six and ten bytes past, in the
        case that exposed this -- so the channel value `40 00` landed on the NEXT block's
        size/content-type word. That unframed the neighbour: the walk lost it, so adding a track
        left the 0x1014 count unchanged (one gained, one destroyed), and duplicate->remove came back
        two bytes short of byte-exact.

        The count is the honest test. It needs no offsets and no identity mapping -- the two things
        that made this defect take nine failed diagnoses to find, every one of them derailed by
        comparing blocks through an assumed correspondence.
        """
        for rel in ("lots of mono tracks/4 mono tracks.ptx",
                    "multiple track types/multiple track types no click.ptx"):
            path = ROOT / "control_files" / rel
            if not path.exists():
                continue
            data = _load_path(path)
            bl = BS._size_driven_blocks(data)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            names = [n for n, _p in BS._inline_name_entries(data, z, e, bl)]
            before = sum(1 for _z, _e, c in bl if c == 0x1014)
            with self.subTest(session=path.name):
                dup = BS.duplicate_track(data, names[-1], "ZZ Probe")
                after = sum(1 for _z, _e, c in BS._size_driven_blocks(dup) if c == 0x1014)
                self.assertEqual(after, before + 1,
                                 msg="the copy must ADD a 0x1014, not overwrite a neighbour")
                self.assertEqual(BS.remove_track(dup, "ZZ Probe"), data)

    def test_rekey_preserves_the_zero_sentinel(self):
        """Zero means NOT NUMBERED in every ordinal table, and the re-key must never overwrite it.

        This case cannot be sampled locally — across all 213 PT-authored controls there is not a
        single zero ordinal, in `0x2519` or in the lane table — while 40.6% of external-corpus
        entries are zero. Rather than leave the behaviour unverified until that drive returns, the
        case is CONSTRUCTED: zeros are injected into a genuine Pro Tools session and the re-key must
        preserve them while numbering the survivors 1..k within each lane group.

        That reproduces the shape actually measured on real sessions (nonzeros forming ascending
        runs that restart at 1) without needing them present, and the same change is a byte-level
        no-op on all 213 local controls, so it cannot regress anything that IS testable here.
        """
        data = _load_path(ROOT / "control_files" / "lots of mono tracks" / "4 mono tracks.ptx")

        def lane_slots(d):
            out = []
            for z, _e, c in BS._size_driven_blocks(d):
                if c != 0x251A:
                    continue
                nl = int.from_bytes(d[z + 11 : z + 15], "little")
                out.append(z + 15 + nl + 18)
            return out

        def lane_vals(d):
            return [int.from_bytes(d[p : p + 2], "little") for p in lane_slots(d)]

        slots = lane_slots(data)
        self.assertEqual(lane_vals(data), [1, 2, 3, 4, 1, 2, 3, 4],
                         msg="the PT source is lane-major [1..N] per lane group")

        mod = bytearray(data)
        for i in (1, 4):                       # one zero in each lane group
            mod[slots[i] : slots[i] + 2] = b"\x00\x00"
        out = BS._rekey_offsynth_positional(bytes(mod))

        vals = lane_vals(out)
        for i in (1, 4):
            self.assertEqual(vals[i], 0, msg="a zero ordinal must survive the re-key untouched")
        n = len(slots) // 2
        for group in (vals[:n], vals[n:]):
            nz = [v for v in group if v]
            self.assertEqual(nz, list(range(1, len(nz) + 1)),
                             msg=f"survivors must be 1..k within the lane group, got {group}")

    def test_no_edit_path_ever_shares_a_track_id_range(self):
        """The confirmed crash-on-close cause, guarded across EVERY path that clones a track.

        Pro Tools gives each track a contiguous block of 10 session-unique ids in its `0x261B` and
        resolves track IDENTITY through them. A byte-copied subtree inherits the source's range, and
        PT then treats the two tracks as ONE object: it showed the copy under the SOURCE's name
        whatever name we wrote, selected both together, and crashed on close (4/4 duplicates tested
        2026-07-25, against 3/3 clean removals).

        Chained duplicates are the sharp case -- each successive copy must get a range of its own,
        not the one the previous copy just took. `add_click_anyN` is included because it splices the
        click's subtree from a DONOR pair and so carried the donor's ranges, some of them twice; it
        had the identical defect and was only found by auditing for it.
        """
        def coll(d):
            r = [f for _z, f, _l in BS.track_id_ranges(d)]
            return len(r) - len(set(r))

        base = _load_path(ROOT / "control_files" / "lots of mono tracks" / "4 mono tracks.ptx")
        names = [t.name for t in BS.track_types(base)]
        self.assertEqual(coll(base), 0, msg="the PT-authored source must start clean")

        cases = {
            "duplicate last": BS.duplicate_track(base, names[-1], "Audio 5"),
            "duplicate middle": BS.duplicate_track(base, names[1], "Audio 9"),
            "remove last": BS.remove_track(base, names[-1]),
            "remove middle": BS.remove_track(base, names[1]),
        }
        chained = base
        for i, nm in enumerate(("X1", "X2", "X3")):
            chained = BS.duplicate_track(chained, names[0] if i == 0 else f"X{i}", nm)
        cases["three chained duplicates"] = chained
        try:
            from ptxformatwriter import _click_data as _CD
            clean, clk = _CD.click_pair()
            cases["add_click_anyN bottom"] = BS.add_click_anyN(base, clean, clk, at_top=False)
            cases["add_click_anyN top"] = BS.add_click_anyN(base, clean, clk, at_top=True)
        except Exception:                      # click donor not present in this checkout
            pass

        for label, out in cases.items():
            with self.subTest(case=label):
                self.assertEqual(coll(out), 0, msg=f"{label}: two tracks share a 0x261B id range")
                errs = [v for v in BS.validate_pt_invariants(out)
                        if v["severity"] == "error"]
                self.assertEqual(errs, [], msg=f"{label}: {errs}")

    def test_next_kind_chain_survives_duplicate_and_remove(self):
        """`next_kind` is a CHAIN field: entry i's u16 names the KIND of entry i+1, shifted << 8
        (0 audio / 1 MIDI / 2 click / 5 folder-VCA / 7 instrument / 11 master-bus). Verified
        3379/3379 consecutive pairs across 40 real corpus sessions, and on Pro Tools' OWN duplicate
        (corpus pair THE WIND_Vocal Tracking bak.095 -> bak.096, PT writing `Voc 2.dup1`).

        Inserting a copy directly after its source makes the SOURCE's next_kind describe the copy,
        so it must be rewritten to the source's own kind; removing a non-last entry must hand the
        predecessor the removed entry's next_kind. Neither happened, so duplicating `MIDI 1` in a
        mixed session left `MIDI 1 -> MIDI 2` tagged 0x0000 (audio) instead of 0x0100 -- the file
        PT crashed on load. Both directions are asserted here because fixing only the add side
        silently broke the duplicate->remove inverse.
        """
        KIND = {0x261C: 0, 0x2620: 1, 0x261E: 2, 0x263B: 5, 0x2621: 7, 0x261D: 11}

        def violations(d):
            bl = BS._size_driven_blocks(d)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            end = min((cz for cz, _ce, _cc in bl if z < cz < e), default=e)
            ents = BS._inline_name_entries(d, z, e, bl)
            slots = BS._name_entry_ordinal_slots(d, z, end)
            kinds = []
            for b in slots:
                g = d[b + 10 : b + 18]
                kinds.append(next((KIND[cc] for zz, ee, cc in bl
                                   if cc in KIND and g in d[zz:ee]), None))
            out = []
            for i in range(len(slots) - 1):
                if kinds[i + 1] is None:
                    continue
                got = int.from_bytes(d[slots[i] + 20 : slots[i] + 22], "little")
                if got != (kinds[i + 1] << 8):
                    out.append(f"{ents[i][0]}->{ents[i + 1][0]} got 0x{got:04X}")
            return out

        data = self._non_synth_session()
        self.assertEqual(violations(data), [], msg="the control's own chain must be intact")
        for src in [t.name for t in BS.track_types(data)][:3]:
            dup = BS.duplicate_track(data, src, "ZKind")
            self.assertEqual(violations(dup), [],
                             msg=f"duplicating {src!r} must keep the next_kind chain exact")
            self.assertEqual(BS.remove_track(dup, "ZKind"), data,
                             msg=f"and the chain repair must invert byte-exact for {src!r}")

    def test_duplicate_remove_midi_track(self):
        """remove_track/duplicate_track now handle MIDI tracks (no 0x1014/0x1052/0x261B/0x261C;
        the track-list entry is 0x1057 in 0x1058, the per-track record is 0x2620). Identification
        widened the container set to {0x261C, 0x2620, 0x261E} and seeds 0x1057 like 0x1014. The
        duplicate->remove perfect-inverse oracle must hold byte-exact on a MIDI track."""
        data = self._non_synth_session()          # mixed audio + one MIDI track
        midi = next(t.name for t in BS.track_types(data) if t.kind == "midi")
        dup = BS.duplicate_track(data, midi, "MZ")
        self.assertIn("MZ", [t.name for t in BS.track_types(dup)])
        self.assertEqual(len(BS.track_types(dup)), len(BS.track_types(data)) + 1)
        self.assertEqual(BS.validate(dup), [])
        self.assertEqual(BS.remove_track(dup, "MZ"), data,
                         msg="MIDI duplicate->remove must invert byte-exact")

    @unittest.skipUnless((ROOT / "control_files" / "midi9.ptx").exists(),
                         "midi9 control not present")
    def test_duplicate_remove_pure_midi_control(self):
        """The pure-MIDI control (9 MIDI tracks, ZERO audio) is the decisive MIDI oracle: no
        0x1014 anywhere, so the audio GUID-seed is entirely absent."""
        data = _load_path(ROOT / "control_files" / "midi9.ptx")
        self.assertTrue(all(t.kind == "midi" for t in BS.track_types(data)))
        dup = BS.duplicate_track(data, "MIDI 1", "MIDI Z")
        self.assertEqual(BS.validate(dup), [])
        self.assertEqual(BS.remove_track(dup, "MIDI Z"), data)

    @unittest.skipUnless(
        (ROOT / "control_files" / "various" / "two midi tracks one note each.ptx").exists(),
        "MIDI-clip control not present")
    def test_midi_edit_leaves_audio_count_0x1015_unchanged(self):
        """REGRESSION (bug #17): 0x1015 counts AUDIO tracks (its 0x1014 entries), NOT all tracks
        -- verified on 166 PT sessions. Adding/removing a MIDI track must leave it unchanged. The
        round-trip HID this (dup +1, remove -1 nets to zero) while dup produced a PT-wrong count;
        this asserts the intermediate is correct on a PT-authored 0-audio session (0x1015 == 0)."""
        data = _load_path(ROOT / "control_files" / "various" / "two midi tracks one note each.ptx")

        def audio_count(d):
            for z, _e, c in BS._size_driven_blocks(d):
                if c == 0x1015:
                    return int.from_bytes(d[z + 9 : z + 13], "little")
            return None

        self.assertEqual(len([t for t in BS.track_types(data) if t.kind in ("mono", "stereo")]), 0)
        self.assertEqual(audio_count(data), 0)
        dup = BS.duplicate_track(data, "MIDI 1", "MZ")
        self.assertEqual(audio_count(dup), 0, msg="a MIDI copy must NOT bump the audio-count 0x1015")
        self.assertEqual(BS.validate(dup), [])
        self.assertEqual(audio_count(BS.remove_track(data, "MIDI 2")), 0,
                         msg="removing a MIDI track must NOT change the audio-count 0x1015")

    @unittest.skipUnless(
        (ROOT / "control_files" / "various" / "two midi tracks one note each.ptx").exists(),
        "MIDI-clip control not present")
    def test_midi_dup_bumps_the_midi_list_count_0x1058(self):
        """REGRESSION (bug #18): 0x1058 is the MIDI track-list count (== its 0x1057 entries;
        PT-verified 1 MIDI -> 1, 2 MIDI -> 2). A MIDI copy must bump it so the count stays
        consistent with the entries -- the round-trip HID a stale count (dup left it while adding a
        0x1057). Verified on a PT-authored session where 0x1058 was consistent to begin with."""
        data = _load_path(ROOT / "control_files" / "various" / "two midi tracks one note each.ptx")

        def midi_list(d):
            bl = BS._size_driven_blocks(d)
            for z, e, c in bl:
                if c == 0x1058 and any(z < zz < e and cc == 0x1057 for zz, _ee, cc in bl):
                    stored = int.from_bytes(d[z + 9 : z + 13], "little")
                    entries = sum(1 for zz, _ee, cc in bl if z < zz < e and cc == 0x1057)
                    return stored, entries
            return None

        self.assertEqual(midi_list(data), (2, 2), msg="PT control: 0x1058 = 2 for 2 MIDI tracks")
        dup = BS.duplicate_track(data, "MIDI 1", "MZ")
        stored, entries = midi_list(dup)
        self.assertEqual(entries, 3, msg="dup added a 0x1057 MIDI entry")
        self.assertEqual(stored, 3, msg="0x1058 count must track its 0x1057 entries after the dup")
        self.assertEqual(BS.remove_track(dup, "MZ"), data, msg="round-trip stays byte-exact")

    @unittest.skipUnless((CONTROL_DIR.parent / "3 stereo plus click.ptx").exists()
                         or list(ROOT.glob("control_files/**/3 stereo plus click.ptx")),
                         "click control not present")
    def test_non_synth_duplicate_gives_the_copy_a_view_chain(self):
        """REGRESSION (bug #19): every PT session satisfies `0x2589 ordinals == range(name
        entries)` -- one view chain per display row (162/162 corpus sessions). The off-synth
        duplicate used to SKIP the chain (it attributed against `track_types`, which omits the
        click, so the 1:1 check failed), leaving N+1 name entries with only N chains and silently
        violating that invariant. The byte-exact round-trip hid it because remove skipped
        symmetrically -- the same "check the intermediate" lesson as #17/#18.

        A click-bearing control is the discriminating fixture: 4 name entries but only 3
        `track_types`, which is exactly what defeated the old basis. Attribution is now against the
        NAME-TABLE entries, and the copy is restamped to display row R+1 with the rows below it
        shifted down."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/3 stereo plus click.ptx"))))

        def chains(d):
            bl = BS._size_driven_blocks(d)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            nents = len(BS._inline_name_entries(d, z, e, bl))
            ords = sorted(int.from_bytes(d[zz + 9 : zz + 11], "little")
                          for zz, _ee, cc in bl if cc == 0x2589)
            return nents, ords

        n0, o0 = chains(data)
        self.assertGreater(n0, len(BS.track_types(data)),
                           msg="the click gives more name entries than track_types rows -- the "
                               "case the old track_types basis could not attribute")
        self.assertEqual(o0, list(range(n0)), msg="the control satisfies the PT invariant")
        dup = BS.duplicate_track(data, "Audio 2", "AZZZZZZ")
        n1, o1 = chains(dup)
        self.assertEqual(n1, n0 + 1)
        self.assertEqual(len(o1), n1, msg="the copy must get its own 0x2589 view chain")
        self.assertEqual(o1, list(range(n1)),
                         msg="chain ordinals must stay == range(name entries) after the dup")
        self.assertEqual(BS.validate(dup), [])
        self.assertEqual(BS.remove_track(dup, "AZZZZZZ"), data,
                         msg="remove must invert the chain copy + ordinal shift byte-exact")

    @unittest.skipUnless(list(ROOT.glob("control_files/**/3 stereo plus click.ptx")),
                         "click control not present")
    def test_duplicate_the_last_name_entry(self):
        """The LAST 0x2519 entry uses the SHORT 21-byte trailer because its `next_kind` u16 is
        absent -- there is no next entry. Duplicating it used to raise; it now mirrors
        remove_track's last-entry truncation: the source is PROMOTED to the 23-byte form carrying
        `kind(copy) << 8`, and the copy becomes the new last in the short form with ordinal N+1.

        The click is always the last entry, so this is also what unblocked duplicating a click
        (0x261E) track."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/3 stereo plus click.ptx"))))

        def entries(d):
            bl = BS._size_driven_blocks(d)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            ents = BS._inline_name_entries(d, z, e, bl)
            kids = [zz for zz, _e, _c in bl if z < zz < e]
            region_end = min(kids) if kids else e
            out = []
            for nm, npos in ents:
                full = npos - 4 + 4 + len(nm) + 23
                out.append((nm, min(full, region_end) - (npos - 4)))
            return out, region_end

        before, _ = entries(data)
        self.assertEqual(before[-1][0], "Click 1", msg="the click is the last entry")
        self.assertEqual(before[-1][1], 4 + len("Click 1") + 21, msg="...in the SHORT form")

        dup = BS.duplicate_track(data, "Click 1", "ZZZZZZZ")
        after, _ = entries(dup)
        self.assertEqual([n for n, _s in after],
                         ["Audio 1", "Audio 2", "Audio 3", "Click 1", "ZZZZZZZ"])
        # the source was promoted to the FULL form, the copy is the new SHORT last
        self.assertEqual(after[-2][1], 4 + len("Click 1") + 23,
                         msg="the old last entry must be promoted to the 23-byte form")
        self.assertEqual(after[-1][1], 4 + len("ZZZZZZZ") + 21,
                         msg="the copy becomes the new last, in the short form")
        self.assertEqual(BS.validate(dup), [])
        self.assertEqual(BS.remove_track(dup, "ZZZZZZZ"), data,
                         msg="remove must invert the promotion byte-exact")

    def test_copy_is_not_orphaned_out_of_its_container(self):
        """REGRESSION (bug #22, the PT CRASH-ON-LOAD cause). A copy unit is spliced at
        `at == end_of_source_unit`. When the source is the LAST child of an ancestor, that
        ancestor's declared end EQUALS the insertion offset, so the old strict `z < at < e`
        growth test skipped it: the container kept its old size and the copy landed just past
        its declared end -- a block ORPHANED out of its parent.

        Pro Tools verdict (2026-07-23): every duplicate whose copied 0x2589 escaped 0x258A/0x258B
        this way CRASHED ON LOAD, or failed with "unexpected stream type ... Zoom Memories"; every
        duplicate without the orphan at least loaded. `two midi tracks no clips` is the minimal
        reproduction -- MIDI 1's view chain is the LAST child of 0x258A, so duplicating it hits
        the boundary exactly."""
        data = _load_path(next(iter(ROOT.glob(
            "control_files/**/two midi tracks no clips.ptx"))))
        dup = BS.duplicate_track(data, "MIDI 1", "MIDI 3")
        bl = BS._size_driven_blocks(dup)
        for child, parent in ((0x2589, 0x258A), (0x1057, 0x1058), (0x210B, 0x2107),
                              (0x2620, 0x2624)):
            spans = [(z, e) for z, e, c in bl if c == parent]
            loose = [z for z, e, c in bl if c == child
                     and not any(pz < z and e <= pe for pz, pe in spans)]
            self.assertEqual(loose, [], msg=f"0x{child:04x} orphaned out of 0x{parent:04x}")
        self.assertEqual(BS.validate(dup), [])
        self.assertEqual(BS.remove_track(dup, "MIDI 3"),
                         BS._rekey_offsynth_positional(data))

    def test_duplicate_gives_the_copy_its_own_0x261c_counter(self):
        """REGRESSION (bug #23): the 0x261C per-track "C4" counter (block-END anchored, so its
        offset is kind-independent) is the track's AUDIO-ONLY index -- measured on 43
        discriminating PT-authored sessions with ZERO exceptions, e.g. `multiple track types`
        (Click 1, MIDI 1, Audio 1, Audio 2) stores [0, 1], NOT the global display slots [2, 3].

        A duplicate cloned it verbatim, so two tracks carried the same value ([0,1,2,2] for four
        audio tracks).

        CAUSAL CLAIM CORRECTED (2026-07-25): this docstring used to say that was "why duplicates
        showed the copy under the SOURCE's name". It was not. Six files whose C4 counters were
        already correct STILL displayed the copy under the source's name in Pro Tools and still
        crashed on close; the real identity field is the `0x261B` track-id range (see
        `test_no_edit_path_ever_shares_a_track_id_range` and the spec). Keeping C4 distinct per
        track is right for every session that uses this convention -- and this test still pins it --
        but it explains none of the observed crashes.

        NOTE for anyone tempted to make this pass conditional: `c4 == 0..k-1` holds in 0 of 149
        real external sessions, so the writer does impose our convention on sessions that do not
        use it. Skipping the renumber when the session "looks non-conforming" was tried and
        REVERTED: the check runs after the copy is spliced in, so it sees [0,1,2,3,3], calls the
        session non-conforming, and skips the very renumber the copy needs. Every predicate tried
        mis-classified either the post-duplicate or the post-remove shape. Doing this properly needs
        the meaning of the field at `end-19` in a real session, which is still UNPROVEN."""
        data = _load_path(next(iter(ROOT.glob(
            "control_files/**/multiple track types no click.ptx"))))

        def c4(d):
            return [int.from_bytes(d[e - BS._B261C_C4_FROM_END : e - BS._B261C_C4_FROM_END + 4],
                                   "little")
                    for z, e in BS._blocks_by_type(d).get(0x261C, [])]

        base = c4(data)
        self.assertEqual(base, list(range(len(base))), msg="PT control uses the audio-only index")
        dup = BS.duplicate_track(data, "Audio 1", "Audio 3")
        got = c4(dup)
        self.assertEqual(len(got), len(base) + 1)
        self.assertEqual(len(set(got)), len(got), msg="every track needs its own C4 counter")
        self.assertEqual(got, list(range(len(got))))
        self.assertEqual(BS.validate(dup), [])

    @unittest.skipUnless(
        (pathlib.Path("/Users/jonkubis/Desktop/PTX_VERIFY_2026-07-23")).exists(),
        "the PT-verdict corpus is not present on this machine")
    def test_pt_invariant_validator_matches_real_pro_tools_verdicts(self):
        """`validate_pt_invariants` is CALIBRATED against 12 files a human ran through Pro Tools
        on 2026-07-23: 8 were rejected (3 crash-on-load, 4 duplicate-names-then-crash-on-close, 1
        "Zoom Memories" stream error) and 4 were accepted. It must flag an error on every rejected
        file and none on any accepted one.

        The severity split exists because of file 12: PT ACCEPTED it with 0x2519 ordinals [1,3,4]
        (a gap left by removing a middle track). Density is therefore a normalisation PT-authored
        files happen to have, NOT a requirement -- reporting it as an error would reject a file PT
        demonstrably opens and closes cleanly. UNIQUENESS is the hard rule."""
        root = pathlib.Path("/Users/jonkubis/Desktop/PTX_VERIFY_2026-07-23")
        verdicts = {
            "A_no_audio_needed/01_MIDI_duplicate_track.ptx": False,
            "A_no_audio_needed/02_MIDI_remove_track.ptx": True,
            "A_no_audio_needed/03_MIDI_duplicate_last_entry.ptx": False,
            "A_no_audio_needed/04_MIDI_single_track_duplicate.ptx": False,
            "A_no_audio_needed/05_audio_duplicate_last_entry.ptx": False,
            "A_no_audio_needed/06_mixed_duplicate_audio.ptx": False,
            "A_no_audio_needed/07_mixed_duplicate_MIDI.ptx": False,
            "A_no_audio_needed/08_mixed_remove_audio.ptx": True,
            "B_click_session_with_audio/09_click_remove.ptx": True,
            "B_click_session_with_audio/10_click_duplicate.ptx": False,
            "B_click_session_with_audio/11_audio_dup_in_click_session.ptx": False,
            "B_click_session_with_audio/12_audio_remove_in_click_session.ptx": True,
        }
        for rel, pt_accepted in verdicts.items():
            path = root / rel
            if not path.exists():
                continue
            errs = [v for v in BS.validate_pt_invariants(_load_path(path))
                    if v["severity"] == "error"]
            if pt_accepted:
                self.assertEqual(errs, [], msg=f"{rel}: PT ACCEPTED this — must not error")
            else:
                self.assertNotEqual(errs, [], msg=f"{rel}: PT REJECTED this — must be flagged")

    def test_pt_invariant_validator_has_no_false_positives(self):
        """Negative controls: sessions Pro Tools is KNOWN to open must raise no error.

        These caught two over-strict rules. A first version treated repeated 0x2589 display rows
        and repeated per-track subtree ordinals as errors -- true of every PT-AUTHORED session, but
        our own `mixedmidi_click_*` and `mixedmidi_reorder_*` carry duplicates AND were confirmed
        to open in Pro Tools (2026-06-20). Dropping both rules still catches 8/8 of the known
        rejections, so they were pure false-positive generators and are now "nonconforming"."""
        for frag in ("mixedmidi_click_bottom", "mixedmidi_click_top", "mixedmidi_click_middle",
                     "mixedmidi_reorder_MIDImiddle", "mixedmidi_reorder_SMm"):
            hits = list(ROOT.glob(f"control_files/**/{frag}.ptx"))
            if not hits:
                continue
            errs = [v for v in BS.validate_pt_invariants(_load_path(hits[0]))
                    if v["severity"] == "error"]
            # `track-id-uniqueness` is deliberately NOT exempted away here. These fixtures were
            # confirmed to OPEN (2026-06-20) — they were never confirmed to CLOSE, and a shared
            # track-id range is a close-time defect: on 2026-07-25 all 4 duplicates carrying one
            # crashed Pro Tools on close while all 3 collision-free removals were clean. Several of
            # these click sessions do carry a collision, because our click path clones a donor
            # track and inherited its ids. So the rule firing on them is a TRUE positive about a
            # real defect, not the over-strictness this test exists to catch; what the test still
            # guards is that NOTHING ELSE fires.
            other = [v for v in errs if v["invariant"] != "track-id-uniqueness"]
            self.assertEqual(other, [], msg=f"{frag}: PT-confirmed to open — must not error")

    def test_pt_invariant_validator_is_clean_on_the_whole_corpus(self):
        """Every PT-AUTHORED session in the corpus must be error-free -- they ARE the ground truth
        the invariants were measured from, so any error is by definition a false positive."""
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            name = path.name
            if name.startswith(("test_", "synth_", "SYNTH", "mixedmidi", "robust", "midi9")):
                continue
            if ".bak." in name:
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            errs = [v for v in BS.validate_pt_invariants(data) if v["severity"] == "error"]
            self.assertEqual(errs, [], msg=f"{name}: PT-authored — must not error")
            checked += 1
        self.assertGreater(checked, 100, msg="expected a substantial PT-authored corpus")

    def test_every_editing_primitive_produces_pt_clean_output(self):
        """REGRESSION HARNESS. Every track/clip edit must leave the session free of
        error-severity PT-invariant violations. This is the check that bugs #20-#23 all evaded:
        each of them passed `validate()`, a clean reload and a byte-exact round-trip, and Pro Tools
        rejected the file anyway. Running the calibrated gate over the editors' OUTPUT is what
        would have caught them automatically.

        Sampled across the corpus rather than exhaustive, so it stays fast enough to keep."""
        def err(d):
            return [v for v in BS.validate_pt_invariants(d) if v["severity"] == "error"]

        checked = collections.Counter()
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if ".bak." in path.name:
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            if err(data):
                continue                      # only start from sessions PT would accept
            tracks = BS.track_types(data)
            if tracks and checked["track"] < 12:
                src = tracks[0].name
                new = "Z" * len(src)
                while new in [t.name for t in tracks]:
                    new = "Y" + new[1:]
                try:
                    dup = BS.duplicate_track(data, src, new)
                except ValueError:
                    dup = None
                if dup is not None:
                    self.assertEqual(err(dup), [], msg=f"duplicate_track({src}) on {path.name}")
                    self.assertEqual(err(BS.remove_track(dup, new)), [],
                                     msg=f"remove_track on {path.name}")
                    checked["track"] += 1
            lanes = [l for l in BS.clip_lanes(data) if l[2] > 0]
            if lanes and checked["clip"] < 12:
                z = lanes[0][0]
                self.assertEqual(err(BS.add_clip(data, z, position=1_000_000)), [],
                                 msg=f"add_clip on {path.name}")
                self.assertEqual(err(BS.remove_clip(data, z, 0)), [],
                                 msg=f"remove_clip on {path.name}")
                self.assertEqual(err(BS.move_clip(data, z, 0, 2_000_000)), [],
                                 msg=f"move_clip on {path.name}")
                checked["clip"] += 1
            if checked["track"] >= 12 and checked["clip"] >= 12:
                break
        self.assertGreaterEqual(checked["track"], 5, msg="expected several track-edit samples")
        self.assertGreaterEqual(checked["clip"], 5, msg="expected several clip-edit samples")

    @unittest.skipUnless(list(ROOT.glob("control_files/**/one clip bar 1.ptx")),
                         "clip control not present")
    def test_duplicate_region_and_place_a_clip_referencing_it(self):
        """NEW CAPABILITY: `duplicate_region` appends a copy of a region under a new name, and
        `add_clip(region_index=...)` can then place a clip that REFERENCES it.

        This is unlocked by the 0x2628 layout being pinned: the record's variable section is
        delimited by a constant 12-byte tail with a fixed 27-byte trailer (2774/2774 corpus
        records), so `span == 57 + namelen + varlen` and duplicating a region only requires
        rewriting the NAME -- no field semantics. Neither 0x2629 nor 0x2628 is a master-index
        target (only the 0x262A list is), so no index entries are added."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/one clip bar 1.ptx"))))
        before = BS.region_list(data)
        self.assertGreater(len(before), 0)

        step1 = BS.duplicate_region(data, 0, "NEWRGN")
        after = BS.region_list(step1)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[-1][1], "NEWRGN")
        self.assertEqual([n for _z, n in after[:-1]], [n for _z, n in before],
                         msg="existing regions must be untouched")
        self.assertEqual(BS.validate(step1), [])
        self.assertEqual([v for v in BS.validate_pt_invariants(step1)
                          if v["severity"] == "error"], [])

        lane = [l for l in BS.clip_lanes(step1) if l[2] > 0][0][0]
        step2 = BS.add_clip(step1, lane, position=3_000_000, region_index=len(after) - 1)
        self.assertIn("NEWRGN", BS.clip_names(step2, lane),
                      msg="the placed clip must reference the NEW region")
        self.assertEqual(BS.validate(step2), [])
        self.assertEqual([v for v in BS.validate_pt_invariants(step2)
                          if v["severity"] == "error"], [])

    @unittest.skipUnless(list(ROOT.glob("control_files/**/one clip bar 1.ptx")),
                         "clip control not present")
    def test_register_audio_file_then_region_then_clip(self):
        """NEW CAPABILITY: the three-step chain an in-place editor needs to introduce audio --
        `duplicate_audio_file` (0x103A filename entry + its 1:1 0x1003 record),
        `duplicate_region` (0x2629/0x2628), then `add_clip(region_index=...)`.

        None of 0x103A / 0x1003 / 0x2629 / 0x2628 is a master-index target, so every step is a
        plain splice plus an offset slide -- no index entries.

        SCOPE: the 0x1003 body is copied verbatim, so the new registration describes the SAME
        audio as the source. Pointing it at genuinely different audio additionally requires
        rewriting the identity fields inside 0x1003. PT has NOT verified this chain."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/one clip bar 1.ptx"))))
        files0, regions0 = BS._audio_filenames(data), BS.region_list(data)

        s1 = BS.duplicate_audio_file(data, 0, "IMPORTED.wav")
        self.assertEqual(BS._audio_filenames(s1), files0 + ["IMPORTED.wav"])

        s2 = BS.duplicate_region(s1, 0, "IMPORTED")
        self.assertEqual([n for _z, n in BS.region_list(s2)],
                         [n for _z, n in regions0] + ["IMPORTED"])

        lane = [l for l in BS.clip_lanes(s2) if l[2] > 0][0][0]
        s3 = BS.add_clip(s2, lane, position=4_000_000,
                         region_index=len(BS.region_list(s2)) - 1)
        self.assertIn("IMPORTED", BS.clip_names(s3, lane))

        for stage, d in (("file", s1), ("region", s2), ("clip", s3)):
            self.assertEqual(BS.validate(d), [], msg=f"validate after {stage}")
            self.assertEqual([v for v in BS.validate_pt_invariants(d)
                              if v["severity"] == "error"], [], msg=f"PT gate after {stage}")

    def test_retarget_audio_file_writes_the_wav_identity(self):
        """NEW CAPABILITY: `retarget_audio_file` points one registered 0x1003 record at a
        different WAV, which is what turns `duplicate_audio_file` (a verbatim copy, describing the
        SAME audio) into genuine new-audio registration.

        Writes the identity fields `set_clip_wav` already established for the synthesis path --
        sample count @+15, umid @+44 and @+292, id2 @+301 -- to ONE record instead of all of them.
        All fixed-width, so the edit is size-neutral."""
        sessions = list(ROOT.glob("control_files/**/one clip bar 1.ptx"))
        wavs = [w for w in sorted(ROOT.glob("control_files/**/Audio Files/*.wav"))]
        target = None
        for w in wavs:
            try:
                BS.wav_clip_identity(w)
                target = w
                break
            except Exception:
                continue
        if not sessions or target is None:
            self.skipTest("no session + PT/BWF wav available")

        def identities(d):
            return [(int.from_bytes(d[z + 15 : z + 19], "little"), bytes(d[z + 44 : z + 52]))
                    for z, e in sorted((z, e) for z, e, c in BS._size_driven_blocks(d)
                                       if c == 0x1003)]

        data = _load_path(sessions[0])
        dup = BS.duplicate_audio_file(data, 0, target.name)
        self.assertEqual(identities(dup)[0], identities(dup)[1],
                         msg="the copy starts out describing the SAME audio")

        out = BS.retarget_audio_file(dup, 1, target)
        self.assertEqual(len(out), len(dup), msg="retarget must be size-neutral")
        want_count, want_umid, _id2 = BS.wav_clip_identity(target)
        self.assertEqual(identities(out)[1], (want_count, want_umid),
                         msg="the retargeted record must carry the WAV's identity")
        self.assertEqual(identities(out)[0], identities(data)[0],
                         msg="the original registration must be untouched")
        self.assertEqual(BS.validate(out), [])
        self.assertEqual([v for v in BS.validate_pt_invariants(out)
                          if v["severity"] == "error"], [])

    @unittest.skipUnless(list(ROOT.glob("control_files/**/one clip bar 1.ptx")),
                         "clip control not present")
    def test_region_length_is_the_wav_sample_count(self):
        """The FIRST field of a 0x2628 variable section is the region's length in samples --
        verified against the source WAVs on every corpus region whose file is on disk (40/40 equal
        to `wav_clip_identity(wav)[0]`). This is sharper than the spec's earlier "final field ==
        source-file length", which was refuted as universal.

        `set_region_length` is the piece `retarget_audio_file` deliberately leaves alone: pointing
        a registration at a WAV of a different length leaves the region describing the old one."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/one clip bar 1.ptx"))))
        n = len(BS.region_list(data))
        before = [BS.region_length(data, i) for i in range(n)]
        self.assertTrue(all(v is not None for v in before))

        out = BS.set_region_length(data, 0, 4321)
        after = [BS.region_length(out, i) for i in range(n)]
        self.assertEqual(after[0], 4321)
        self.assertEqual(after[1:], before[1:], msg="other regions must be untouched")
        self.assertEqual(len(out), len(data), msg="must be size-neutral")
        self.assertEqual(BS.validate(out), [])
        self.assertEqual([v for v in BS.validate_pt_invariants(out)
                          if v["severity"] == "error"], [])
        self.assertEqual(BS.set_region_length(out, 0, before[0]), data,
                         msg="setting it back must be byte-exact")

        # A value too wide for the declared field is WIDENED (see
        # test_set_region_length_widens_the_descriptor_field), never silently truncated. The only
        # hard stop left is a nibble's own ceiling: a slot cannot declare more than 15 bytes.
        self.assertEqual(BS.region_length(BS.set_region_length(data, 0, 10_000_000), 0),
                         10_000_000)
        with self.assertRaises(ValueError):
            BS.set_region_length(data, 0, 1 << 130)
        with self.assertRaises(ValueError):
            BS.set_region_length(data, 0, -1)

    @unittest.skipUnless(list(ROOT.glob("control_files/**/one clip bar 1.ptx")),
                         "clip control not present")
    def test_set_region_length_widens_the_descriptor_field(self):
        """The 0x2628 length field is nibble-width-declared (2 bytes in ~94% of records), so a
        count above 65535 does not fit. Widening rewrites the descriptor nibble AND resizes the
        block, moving every enclosing size and index offset -- the last structural piece of the
        0x2628 model.

        Note the block-type multiset DOES change by one at body offset 20: that is the file-header
        index-pointer phantom, whose apparent content type is the low half of the final-index
        offset, so it shifts when the body grows. Total block count is unchanged."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/one clip bar 1.ptx"))))
        n = len(BS.region_list(data))
        others = [BS.region_length(data, i) for i in range(1, n)]

        for target, grew in ((4321, 0), (70_000, 1), (5_000_000, 1)):
            out = BS.set_region_length(data, 0, target)
            self.assertEqual(BS.region_length(out, 0), target)
            self.assertEqual([BS.region_length(out, i) for i in range(1, n)], others,
                             msg="other regions must be untouched")
            self.assertEqual(len(out) - len(data), grew,
                             msg=f"{target} should grow the body by {grew}")
            self.assertEqual(BS.validate(out), [])
            self.assertEqual([v for v in BS.validate_pt_invariants(out)
                              if v["severity"] == "error"], [])
            self.assertEqual(len(BS._size_driven_blocks(out)),
                             len(BS._size_driven_blocks(data)),
                             msg="widening must not add or lose blocks")
            # the proven layout formula must still hold for the widened record
            z, nl = BS._region_2628(out, 0)
            end = [e for zz, e, _c in BS._size_driven_blocks(out) if zz == z][0]
            tail = b"\xfe\xff\x00\x00\x00\x00\xff\xff\x04\x00\x04\x00"
            k = out.find(tail, z + 13 + nl, end)
            self.assertGreater(k, 0)
            self.assertEqual(end - z, 57 + nl + (k - (z + 13 + nl + 5)),
                             msg="span == 57 + namelen + varlen must survive widening")

    def test_synthesis_paths_are_pt_invariant_clean(self):
        """The SYNTHESIS paths must satisfy the same PT invariants the in-place editors do.

        Worth pinning because they are a separate writer: sweeping `generated/` found 29 of 413
        historical outputs carrying error-severity violations (0x2589 chain counts, 0x261C C4
        counters, 0x251A lane ordinals). Every LIVE path is clean, so those are artifacts of older
        code -- this test keeps it that way.

        As of bugs #26-#28 the mixed-MIDI paths are also free of nonconforming NOTES: the kind
        track counts, the 0x2589 chain permutation and the 0x261C C4 audio index are all repaired
        at the end of `synthesize_mixed_midi`. This test asserts BOTH severities for every path,
        so a regression in either is caught."""
        def errors(d):
            return [v for v in BS.validate_pt_invariants(d) if v["severity"] == "error"]

        for label, build in (
            ("stereo_inline(8)", lambda: BS.synthesize_stereo_inline(8)),
            ("stereo_inline(12)", lambda: BS.synthesize_stereo_inline(12)),
            ("mono_inline(8)", lambda: BS.synthesize_mono_inline(8)),
            ("mixed_midi([1,0,2])", lambda: BS.synthesize_mixed_midi_inline([1, 0, 2])),
            ("mixed_midi([1,0,2,2,1,2])",
             lambda: BS.synthesize_mixed_midi_inline([1, 0, 2, 2, 1, 2])),
        ):
            with self.subTest(path=label):
                data = build()
                self.assertEqual(errors(data), [], msg=f"{label} must be PT-invariant clean")
                notes = [v["invariant"] for v in BS.validate_pt_invariants(data)
                         if v["severity"] == "nonconforming"]
                self.assertEqual(notes, [], msg=f"{label} must also be free of nonconforming notes")

    @unittest.skipUnless(list(ROOT.glob("control_files/**/arbitrary wav relinked.ptx")),
                         "relinked control not present")
    def test_audio_file_list_is_not_tied_to_a_folder_named_Audio_Files(self):
        """REGRESSION (bug #25): the 0x103A audio-file list opens with the MEDIA-FOLDER name,
        length-prefixed at block offset +18 -- "Audio Files" by default, but whatever the folder is
        actually called. Both readers searched for the literal string, so a RELINKED session whose
        media lives elsewhere (here "8192 wavs") parsed as having ZERO audio files, and
        `duplicate_audio_file` raised "session has no 0x103A audio-file list".

        Found by sweeping the new import primitives across the corpus rather than by a PT verdict:
        3 of 52 sessions with regions hit it. Reading the length prefix works on 52/52 and agrees
        with the old literal search on all 49 where the folder really is named "Audio Files"."""
        data = _load_path(next(iter(ROOT.glob("control_files/**/arbitrary wav relinked.ptx"))))
        names = BS._audio_filenames(data)
        self.assertEqual(len(names), 1, msg="the relinked session's wav must be found")
        self.assertTrue(names[0].endswith(".wav"))

        z, end, entries = BS._audio_file_list(data)
        self.assertEqual([n for _o, n in entries], names,
                         msg="both readers must agree")

        out = BS.duplicate_audio_file(data, 0, "ZZ.wav")
        self.assertEqual(BS._audio_filenames(out), names + ["ZZ.wav"])
        self.assertEqual([v for v in BS.validate_pt_invariants(out)
                          if v["severity"] == "error"], [])

    def test_gate_checks_referential_integrity(self):
        """The gate verified ordinals and containment but never that a REFERENCE RESOLVES, which
        is why bug #25 escaped it entirely and was only caught by a corpus-wide sweep. Two checks
        close that hole, both holding on 162/162 PT-authored sessions:

          * every AUDIO placement's 0x104F `+11` region index resolves into `region_list()`
            (audio chain only -- the MIDI chain 0x1058>0x1057>0x1056>0x104F indexes another list);
          * a session that has regions must have a parseable 0x103A audio-file list.

        Verified to actually catch #25: reintroducing the hardcoded "Audio Files" lookup makes the
        relinked sessions report an `audio-file-list` error."""
        sessions = list(ROOT.glob("control_files/**/one clip bar 1.ptx"))
        if not sessions:
            self.skipTest("clip control not present")
        data = _load_path(sessions[0])
        self.assertEqual([v for v in BS.validate_pt_invariants(data)
                          if v["severity"] == "error"], [])

        # a dangling region reference must be reported
        bl = BS._size_driven_blocks(data)
        lanes = [(z, e) for z, e, c in bl if c == 0x1052]
        target = None
        for z, e, c in bl:
            if c == 0x104F and z >= 31 and any(lz < z and e <= le for lz, le in lanes):
                target = z
                break
        self.assertIsNotNone(target, msg="expected an audio placement to corrupt")
        broken = bytearray(data)
        broken[target + 11 : target + 15] = (9999).to_bytes(4, "little")
        kinds = [v["invariant"] for v in BS.validate_pt_invariants(bytes(broken))
                 if v["severity"] == "error"]
        self.assertIn("region-reference", kinds,
                      msg="a placement pointing at a non-existent region must be an error")

    def test_mixed_midi_synth_writes_the_kind_track_counts(self):
        """REGRESSION (bug #26): the mixed-MIDI SYNTH wrote 0x1015 as the TOTAL track count and
        0x1058 off by one, where PT's convention is 0x1015 == the audio (0x1014) count and
        0x1058 == the MIDI (0x1057) count -- zero mismatches on 162 PT-authored sessions.

        This is the synth-path twin of editor bug #17, and the gate's `container-count` note is
        what localised it on 13 of our own files. Recomputing is safe HERE (unlike in the editors,
        which adjust by delta) precisely because the convention is now pinned."""
        for spec in ([1, 0, 2], [1, 0, 2, 2, 1, 2]):
            with self.subTest(spec=spec):
                data = BS.synthesize_mixed_midi_inline(spec)
                bl = BS._size_driven_blocks(data)
                for cont, child in ((0x1015, 0x1014), (0x1058, 0x1057)):
                    for z, e, c in bl:
                        if c != cont or z < 31:
                            continue
                        framed = sum(1 for zz, _ee, cc in bl if z < zz < e and cc == child)
                        if not framed:
                            continue
                        stored = int.from_bytes(data[z + 9 : z + 13], "little")
                        self.assertEqual(stored, framed,
                                         msg=f"0x{cont:04x} must equal its 0x{child:04x} count")
                        break
                self.assertNotIn("container-count",
                                 [v["invariant"] for v in BS.validate_pt_invariants(data)])

    def test_staged_pt_verification_files_are_not_stale(self):
        """The files staged for Pro Tools testing must still be what the current code produces.

        They were generated several fixes ago and the editors have changed since (bugs #25-#28).
        A stale file wastes the one input this project cannot generate for itself -- a human
        opening Pro Tools -- and would produce a verdict about code that no longer exists."""
        root = pathlib.Path("/Users/jonkubis/Desktop/PTX_VERIFY_2026-07-24")
        if not root.exists():
            self.skipTest("staged verification set not present on this machine")

        def ctl(frag):
            hits = list(ROOT.glob(f"control_files/**/{frag}.ptx"))
            return _load_path(hits[0]) if hits else None

        jobs = [
            ("A_no_audio_needed/01_MIDI_duplicate_track.ptx",
             "two midi tracks no clips", "dup", "MIDI 1", "MIDI 3"),
            ("A_no_audio_needed/05_audio_duplicate_last_entry.ptx",
             "stereo mono stereo", "dup", "Audio 3", "Audio 4"),
            ("A_no_audio_needed/08_mixed_remove_audio.ptx",
             "multiple track types no click", "rm", "Audio 2", None),
            ("B_click_session_with_audio/10_click_duplicate.ptx",
             "3 stereo plus click", "dup", "Click 1", "Click 2"),
            ("B_click_session_with_audio/12_audio_remove_in_click_session.ptx",
             "3 stereo plus click", "rm", "Audio 2", None),
        ]
        checked = 0
        for rel, frag, op, track, new in jobs:
            path = root / rel
            src = ctl(frag)
            if src is None or not path.exists():
                continue
            want = (BS.duplicate_track(src, track, new) if op == "dup"
                    else BS.remove_track(src, track))
            self.assertEqual(_load_path(path), want,
                             msg=f"{rel} is STALE — regenerate before asking for a PT run")
            checked += 1
        self.assertGreaterEqual(checked, 3, msg="expected several staged files to verify")

    def test_same_length_duplicate_is_unchanged(self):
        """The same-length path must be byte-for-byte what it was before the variable-length
        branch existed (the PT-confirmed synth oracles depend on it)."""
        data = self._non_synth_session()
        dup = BS.duplicate_track(data, "Audio 2", "Audio X")   # same length -> fast path
        self.assertEqual(BS.remove_track(dup, "Audio X"), data)
        self.assertEqual(BS.validate(dup), [])


class NameTablePreambleTests(unittest.TestCase):
    """The 0x2519 preamble has two lengths, selected by the u32 flag at payload+0x0A (0 = base,
    entries at +0x14; 1 = extended, entries at +0x16) -- NOT by the block's +1 subtype word, which
    is 9 for both. Guessing from the subtype misread real sessions' name tables (a bogus
    multi-hundred-KB name_length) and made `_fix_name_table_count` write the entry count to the
    wrong offset, leaving it stale -- a genuine end-of-stream fault in Pro Tools."""

    def _name_tables(self, data):
        return [(z, e) for z, e, c in BS._size_driven_blocks(data) if c == 0x2519]

    def _true_entry_start(self, data, z, e):
        payload = data[z + 9 : e]
        for off in range(0, 44):
            if off + 4 > len(payload):
                break
            ln = int.from_bytes(payload[off : off + 4], "little")
            name = payload[off + 4 : off + 4 + ln]
            if 1 <= ln <= 32 and len(name) == ln and all(0x20 <= b < 0x7F for b in name):
                return off
        return None

    def test_shift_matches_the_real_entry_start(self):
        # a synth uses the base preamble; both must agree with where entries actually parse
        data = BS.synthesize_stereo_inline(8)
        for z, e in self._name_tables(data):
            self.assertEqual(0x14 + BS._name_table_shift(data, z),
                             self._true_entry_start(data, z, e))

    def test_fix_count_is_idempotent_on_both_preambles(self):
        for data in (BS.synthesize_stereo_inline(8), BS.synthesize_mono_inline(8)):
            with self.subTest(tracks=len(BS.track_types(data))):
                self.assertEqual(BS._fix_name_table_count(data), data)
                self.assertEqual(BS.validate(data), [])

    def test_duplicate_keeps_the_entry_count_in_sync(self):
        # the count must track the entries whichever preamble the session uses
        data = BS.set_track_names(BS.synthesize_stereo_inline(8),
                                  ["Kick", "Snare", "Bass", "Gtr", "Keys", "Vox", "Perc", "FX"])
        dup = BS.duplicate_track(data, "Bass", "Bas2")
        self.assertEqual(BS.validate(dup), [])
        for z, e in self._name_tables(dup):
            shift = BS._name_table_shift(dup, z)
            stored = int.from_bytes(dup[z + 9 + 0x0E + shift : z + 9 + 0x10 + shift], "little")
            self.assertEqual(stored, len(BS.track_types(dup)))

    @unittest.skipUnless((CONTROL_DIR / "robust_audio_mixed12.ptx").exists(),
                         "robust_audio_mixed12 control not present")
    def test_name_entry_stride_is_not_constant(self):
        """REGRESSION (bug #14): a 0x2519 name entry's ordinal is a u32 in almost every entry
        but a u16 in some, so the entry stride is 23 OR 21 -- `robust_audio_mixed12` is an
        unedited PT session carrying a 21-byte gap at entry [1], mid-table.

        Walking with a hardcoded 23 landed 2 bytes off and stopped at entry [2], reporting this
        12-entry table as 2 -- so `_fix_name_table_count` rewrote the session's header count
        12 -> 2. PT bounds its inline read by that count, so editing this session at all would
        have stranded PT reading 2 of 12 tracks. The count PT itself wrote is the ground truth:
        our walk must reproduce it, never overwrite it."""
        data = _load_path(CONTROL_DIR / "robust_audio_mixed12.ptx")
        bl = BS._size_driven_blocks(data)
        z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
        off = z + 9 + 0x0E + BS._name_table_shift(data, z)
        stored = int.from_bytes(data[off : off + 2], "little")

        self.assertEqual(stored, 12, msg="PT authored this table with 12 entries")
        self.assertEqual(len(BS._inline_name_entries(data, z, e, bl)), 12,
                         msg="the walk must reach all 12 entries across the 21-byte gap")
        self.assertEqual(len(BS.track_types(data)), 12)

        # the writer must agree with PT's own value rather than clobber it
        again = int.from_bytes(BS._fix_name_table_count(data)[off : off + 2], "little")
        self.assertEqual(again, stored,
                         msg="_fix_name_table_count must not rewrite a PT-authored count")

    @unittest.skipUnless((ROOT / "control_files" / "mixedmidi_click_bottom.ptx").exists(),
                         "mixedmidi_click_bottom control not present")
    def test_all_ordinal_count_is_entry_count_not_first_run(self):
        """REGRESSION (bug #15): the 0x202a/0x202b `<ALL>` u16 count is the NAME-TABLE ENTRY
        count, not the length of the first `fe ff`-terminated run.

        On a click session the `<ALL>` list is SEGMENTED -- runs of ordinals separated by
        `fe ff 10 80 <u32> ff ff`, e.g. `[0,1,2]` then `[8,9,10,11]` -- so the first run is far
        shorter than the entry count. `_fix_all_ordinal_counts` used to recompute the count to
        that first run (6 -> 3), and because dup inserts the copy's ordinal at `ords_end` while
        remove strips `ords_end-2`, the two only invert when the count moves +1/-1 with the edit.
        The recompute pinned it to the run length instead, so duplicate->remove was not a perfect
        inverse on every click-bearing session (8 of them in the corpus). Entry count is the
        ground truth: it matches PT's stored value on all 280 corpus sessions."""
        data = _load_path(ROOT / "control_files" / "mixedmidi_click_bottom.ptx")
        bl = BS._size_driven_blocks(data)
        z19, e19 = [(z, e) for z, e, c in bl if c == 0x2519][0]
        n_entries = len(BS._inline_name_entries(data, z19, e19, bl))
        self.assertEqual(n_entries, 6, msg="Audio 1/MIDI 1/Audio 2/Audio 3/MIDI 2/Click 1")

        # PT's stored <ALL> count is the entry count (6), not the first-run length (3)
        z2b, e2b = [(z, e) for z, e, c in bl if c == 0x202B][0]
        a = data.find(b"<ALL>", z2b, e2b)
        self.assertEqual(int.from_bytes(data[a + 12 : a + 14], "little"), n_entries)

        # our recompute must reproduce it, never rewrite it to the first run
        again = BS._fix_all_ordinal_counts(data)
        self.assertEqual(int.from_bytes(again[a + 12 : a + 14], "little"), n_entries,
                         msg="_fix_all_ordinal_counts must pin the count to the entry count")

        # and the whole point: duplicate -> remove is now a perfect inverse on a click session
        names = [t.name for t in BS.track_types(data)]
        src = names[0]
        new = "Z" * len(src)
        while new in names:
            new = "Y" + new[1:]
        back = BS.remove_track(BS.duplicate_track(data, src, new), new)
        # The inverse is now `identity . repair`: this fixture is OUR OWN mixed-MIDI synth output,
        # whose per-track positional ordinals were already malformed before any edit (duplicate
        # 0x2519/0x251A/0x2015 values -- the synth-writer bug). Both editors now re-key those to
        # the PT convention (`_rekey_offsynth_positional`), because Pro Tools resolves a track's
        # identity through them and REJECTED every duplicate that left them stale. So the pair
        # returns the session REPAIRED, not bit-for-bit. On a session whose ordinals are already
        # PT-valid -- every PT-authored one, 253 of 263 in the corpus -- the two are identical.
        self.assertEqual(back, BS._rekey_offsynth_positional(data),
                         msg="duplicate->remove must invert exactly, modulo the ordinal repair")
        self.assertEqual(BS.remove_track(BS.duplicate_track(back, src, new), new), back,
                         msg="...and once repaired, the pair is byte-exact (idempotent)")

    @unittest.skipUnless((CONTROL_DIR / "robust_audio_mixed12.ptx").exists(),
                         "robust_audio_mixed12 control not present")
    def test_validate_handles_the_short_name_entry_stride(self):
        """REGRESSION (bug #14, validator site): the EOS name-table walk advanced by a fixed
        23-byte trailer, so on the 21-byte-stride entry in `robust_audio_mixed12` (an unedited PT
        session) it landed 2 bytes off and misread the next u32 as a ~1.9GB name_length -> a
        FALSE 'end of stream' on a file Pro Tools loads fine. validate() must be clean here."""
        data = _load_path(CONTROL_DIR / "robust_audio_mixed12.ptx")
        self.assertEqual(BS.validate(data), [],
                         msg="validate() must not false-positive on the 21-byte-stride name entry")

    @unittest.skipUnless((ROOT / "control_files" / "various" / "two midi tracks one note each.ptx").exists(),
                         "MIDI-clip control not present")
    def test_rename_track_variable_length_on_midi(self):
        """rename_track is size-driven, so it works on MIDI-clip sessions that made the old
        scan-parser path raise `bad element tag` (this exact file did). A longer name must
        round-trip byte-exact and the renamed session must validate + resolve every index
        offset."""
        data = _load_path(ROOT / "control_files" / "various" / "two midi tracks one note each.ptx")
        src = [t.name for t in BS.track_types(data)][0]
        renamed = BS.rename_track(data, src, src + " with a longer name")
        self.assertIn(src + " with a longer name", [t.name for t in BS.track_types(renamed)])
        self.assertEqual(BS.validate(renamed), [])
        holes, zmarks = BS._index_offset_holes(renamed)
        self.assertEqual([t for _p, t in holes if t and t not in zmarks], [],
                         msg="every index offset must resolve after a variable-length rename")
        self.assertEqual(BS.rename_track(renamed, src + " with a longer name", src), data,
                         msg="rename must round-trip byte-exact")


if __name__ == "__main__":
    unittest.main()
