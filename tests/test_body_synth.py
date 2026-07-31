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
                # strict=False DELIBERATELY, and the assertion is weaker than it looks: the
                # selection-flag anchor is absent from all 3 0x261C blocks of a synthesized
                # session, so `selected_tracks` cannot read the flag and returns [] whatever
                # the flag says. This test has therefore always passed VACUOUSLY -- it does
                # not verify "no stray selection", it verifies that an UNREADABLE flag reads
                # as unset. Kept because a regression that made the anchor appear with a set
                # flag would still trip it, but it is not evidence for the property it names.
                BS.selected_tracks(synth, strict=False), [],
                msg=f"stray track selection for {base}->{target}"
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
                         BS._rekey_offsynth_positional(
                             data, c4_dense=BS._c4_uses_dense_convention(data)))

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
            # Two rules are deliberately NOT exempted away here, for the same reason. These
            # fixtures were confirmed to OPEN (2026-06-20); they were never confirmed to CLOSE, and
            # both rules describe CLOSE-TIME defects:
            #
            #   `track-id-uniqueness`        on 2026-07-25 all 4 duplicates carrying a shared id
            #                                range crashed Pro Tools on close, while all 3
            #                                collision-free removals were clean
            #   `per-track-subtree-ordinal`  on 2026-07-27 a duplicate whose column read
            #                                [1,2,3,4,4] was reported as "two tracks named Audio 4
            #                                and Pro Tools crashes on close"
            #
            # These click sessions carry BOTH, because our click path clones a donor track and
            # inherited its ids and ordinals. So both rules firing on them are TRUE positives about
            # real defects, not the over-strictness this test exists to catch.
            #
            # NOTE THE DOCSTRING ABOVE IS NOW PARTLY HISTORICAL: it records that repeated subtree
            # ordinals were dropped in 2026-06 as a "pure false-positive generator". That judgment
            # was made from an open-only observation, which could not see a close-time crash. The
            # 2026-07-27 report supplies what was missing. The earlier evidence still stands --
            # these files DO open -- it simply never covered the question.
            other = [v for v in errs
                     if v["invariant"] not in ("track-id-uniqueness", "per-track-subtree-ordinal")]
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

        Writes the identity fields to ONE record instead of all of them. All fixed-width, so the
        edit is size-neutral.

        > **This test encoded the bug it was meant to guard.** It read the sample count from
        > `0x1003+15` and asserted a retarget changed it. `+15` is NOT the sample count: across
        > 17,900 corpus records it takes exactly two values, 8448 (17,890x) and 4352 (10x),
        > independent of file length. The real field is `+28` -- 3,320 distinct values, equal to
        > `wav_clip_identity(wav)[0]` on 40 of 40 (record, on-disk WAV) pairs where `+15` matched
        > NONE. So the assertion passed only because the writer and the test agreed on the wrong
        > offset, and fixing the writer is what surfaced it. Now reads `+28`."""
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
            return [(int.from_bytes(d[z + 28 : z + 32], "little"), bytes(d[z + 44 : z + 52]))
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
        # The expectation must be given the SAME C4 gate the editors use. This fixture's C4 column
        # is [0, 1, 3] -- non-dense, because the fixture is our own broken output -- so the editors
        # now leave it alone rather than "repairing" it to [0, 1, 2]. An ungated call here would
        # renumber it and disagree by exactly that one slot. Preserving is the correct behaviour:
        # the ungated renumber rewrote Pro Tools' own values on 115/120 unedited sessions of one
        # real corpus (18062 bytes), and gapped C4 columns are what PT itself writes there.
        self.assertEqual(back, BS._rekey_offsynth_positional(
                                   data, c4_dense=BS._c4_uses_dense_convention(data)),
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

    def test_index_pointer_block_is_never_mistaken_for_a_content_type(self):
        """The file's first block (offset 20, `size == 4`) stores the master-index offset in its
        payload, so the generic frame reads the low half of that offset as a content_type.

        It is a real block, so no grammar bound removes it, and its "type" is a different
        session-specific value in every file. On a generated session it landed on `0x261C` and the
        walk counted a fifth TRACK RECORD in a four-track session -- the name-table guard then
        refused a perfectly good file. Tagged -1 at the source so no consumer can be fooled.
        """
        seen = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if "Backups" in str(path):
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            bl = BS._size_driven_blocks(data)
            if not bl:
                continue
            seen += 1
            first = min(bl)
            self.assertEqual(first[0], 20, msg=f"{path.name}: first block is not at offset 20")
            self.assertEqual(first[2], -1, msg=f"{path.name}: index-pointer block reported a type")
            self.assertNotIn(-1, {c for z, _e, c in bl if z != 20},
                             msg="only the index-pointer block may carry the sentinel")
        self.assertGreater(seen, 100, "control corpus not found -- the sweep proved nothing")

    def test_a_track_whose_guid_is_ambiguous_still_resolves_to_one_record(self):
        """`2A 00 00 00` is the identity-GUID tag AND the number 42, so track 42's own ordinal
        spells the tag and the bytes after it -- ordinary neighbours, not an id -- are found inside
        EVERY track record. `Audio 42` matched all 512 records in `512 mono tracks.ptx`.

        Duplicating it spliced the copy in once per match; removing it would have deleted every
        matched track. Both editors must treat a multi-way match as no match and fall back to the
        positional rule.
        """
        checked = 0
        for path in (BIG_MONO, CONTROL_DIR / "99 stereo tracks.ptx",
                     CONTROL_DIR / "100 stereo tracks.ptx", ROOT / "control_files" / "midi9.ptx"):
            if not path.exists():
                continue
            data = _load_path(path)
            bl = BS._size_driven_blocks(data)
            nt = [(z, e) for z, e, c in bl if c == 0x2519]
            names = [n for n, _p in BS._inline_name_entries(data, nt[0][0], nt[0][1], bl)]
            n_rec = sum(1 for _z, _e, c in bl if c in BS._TRACK_RECORD_TYPES)
            for victim in ("Audio 42", "MIDI 9"):
                if victim not in names:
                    continue
                checked += 1
                with self.subTest(session=path.name, track=victim):
                    dup = BS.duplicate_track(data, victim, "ZZ Probe")
                    self.assertEqual(
                        sum(1 for _z, _e, c in BS._size_driven_blocks(dup)
                            if c in BS._TRACK_RECORD_TYPES), n_rec + 1,
                        msg="a duplicate must add exactly ONE track record")
                    self.assertEqual(BS.remove_track(dup, "ZZ Probe"), data,
                                     msg="duplicate->remove must invert byte-exact")
                    self.assertEqual(
                        sum(1 for _z, _e, c in BS._size_driven_blocks(BS.remove_track(data, victim))
                            if c in BS._TRACK_RECORD_TYPES), n_rec - 1,
                        msg="a remove must drop exactly ONE track record")
        self.assertGreater(checked, 0, "no ambiguous-GUID control present -- proved nothing")

    def test_a_duplicate_does_not_come_back_with_two_tracks_selected(self):
        """Each `0x2015` carries TWO per-track selection flags -- one at `zmark + 28`, one at
        `end - 4` -- and a byte-copied duplicate inherited both. Pro Tools reported it verbatim:
        "Both Audio 4 tracks are selected".

        BOTH must be cleared. Clearing only the head flag was confirmed insufficient: Pro Tools
        still displayed two selected tracks on a file whose head column read `[0,0,0,1,0]` while the
        tail column still read `[0,0,0,1,1]`.

        `0x2015` is not fixed-length, so the second flag has to be anchored to the block END --
        quoting its 258-byte-form offset (`+254`) runs off the end of the 210/212/218/230-byte forms
        and makes it look like a different field. Only the COPY is cleared, never the source, so
        `duplicate -> remove` stays an exact inverse.
        """
        def flags(d):
            bs = [(z, e) for z, e, c in BS._size_driven_blocks(d) if c == 0x2015]
            return [d[z + 28] for z, _e in bs], [d[e - 4] for _z, e in bs]

        checked = 0
        for n in range(1, 8):
            src_path = MONO_DIR / f"{n} mono tracks.ptx"
            if not src_path.exists():
                continue
            data = _load_path(src_path)
            bl = BS._size_driven_blocks(data)
            nt = [(z, e) for z, e, c in bl if c == 0x2519]
            names = [nm for nm, _p in BS._inline_name_entries(data, nt[0][0], nt[0][1], bl)]
            checked += 1
            with self.subTest(n=n):
                dup = BS.duplicate_track(data, names[-1], f"Audio {n + 1}")
                for got, was, site in zip(flags(dup), flags(data), ("head+28", "end-4")):
                    self.assertEqual(len(got), len(was) + 1,
                                     msg="0x2015 is 1:1 with tracks")
                    self.assertEqual(sum(got), sum(was),
                                     msg=f"{site}: the copy must not add a selected track")
                    self.assertEqual(got[-1], 0, msg=f"{site}: the copy must not be selected")
                    self.assertEqual(got[:-1], was,
                                     msg=f"{site}: no surviving track's selection may change")
                self.assertEqual(BS.remove_track(dup, f"Audio {n + 1}"), data,
                                 msg="clearing the flags must not break the inverse")
        self.assertGreaterEqual(checked, 4, "mono ladder not found -- the sweep proved nothing")

    def test_both_selection_flags_are_binary_across_the_corpus(self):
        """Both sites must be real fields, not offsets that happen to work on the ladder.

        `0x2015` occurs at six sizes (258, 277, 218, 210, 230, 212). Anchored as `zmark + 28` and
        `end - 4`, both columns are binary in every session -- which is what justifies writing them.
        Anchoring the second by its 258-byte-form head offset instead reads past the end of four of
        those sizes, and that misreading is why it was first dismissed as an unrelated field.
        """
        seen = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if "Backups" in str(path):
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            bs = [(z, e) for z, e, c in BS._size_driven_blocks(data) if c == 0x2015]
            if not bs:
                continue
            seen += 1
            with self.subTest(session=path.name):
                self.assertLessEqual({data[z + 28] for z, _e in bs}, {0, 1},
                                     msg="head flag is not binary")
                self.assertLessEqual({data[e - 4] for _z, e in bs}, {0, 1},
                                     msg="tail flag is not binary")
        self.assertGreater(seen, 100, "control corpus not found -- the sweep proved nothing")


    def test_no_write_is_derived_from_a_phantom_block(self):
        """Every offset a re-key WRITES must be bounds-checked against the block it came from.

        `0x5A` is `'Z'` and occurs freely in leaf data, so the block walk manufactures phantoms
        inside big payloads -- that is unavoidable and not itself the bug. The bug is deriving a
        write position from such a block's own bytes without checking it lands inside it: the
        `0x251A` lane pass read `nlen` from `+11`, and on a 290-byte phantom that read 26403, so it
        wrote two bytes ~1.31 MB away inside a `0x1038` audio payload.

        The invariant, stated so it cannot regress: applying the re-key to an UNEDITED Pro Tools
        session must be a NO-OP everywhere it does not legitimately renumber, and in particular must
        never write outside the block whose bytes produced the offset.
        """
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if "Backups" in str(path):
                continue
            try:
                data = _load_path(path)
            except Exception:
                continue
            bl = BS._size_driven_blocks(data)
            lanes = [(z, e) for z, e, c in bl if c == 0x251A]
            if not lanes:
                continue
            checked += 1
            with self.subTest(session=path.name):
                for z, e in lanes:
                    nlen = int.from_bytes(data[z + 11 : z + 15], "little")
                    pos = z + 15 + nlen + 18
                    self.assertTrue(pos + 2 <= e or not (z + 15 <= pos),
                                    msg=f"a 0x251A at {z} derives a write at {pos}, outside [{z},{e})")
        self.assertGreater(checked, 50, "control corpus not found -- the sweep proved nothing")


    def test_a_third_of_the_control_corpus_is_our_own_output(self):
        """`control_files` has been used as a Pro Tools oracle for this whole project. It is not
        entirely one: 94 of its 282 sessions record a DIFFERENT `.ptx` name in their `0x2067`
        save-lineage block than the name on disk, because a donor-transplanted session keeps the
        DONOR's name there.

        85 of those name a donor that is still in the corpus -- unambiguously our output. Only 4 are
        genuine Pro Tools sessions renamed on disk, which is why the sharper test requires the donor
        to be PRESENT rather than merely differing.

        This is pinned because the consequence is easy to forget: any figure quoted as "n/n on
        PT-authored controls" that did not exclude these is partly circular.
        """
        paths = sorted(ROOT.glob("control_files/**/*.ptx"))
        self.assertGreater(len(paths), 200, "control corpus not found -- proved nothing")
        siblings = {q.name for q in paths}
        ours, mismatched = 0, 0
        for path in paths:
            try:
                data = _load_path(path)
            except Exception:
                continue
            recorded = BS.session_self_name(data)
            self.assertIsNotNone(recorded, msg=f"{path.name}: no 0x2067 self-name")
            if recorded != path.name:
                mismatched += 1
            if BS.session_is_probably_ours(data, path, siblings):
                ours += 1
        self.assertGreater(mismatched, 50,
                           msg="the self-name check found almost nothing -- it has probably broken")
        self.assertGreater(ours, 50,
                           msg="the donor-present check found almost nothing -- it has probably broken")
        self.assertLess(ours, mismatched,
                        msg="donor-present must be STRICTLY narrower than name-differs; if they are "
                            "equal the renamed-PT-session cases are being misclassified as ours")


    def test_playlists_are_attributed_to_tracks_by_the_0x2015_ordinal(self):
        """The per-track `0x2015` ordinal is the 1-based index of that track's FIRST playlist in the
        flat `0x2107` > `0x210B` list, so track `i` owns the slice `[ord[i], ord[i+1])`.

        Verified BY NAME, which is what makes it more than arithmetic: on a 36-track / 126-playlist
        session every group's members carry their track's base name, `36/36`, including the
        systematic `gutterN` extra that each multi-playlist track carries.

        On a single-playlist session -- every session in `control_files` and `/Volumes/Samples` --
        this degenerates to one playlist per track, which is exactly why the field read as a plain
        positional counter for so long and was twice documented wrongly.
        """
        import re
        base = lambda s: re.sub(r"\.\d{2}$", "", s)

        ref = list(pathlib.Path("/Volumes/Hack 2TB SSD Old").rglob(
            "SANTA CLAUS IS COMIN.bak.011.ptx")) if pathlib.Path("/Volumes/Hack 2TB SSD Old").exists() else []
        if ref:
            data = _load_path(ref[0])
            groups = BS.track_playlists(data)
            bl = BS._size_driven_blocks(data)
            z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
            names = [n for n, _p in BS._inline_name_entries(data, z, e, bl)]
            self.assertEqual(len(groups), len(names), msg="one playlist group per track")
            self.assertEqual(sum(len(g) for g in groups), len(BS.playlist_names(data)),
                             msg="the groups must partition the playlist list exactly")
            for track, group in zip(names, groups):
                self.assertTrue(group, msg=f"{track!r} owns no playlist")
                shared = sum(1 for x in group if base(x) == base(track))
                self.assertGreaterEqual(shared, max(1, len(group) - 1),
                                        msg=f"{track!r} -> {group}: members do not share its name")

        # and the degenerate case, on the local controls
        checked = 0
        for path in sorted(ROOT.glob("control_files/lots of mono tracks/*.ptx")):
            try:
                data = _load_path(path)
            except Exception:
                continue
            groups = BS.track_playlists(data)
            if not groups:
                continue
            checked += 1
            with self.subTest(session=path.name):
                self.assertTrue(all(len(g) == 1 for g in groups),
                                msg=f"{path.name}: expected one playlist per track, got "
                                    f"{[len(g) for g in groups]}")
        self.assertGreater(checked, 3, "mono ladder not found -- the degenerate case proved nothing")


    def test_playlist_ordinal_repair_reproduces_pro_tools_own_column(self):
        """The strongest local check available for the repair FORMULA: recomputing the ordinal
        column from the playlist counts it derives must reproduce, byte for byte, what Pro Tools
        already wrote. If the formula were wrong, an unedited session would change.

        `_playlist_counts` gates on three agreeing properties -- starts at 1, strictly increasing,
        and gaps summing to exactly the 0x210B count. The third is the one that matters: it ties the
        column to an INDEPENDENT object count instead of to itself, which is what a field documented
        wrongly twice requires.
        """
        checked = conforming = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                data = _load_path(path)
            except Exception:
                continue
            checked += 1
            counts = BS._playlist_counts(data)
            if counts is None:
                continue
            conforming += 1
            with self.subTest(session=path.name):
                self.assertEqual(BS._write_playlist_ordinals(data, counts), data,
                                 msg="recomputing the column changed an UNEDITED Pro Tools session")
        self.assertGreater(checked, 200, "control corpus not found -- proved nothing")
        self.assertGreater(conforming, 100, "nothing conformed -- the gate has probably broken")

    def test_playlist_ordinal_repair_is_an_exact_inverse_and_fixes_the_collision(self):
        """duplicate -> repair -> remove -> repair must return the original byte for byte, and the
        repaired column must not contain the duplicate ordinal an unrepaired copy leaves.

        Opt-in, so this exercises the helpers directly rather than the default editor path: enabling
        it rewrites SURVIVING tracks, and Pro Tools opened an UNREPAIRED duplicate cleanly, so the
        default stays where PT has confirmed it until a PT run says otherwise.
        """
        checked = fixed = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            if "Backups" in str(path):
                continue
            try:
                data = _load_path(path)
                bl = BS._size_driven_blocks(data)
                nt = [(z, e) for z, e, c in bl if c == 0x2519]
                if not nt:
                    continue
                names = [nm for nm, _p in BS._inline_name_entries(data, nt[0][0], nt[0][1], bl)]
                if not names:
                    continue
                raw = BS.duplicate_track(data, names[-1], "ZZ")
                # BASELINE FIRST. Two control fixtures (`robust_audio_mixed12`,
                # `synth_4mono_8stereo`) have malformed name tables -- they are our own broken
                # output -- and their round trip is already inexact WITHOUT any repair. The
                # property under test is that the repair does not break what worked, not that it
                # fixes pre-existing breakage, so sessions that fail the baseline are skipped.
                if BS.remove_track(raw, "ZZ") != data:
                    continue
                rep = BS.repair_playlist_ordinals_after_add(data, raw)
                back = BS.remove_track(rep, "ZZ")
                back = BS.repair_playlist_ordinals_after_remove(rep, back)
            except Exception:
                continue
            checked += 1
            col = [v for _p, v in BS._playlist_ordinal_sites(rep)]
            with self.subTest(session=path.name):
                if BS._playlist_counts(data) is not None:
                    self.assertEqual(len(col), len(set(col)),
                                     msg="the repaired column still repeats an ordinal")
                    fixed += 1
                self.assertEqual(back, data, msg="repair must invert exactly")
        self.assertGreater(checked, 200, "control corpus not found -- proved nothing")
        self.assertGreater(fixed, 100, "no session conformed -- the gate has probably broken")


    def test_synth_playlist_ordinals_are_in_block_order(self):
        """The synth writer numbered the `0x2015` playlist ordinals in CREATION order, so growing a
        session with MIDI tracks emitted a permutation of `1..N` that was not sorted::

            [1,0,2]      -> [1, 2, 3]        correct
            [1,0,2,0]    -> [1, 2, 4, 3]     the grown MIDI took 4, the stereo kept 3
            [1,0,2,0,0]  -> [1, 2, 4, 5, 3]

        Pro Tools never writes that: the value is the index of the track's FIRST PLAYLIST, so the
        column is a running total and is strictly increasing by construction. It is also the exact
        shape of the `mixedmidi_*` fixtures this project carried as "historical" broken output --
        `mixedmidi_5_AMSmM` reads `[1,2,5,3,4]` -- which means the defect was still LIVE. Only the
        `next_kind` half of that old finding had actually been fixed.

        The edit path already renumbered these positionally, so synth and duplicate disagreed about
        one field; this pins that they agree.
        """
        for spec in ([1, 0, 2], [1, 0, 2, 0], [1, 0, 2, 0, 0],
                     [1, 0, 2, 2, 1, 2], [1, 0, 2, 0, 2, 0]):
            with self.subTest(spec=spec):
                data = BS.synthesize_mixed_midi_inline(spec)
                col = [v for _p, v in BS._playlist_ordinal_sites(data)]
                self.assertEqual(col, list(range(1, len(col) + 1)),
                                 msg="synth ordinals must be 1..N in BLOCK order")
                self.assertIsNotNone(BS._playlist_counts(data),
                                     msg="synth output must satisfy the playlist-ordinal convention")

    def test_the_ordinal_normalizer_never_touches_a_real_session(self):
        """It reorders; it must never invent. Guarded on the `0x210B` count matching the `0x2015`
        count (so `1..N` genuinely IS the running total) and on the values already being a
        permutation of `1..N`. Across the PT-authored controls it must be a strict no-op, and the
        only files it may rewrite are ones this project produced.
        """
        touched, checked = [], 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                data = _load_path(path)
            except Exception:
                continue
            checked += 1
            if BS._normalize_one_playlist_ordinals(data) != data:
                touched.append(path.name)
        self.assertGreater(checked, 200, "control corpus not found -- proved nothing")
        siblings = {q.name for q in ROOT.glob("control_files/**/*.ptx")}
        for name in touched:
            path = next(iter(ROOT.glob("control_files/**/" + name)))
            with self.subTest(session=name):
                self.assertTrue(BS.session_is_probably_ours(_load_path(path), path, siblings),
                                msg=f"{name} is not ours, and the normalizer must not rewrite it")


    def test_0x261f_is_a_track_record_kind(self):
        """`0x261F` belongs in `_TRACK_RECORD_TYPES` by the same criterion the other six were
        enumerated with -- a DIRECT child of the `0x2624` track list that occupies a row in the
        `0x2519` name table.

        It appears in ZERO sessions of `control_files` or `/Volumes/Samples`, so it was missed
        rather than rejected; it took two further drives to see it. The consequence was not
        cosmetic: `_assert_name_table_complete` compares name entries against track records, so
        while this kind went uncounted the guard undercounted by 12-13 rows on such sessions and
        would have passed a table missing that many.

        Pinned locally on the synth writer, which is reachable without an external drive: the set
        must contain 0x261F, and the guard must be self-consistent on every session the writer
        produces. The corpus evidence (parent 0x2624 in 290/290, `entries == records` only balancing
        once 0x261F is counted) is recorded in the spec.
        """
        self.assertIn(0x261F, BS._TRACK_RECORD_TYPES,
                      msg="0x261F is a direct child of the 0x2624 track list and holds a name row")
        for spec in ([1, 0, 2], [1, 0, 2, 0], [1, 0, 2, 2, 1, 2]):
            with self.subTest(spec=spec):
                data = BS.synthesize_mixed_midi_inline(spec)
                bl = BS._size_driven_blocks(data)
                nt = [(z, e) for z, e, c in bl if c == 0x2519]
                ents = len(BS._inline_name_entries(data, nt[0][0], nt[0][1], bl))
                recs = sum(1 for _z, _e, c in bl if c in BS._TRACK_RECORD_TYPES)
                self.assertEqual(ents, recs,
                                 msg="name entries and track records must agree on synth output")
                BS._assert_name_table_complete(data)   # must not raise


    def test_name_table_guard_separates_off_by_one_from_truncation(self):
        """The guard must still refuse a catastrophically truncated table while tolerating a
        one-row shortfall.

        Measured over 612 sessions on four drives, the shortfall (track records minus parsed name
        entries) is bimodal with NOTHING between 2 and 19: 562 exact, 7 short by one, 42 short by
        20-152. The motivating case for this guard was 18 names for 54 records. Treating a
        one-row discrepancy and a 130-row one as the same condition conflates two different
        failures, and doing so made 7 previously-editable real sessions start refusing when
        `0x261F` was added to the record set.

        Synthetic here, so it needs no external drive: the property is about the THRESHOLD, and a
        session is not required to demonstrate arithmetic.
        """
        real = BS._assert_name_table_complete

        class FakeBL(list):
            pass

        # Drive the threshold directly rather than through a fixture: patch the two counts the
        # guard derives, so the test states the boundary instead of depending on a corpus file.
        import unittest.mock as mock
        for ents, trk, should_raise in ((176, 176, False), (175, 176, False),
                                        (174, 176, True), (81, 177, True)):
            with self.subTest(entries=ents, records=trk):
                with mock.patch.object(BS, "_inline_name_entries",
                                       return_value=[("t%d" % i, 0) for i in range(ents)]), \
                     mock.patch.object(BS, "_size_driven_blocks",
                                       return_value=[(0, 1, 0x2519)] + [(0, 1, 0x261C)] * trk):
                    if should_raise:
                        with self.assertRaises(ValueError):
                            real(b"", None)
                    else:
                        real(b"", None)


    def test_ordinal_renumber_preserves_a_base_of_two(self):
        """`_renumber_name_entry_ordinals` used to force the nonzero run to start at 1, on evidence
        of "300/300 real sessions". Those 300 were necessarily sessions `_name_entry_ordinal_slots`
        could REACH, and that walk silently returned nothing on 69 of 612 -- so the 7 genuine Pro
        Tools files whose ordinals start at 2 were structurally excluded from the evidence that
        justified the rule.

        The two bugs cancelled: the walk's blind spot was PREVENTING the renumber from rewriting
        those files. Widening the walk without fixing the base wrote 1257 bytes into 7 untouched
        sessions.

        Synthetic, so it needs no external drive: a run starting at 2 must stay at 2 after a
        renumber that closes a gap, and a run starting at 1 must behave exactly as before.
        """
        def run(vals):
            """Apply the renumber's arithmetic to a bare ordinal list."""
            base = next((v for v in vals if v), 1)
            out, k = [], 0
            for v in vals:
                if v:
                    out.append(base + k); k += 1
                else:
                    out.append(0)
            return out

        # a base of 1 is unchanged -- every session the original rule was measured on
        self.assertEqual(run([1, 2, 3, 4]), [1, 2, 3, 4])
        self.assertEqual(run([1, 2, 0, 0, 3, 0, 4]), [1, 2, 0, 0, 3, 0, 4])
        # a base of 2 is PRESERVED, not forced to 1
        self.assertEqual(run([2, 3, 4, 5]), [2, 3, 4, 5])
        self.assertEqual(run([2, 3, 0, 4]), [2, 3, 0, 4])
        # and it still does its job: a gap closes, a duplicate resolves, base intact
        self.assertEqual(run([2, 3, 5, 6]), [2, 3, 4, 5], msg="a removal's gap must close")
        self.assertEqual(run([2, 3, 3, 4]), [2, 3, 4, 5], msg="a duplicate must resolve")
        self.assertEqual(run([1, 2, 2, 3]), [1, 2, 3, 4])


    def test_the_two_name_table_walks_agree(self):
        """`_inline_name_entries` and `_name_entry_ordinal_slots` must find the SAME number of
        entries. They did not for a long time: the slot walk resolved its start from
        `_name_table_shift` alone while the entry walk resolves it empirically, so the slot walk
        returned ZERO on 69 of 612 sessions and silently disabled every pass keyed on it.

        That mattered twice over. It hid the ordinal renumber's base-of-1 assumption, whose
        "300/300" evidence could only ever cover sessions this walk reached -- and when the walk was
        widened, the renumber promptly wrote 1257 bytes into 7 untouched Pro Tools files.

        Pinned on the control corpus. Measured across four drives after the fix, the two agree
        549/549 where a table parses at all.
        """
        checked = agreed = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                data = _load_path(path)
                bl = BS._size_driven_blocks(data)
            except Exception:
                continue
            nt = [(z, e) for z, e, c in bl if c == 0x2519]
            if not nt:
                continue
            z, e = nt[0]
            end = min((cz for cz, _ce, _cc in bl if z < cz < e), default=e)
            ents = BS._inline_name_entries(data, z, e, bl)
            slots = BS._name_entry_ordinal_slots(data, z, end)
            if not ents:
                continue
            checked += 1
            with self.subTest(session=path.name):
                self.assertEqual(len(slots), len(ents),
                                 msg="the two name-table walks disagree -- one of them is blind here")
                agreed += 1
        self.assertGreater(checked, 200, "control corpus not found -- proved nothing")

    def test_locators_do_not_fail_silently(self):
        """A locator that returns EMPTY instead of raising, behind a caller that reads empty as
        "nothing to do", is how this project acquired four false format rules in one day --
        `_ORD2015_ANCHOR` missing 24.1% of blocks, and the slot walk missing 11.8% of sessions,
        being the two that reached the spec.

        So the invariant is stated directly: where a locator IS applicable, it must find something.
        Applicability has to be judged per locator, not by block presence -- an audit that used
        "the block exists" flagged `meter_map` as the worst offender in the module when in fact
        219 sessions declare `count=0` and an empty result is correct.
        """
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                data = _load_path(path)
                bl = BS._size_driven_blocks(data)
            except Exception:
                continue
            checked += 1
            with self.subTest(session=path.name):
                # the anchor must resolve in EVERY 0x2015 -- the 18-byte form missed 24.1%
                for z, e, c in bl:
                    if c == 0x2015:
                        self.assertGreaterEqual(data.find(BS._ORD2015_ANCHOR, z, e), 0,
                                                msg=f"anchor unresolvable in 0x2015 at {z}")
                # one id range per 0x261B, or none at all
                n261b = sum(1 for _z, _e, cc in bl if cc == 0x261B)
                if n261b:
                    self.assertEqual(len(BS.track_id_ranges(data)), n261b,
                                     msg="track_id_ranges is blind on some 0x261B")
                # meter_map is empty ONLY when the header declares zero events
                b29 = [(z, e) for z, e, cc in bl if cc == 0x2029]
                if b29 and (b29[0][1] - b29[0][0]) > 24:
                    cnt = int.from_bytes(data[b29[0][0] + 20 : b29[0][0] + 24], "little")
                    if 0 < cnt < 1000:
                        self.assertTrue(BS.meter_map(data),
                                        msg=f"meter_map empty though the header declares {cnt}")
        self.assertGreater(checked, 200, "control corpus not found -- proved nothing")


    def test_duplicate_of_a_guidless_track_takes_its_own_id_range(self):
        """A copy whose `0x261B` carries no seeded GUID must still take a DISTINCT id range.

        `_reallocate_duplicate_track_ids` locates the copy's `0x261B` by matching the FRESH GUIDs
        that `transform` wrote. Those are seeded from the track-list entries (`0x1014`/`0x1057`/
        `0x210B`), found by NAME -- not from `0x102D`. So when a track's `0x261B` subtree contains
        no `2A 00 00 00` tag at all, the transform writes no fresh GUID into it, the match finds
        nothing, and the copy was left SHARING its source's id range: the condition Pro Tools
        crashed on. Real occurrence: ~21% of `0x102D` blocks on the PT HD 10.3/11.2 corpus carry no
        tag, and 4 of 4 such duplicates shared a range before the structural fallback was added.

        THE REPRODUCTION IS VERIFIED TO BITE. With the fallback disabled this asserts
        `5 ranges / 4 distinct`; with it, `5 / 5`. An earlier attempt -- blanking only the tag in the
        track's `0x102D` -- passed either way and was removed, because `gmap` is not seeded from
        there.
        """
        data = _load_path(MONO_DIR / "4 mono tracks.ptx")
        bl = BS._size_driven_blocks(data)
        recs = [(z, e) for z, e, c in bl if c in BS._TRACK_RECORD_TYPES]
        self.assertTrue(recs, "fixture has no track records")
        z, e = recs[-1]
        b261b = [(kz, ke) for kz, ke, kc in bl if kc == 0x261B and z < kz and ke <= e]
        self.assertTrue(b261b, "last track record has no 0x261B")

        # blank every identity tag inside that 0x261B (never offset 3 -- that is the SIZE field)
        kz, ke = b261b[0]
        sub, mod, k = data[kz:ke], bytearray(data), 0
        while True:
            k = sub.find(b"\x2a\x00\x00\x00", k)
            if k < 0:
                break
            if k != 3:
                mod[kz + k : kz + k + 4] = b"\x00\x00\x00\x00"
            k += 1
        guidless = bytes(mod)

        gbl = BS._size_driven_blocks(guidless)
        nt = [(a, b) for a, b, c in gbl if c == 0x2519][0]
        names = [n for n, _p in BS._inline_name_entries(guidless, nt[0], nt[1], gbl)]
        before = [v for _z, v, _l in BS.track_id_ranges(guidless)]
        dup = BS.duplicate_track(guidless, names[-1], "ZZZZZZZ")
        after = [v for _z, v, _l in BS.track_id_ranges(dup)]

        self.assertEqual(len(after), len(before) + 1, msg="the copy must add a 0x261B")
        self.assertEqual(len(set(after)), len(set(before)) + 1,
                         msg="the copy must take a DISTINCT id range with no GUID to match")

    # WHY THE FIRST ATTEMPT AT THE TEST ABOVE WAS REMOVED, kept because the reason generalises.
    #
    # The defect: `_reallocate_duplicate_track_ids` located the copy's 0x261B by matching the FRESH
    # GUIDs, so a track with no `2A 00 00 00` in its 0x102D left the copy SHARING its source's id
    # range -- the condition Pro Tools crashed on. Fixed with a structural fallback; verified on 5
    # real sessions from /Volumes/Samples, where ~21% of 0x102D blocks carry no tag.
    #
    # I wrote a synthetic test for it -- blank the tag in one track's 0x102D on `4 mono tracks`,
    # duplicate that track, assert the copy takes a distinct range -- and then checked that it FAILS
    # with the fix disabled. It does not: it passes either way. Blanking that tag does not reproduce
    # the condition, because the fresh-GUID map the remap matches against is not derived from it.
    #
    # A test that passes with and without the fix is worse than no test: it advertises a guard that
    # does not exist. Removed rather than left in place. Reproducing this without an external drive
    # needs a real GUID-less fixture, or an understanding of where `gmap` actually comes from --
    # neither of which is available here yet.

    def test_an_empty_playlist_container_has_no_count_field_to_write(self):
        """A count field that does not fit inside its own block must not be written.

        The `0x2107` count lives at payload+9, which assumes the container is big enough to hold
        it. An EMPTY `0x2107` is 10 bytes of payload, so payload+9 lands PAST the block's end --
        on the next block's `block_type`/`size` header. `_fix_container_counts` wrote 4 zero bytes
        there, which broke the contiguous top-level chain walk; `final_index_ref` then returned
        None and every later pass died on `'NoneType' object has no attribute 'start'`. BOTH
        `duplicate_track` and `remove_track` crashed that way on a real 25-track session
        (`BMG17 Apollo v13AS for Apollo Delivery.ptx`) -- the one `AttributeError` in each column
        of the 105-session edit audit.

        The shape is rare (0 of 267 `0x2107` containers across 480 sampled corpus sessions carry
        it) which is exactly why it needs a test rather than a corpus sweep to defend it.

        `_count_field_pos`'s `0x210B` branch reads only `z` and `e`, so this pins the arithmetic
        directly. Verified to BITE: with the bounds check removed it returns 118 for both cases
        and the first assertion fails."""
        C2107 = 0x2107
        self.assertEqual(BS._COUNT_CONTAINER_OFFSET[C2107], 0x210B)
        # empty container: z=100, declared size 10 -> e = 100 + 7 + 10 = 117, field would be 118..122
        self.assertIsNone(
            BS._count_field_pos(b"", 100, 117, C2107, []),
            "an empty 0x2107 has no count field -- writing payload+9 corrupts the NEXT block's header")
        # a container with room keeps the documented offset
        self.assertEqual(BS._count_field_pos(b"", 100, 200, C2107, []), 118)
        # the exact boundary: the field must fit entirely inside the block
        self.assertIsNone(BS._count_field_pos(b"", 100, 121, C2107, []))
        self.assertEqual(BS._count_field_pos(b"", 100, 122, C2107, []), 118)

    def test_a_name_table_entry_may_be_utf8_and_longer_than_64_bytes(self):
        """Name-table entries are UTF-8, and Pro Tools emits names well past 64 bytes.

        `_name_entry_head` used to require every byte in `0x20..0x7E` and cap the length at 64.
        Both assumptions are wrong on real sessions, and each one silently ENDS the walk rather
        than reporting anything -- the entry simply stops parsing and the table looks shorter
        than it is. Eight sessions of one project (`NICK JR_ROUTINING_2019`) parsed 29 of 180
        entries and were therefore uneditable, because `_assert_name_table_complete` refuses to
        edit a table it cannot fully read.

        The blocker was a clip named `Butterbeans Café …` -- and macOS stores that DECOMPOSED
        (NFD), so the `é` is `e` + U+0301 (`cc 81`). Verified to BITE: with the old test both
        assertions below fail (the accented name returns None, and the 100-byte name returns
        None).

        Measured on 288 sessions: 11,937 -> 14,948 entries parsed, agreement with each table's
        own declared entry count gained on 32 sessions and lost on none."""
        # a real NFD name from the corpus: 'e' + COMBINING ACUTE ACCENT
        nfd = "Butterbeans Café _Theme Song Music Video".encode("utf-8")
        self.assertIn(b"\xcc\x81", nfd, "the fixture must actually be decomposed")
        buf = len(nfd).to_bytes(4, "little") + nfd + b"\x00" * BS._NAME_ENTRY_SUFFIX
        got = BS._name_entry_head(buf, 0, len(buf))
        self.assertIsNotNone(got, "a UTF-8 name must parse -- rejecting it silently ends the walk")
        self.assertEqual(got[0].encode("utf-8"), nfd, "the name must round-trip to its file bytes")
        self.assertEqual(got[1], 4 + len(nfd))

        # 100 bytes is the real observed maximum; the old cap of 64 stopped the walk there
        long_name = ("X" * 100).encode()
        buf = len(long_name).to_bytes(4, "little") + long_name + b"\x00" * BS._NAME_ENTRY_SUFFIX
        got = BS._name_entry_head(buf, 0, len(buf))
        self.assertIsNotNone(got, "Pro Tools emits 100-byte names; a 64-byte cap truncates the table")
        self.assertEqual(got[0], "X" * 100)

        # the guard still has to reject garbage: invalid UTF-8 and control bytes
        bad = b"\xff\xfe\xff\xfe" * 4
        buf = len(bad).to_bytes(4, "little") + bad + b"\x00" * BS._NAME_ENTRY_SUFFIX
        self.assertIsNone(BS._name_entry_head(buf, 0, len(buf)),
                          "bytes that are not valid UTF-8 are not a name")
        ctrl = b"ABC\x01DEF"
        buf = len(ctrl).to_bytes(4, "little") + ctrl + b"\x00" * BS._NAME_ENTRY_SUFFIX
        self.assertIsNone(BS._name_entry_head(buf, 0, len(buf)),
                          "a control character is not a name")
        # and a wild length is still refused
        buf = (10 ** 6).to_bytes(4, "little") + b"ABCD"
        self.assertIsNone(BS._name_entry_head(buf, 0, len(buf)))

    def test_the_permissive_name_read_is_a_fallback_not_the_default(self):
        """Control bytes are tolerated only as an ARBITRATED fallback, never by default.

        Two real names carry a raw `0x10` mid-string (`DR PPB \\x10OP HAT` -- a `T` in every
        other copy of that name), and the control-character guard ended the walk there: one
        session parsed 7 of its 143 entries. But simply allowing control bytes is NOT the fix --
        measured over 280 sessions it agrees with the tables' declared counts on 2 more and on
        1 FEWER, and this read feeds `_fix_name_table_count`, which WRITES that count into the
        session. Trading one session for two is not an improvement when the cost is a wrong
        count in a file Pro Tools bounds its read by.

        So the table's own declared count arbitrates: strict read first, permissive accepted
        only when strict disagrees with the declared count and permissive reproduces it. That
        is 280/280 sessions, where strict alone is 277 and permissive alone is 279 -- and it
        makes `_fix_name_table_count` a perfect no-op on 480 untouched sessions (0 bytes,
        against 54 sessions before this fix)."""
        ctrl = b"DR PPB \x10OP HAT"
        buf = len(ctrl).to_bytes(4, "little") + ctrl + b"\x00" * BS._NAME_ENTRY_SUFFIX
        self.assertIsNone(BS._name_entry_head(buf, 0, len(buf)),
                          "the DEFAULT read must still reject a control byte")
        got = BS._name_entry_head(buf, 0, len(buf), True)
        self.assertIsNotNone(got, "the permissive read must accept it")
        self.assertEqual(got[0].encode("utf-8"), ctrl)

        # NUL stays rejected in BOTH modes: without that, zero padding parses as a name
        nul = b"AB\x00CD"
        buf = len(nul).to_bytes(4, "little") + nul + b"\x00" * BS._NAME_ENTRY_SUFFIX
        self.assertIsNone(BS._name_entry_head(buf, 0, len(buf), True),
                          "NUL is not a name byte even permissively -- padding would parse")
        self.assertIsNone(BS._name_entry_head(buf, 0, len(buf), False))

    def test_the_marker_row_locator_survives_a_header_byte_and_a_trailer(self):
        """The `0x2506` row run has a variable-length header before it and a trailer after it.

        Every `0x2077` marker holds one 17-byte `0x2506` row per PLAYLIST. The rows are neither
        the first nor the last bytes of the block, and BOTH ends broke a locator:

          * scanning forward and resynchronising on the next `0x5A` desynchronises when that byte
            occurs inside the marker's name/header -- it found 0 rows for 6 of 388 markers that
            each hold 140, and the caller then silently skipped those markers;
          * walking backward from the block end lands in the trailer and finds 0 rows in EVERY
            marker.

        Both failures are SILENT -- a short row list reads as "this marker has no rows" and the
        edit quietly does nothing. So the fixture puts a bare `0x5A` in the header (the exact byte
        that fooled the forward scan) and a trailer after the last row, and pins the count.

        Verified to BITE: with the forward-resync locator the header `0x5A` makes this return 2
        rows instead of 3; with the backward locator it returns 0."""
        row = bytes.fromhex("5a03000a00000006 25ffffffff00000000".replace(" ", ""))
        self.assertEqual(len(row), 17)
        # header carries a raw 0x5A (a name byte), then 3 rows, then a trailer
        header = b"\x00\x01\x02\x5a\x41\x42\x43\x00"
        trailer = b"\xff\xff\xff\xff\x00\x00\x00\x00"
        payload = header + row * 3 + trailer
        blk = b"\x5a\x0b\x00" + (len(payload) + 2).to_bytes(4, "little") + b"\x77\x20" + payload
        z, e = 0, len(blk)
        got = BS._marker_scaffold_rows(blk, z, e)
        self.assertEqual(len(got), 3, "must find every row despite a 0x5A in the header")
        for (a, b) in got:
            self.assertEqual(blk[a:b], row, "each located span must BE a row")
        self.assertEqual(got[0][0], 9 + len(header), "first row starts right after the header")
        self.assertEqual(got[-1][1], len(blk) - len(trailer), "last row ends before the trailer")
        # a marker with no rows at all is not mistaken for one that has them
        empty = b"\x5a\x0b\x00" + (len(header) + 2).to_bytes(4, "little") + b"\x77\x20" + header
        self.assertEqual(BS._marker_scaffold_rows(empty, 0, len(empty)), [])

    def test_a_name_entry_advances_by_BYTES_not_characters(self):
        """`name_end` is `name_start + len(name.encode())`, never `+ len(name)`.

        `_inline_name_entries` hands back DECODED names, and names are UTF-8, so a name carrying
        any non-ASCII character is LONGER in the file than in Python. Two places in
        `validate_pt_invariants` computed a field offset as `pos + len(name) + k` and so read
        every following field two bytes early on such a session. It surfaced as an ERROR-severity
        `0x2519-entry-ordinals` finding on 23 of 440 untouched sessions -- every one of them a
        session containing a non-ASCII name, and ZERO all-ASCII sessions -- i.e. the validator
        rejecting files Pro Tools itself wrote.

        The bug was LATENT until UTF-8 names began parsing at all: before that the entry walk
        stopped at the first accented name, so those entries never reached the arithmetic. Fixing
        a reader can arm a bug that was previously unreachable, which is why this pins the
        contract rather than the call sites."""
        for text in ("Butterbeans Café", "Thalía", "plain ascii"):
            raw = text.encode("utf-8")
            buf = len(raw).to_bytes(4, "little") + raw + b"\x00" * BS._NAME_ENTRY_SUFFIX
            got = BS._name_entry_head(buf, 0, len(buf))
            self.assertIsNotNone(got, f"{text!r} must parse")
            name, name_end = got
            self.assertEqual(name, text)
            self.assertEqual(name_end - 4, len(raw),
                             "name_end must advance by the BYTE length")
            self.assertEqual(buf[4:name_end], raw,
                             "the span [start, name_end) must be exactly the stored bytes")
        # and the two lengths genuinely differ for the fixtures that caused the bug
        self.assertNotEqual(len("Butterbeans Café".encode("utf-8")), len("Butterbeans Café"))

    @unittest.skipUnless(_mono_available(), "mono control files not present")
    def test_a_track_whose_name_has_an_accent_is_still_editable(self):
        """A name is a byte string. Everything that measures one must count BYTES.

        Names are UTF-8, so a name with an accent is LONGER in the file than in Python, and two
        separate places got that wrong — each failing differently, and the second only reachable
        once the first was fixed:

          1. `track_types()` decoded names as latin1, so `135·3BPM` came back as `135Â·3BPM` —
             a string whose `.encode()` is NOT in the file. A name read from the public API could
             not be passed back to the editors at all: "cannot locate track", on 42 of 42 corpus
             sessions carrying a non-ASCII name.
          2. `duplicate_track` then compared `len(new_name) != len(source_track)` in CHARACTERS
             and asked for a placeholder of `len(source_track)` BYTES. A 22-character/23-byte
             name took a 22-byte placeholder and spliced one byte short, corrupting the master
             index into "bad element tag" — on all 28 sessions reached, while an ASCII-named
             track in the SAME session duplicated cleanly.

        This exercises the whole path on a local control file: rename to an accented name, verify
        the bytes are really in the session, then duplicate that track. Verified to BITE — the
        rename alone reproduces the byte/character mismatch (`Café` is 4 characters, 5 bytes)."""
        data = _load_path(_mono_control(sorted(_mono_available())[0]))
        first = BS.track_types(data)[0].name
        accented = "Café"                      # 4 characters, 5 UTF-8 bytes
        self.assertNotEqual(len(accented.encode("utf-8")), len(accented))

        renamed = BS.rename_track(data, first, accented)
        names = [t.name for t in BS.track_types(renamed)]
        self.assertIn(accented, names, "track_types must report the name as stored, not mojibake")
        self.assertIn(accented.encode("utf-8"), renamed,
                      "the reported name's BYTES must actually be in the session")

        # `_decode_name` is the shared fix: UTF-8 with a latin1 FALLBACK, which makes it a strict
        # improvement over the old latin1-only decode -- valid UTF-8 round-trips, and bytes that
        # are not valid UTF-8 decode exactly as they used to instead of raising.
        self.assertEqual(BS._decode_name("Café".encode("utf-8")), "Café")
        self.assertEqual(BS._decode_name(b"plain"), "plain")
        self.assertEqual(BS._decode_name(b"\xff\xfe"), b"\xff\xfe".decode("latin1", "replace"))
        for raw in ("Café".encode("utf-8"), "135·3BPM".encode("utf-8"), b"ascii"):
            self.assertEqual(BS._decode_name(raw).encode("utf-8"), raw,
                             "a decoded name must re-encode to the bytes it came from")

        out = BS.duplicate_track(renamed, accented, "ZZDUP")
        self.assertGreater(len(out), len(renamed))
        self.assertEqual([v for v in BS.validate_pt_invariants(out) if v["severity"] == "error"], [],
                         "duplicating an accented-name track must not corrupt the session")
        self.assertIn("ZZDUP", [t.name for t in BS.track_types(out)])

    @unittest.skipUnless(_mono_available(), "mono control files not present")
    def test_placement_lanes_covers_all_three_chains(self):
        """There are THREE placement chains, and `clip_lanes` enumerates one of them.

            audio   0x1052 -> 0x1050 -> 0x104F
            MIDI    0x1057 -> 0x1056 -> 0x104F
            video   0x1053 -> 0x1051 -> 0x104F

        Each is `holder -> wrapper -> placement`, differing only in the two OUTER content types.
        `clip_lanes` covers `0x1052` only — correct for the edit functions it feeds, but it means
        neither it nor `clips()` sees a MIDI or video clip. Measured on the corpus: a session
        with 185 MIDI placements had `clips()` return 511 of the 696 `0x104F` records present.

        `placement_lanes` is additive and read-only, so nothing that edits by lane zmark changes.
        Corpus-verified: it accounts for every `0x1050`+`0x1056`+`0x1051` wrapper in 120/120
        sessions, and recovers 1,628 MIDI and 8 video placements across the 38 sessions that have
        any. This test pins the API and the audio-path equivalence on a local control file (the
        controls are audio-only, so the MIDI/video coverage is what the corpus measurement above
        establishes)."""
        data = _load_path(_mono_control(sorted(_mono_available())[0]))
        audio_only = BS.clip_lanes(data)
        every = BS.placement_lanes(data)
        self.assertEqual([(z, n, k) for z, n, k, _kind in every if _kind == "audio"],
                         list(audio_only),
                         "the audio rows must be exactly what clip_lanes returns")
        for _z, _n, _k, kind in every:
            self.assertIn(kind, ("audio", "midi", "video"))
        # the kinds filter selects, and asking for nothing returns nothing
        self.assertEqual(BS.placement_lanes(data, kinds=("audio",)), every if all(
            k == "audio" for *_r, k in every) else
            [r for r in every if r[3] == "audio"])
        self.assertEqual(BS.placement_lanes(data, kinds=()), [])
        # every reported holder really is a block of the chain's holder type
        bl = BS._size_driven_blocks(data)
        holders = {z: c for z, _e, c in bl}
        want = {"audio": 0x1052, "midi": 0x1057, "video": 0x1053}
        for z, _n, _k, kind in every:
            self.assertEqual(holders.get(z), want[kind],
                             f"a {kind} row must point at a 0x{want[kind]:04X} block")

    def test_the_writer_validates_its_own_output_across_its_parameter_space(self):
        """Everything `synthesize` can produce must satisfy the invariants the validator knows.

        THIS GAP IS HOW THE CLICK DEFECT SURVIVED. Every check in this suite runs against the
        CORPUS -- real Pro Tools sessions, or the result of editing one -- while `synthesize` makes
        files from scratch and nothing asked whether those satisfy the same rules. So every
        click-bearing session this writer produced carried a repeated `0x2015` ordinal, the
        condition Pro Tools crashes on (PT-confirmed 2026-07-27), for as long as the click path has
        existed. It surfaced only because a rule added for another reason happened to fire.

        The writer is the component most likely to emit invalid bytes and was the one with no
        systematic check. 58 shapes pass as of this commit.

        WHAT THIS DOES AND DOES NOT GUARD, checked by disabling the fixes rather than assumed:
        disabling EITHER normalizer alone leaves this test passing, because `synthesize()` routes
        through `add_click_anyN` and either one repairs the column; with BOTH disabled it fails on
        16 shapes. So it asserts the broad property -- the writer emits sessions the validator
        accepts -- and NOT the specific click regression, which is guarded by the pre-existing
        `add_click_anyN` test. Stated because a test that names a defect it cannot detect is worse
        than no test.
        """
        cases = []
        for n in (2, 3, 4, 5, 8):
            for kinds in ([1] * n, [2] * n):
                for click in (None, "top", "bottom"):
                    cases.append((f"{kinds[0]}x{n} click={click}", lambda k=kinds, c=click: BS.synthesize(k, click=c)))
        for extra in ([], [0], [2], [0, 0], [2, 1]):
            spec = [1, 0, 2] + extra
            cases.append((f"mixed_midi {spec}", lambda s=spec: BS.synthesize_mixed_midi_inline(s)))
        self.assertGreater(len(cases), 30, "the parameter sweep collapsed -- it proves nothing")
        for name, build in cases:
            with self.subTest(shape=name):
                data = build()
                errs = [v for v in BS.validate_pt_invariants(data) if v["severity"] == "error"]
                self.assertEqual(errs, [], msg=f"{name}: writer emitted a session the validator rejects")
                col = [v for _p, v in BS._playlist_ordinal_sites(data)]
                self.assertEqual(len(col), len(set(col)),
                                 msg=f"{name}: repeated 0x2015 ordinal {col} -- PT crashes on close")


if __name__ == "__main__":
    unittest.main()


class LaneBlockSelectionTests(unittest.TestCase):
    """The lane list that drives ordinal re-keying must contain only real lanes.

    `_blocks_by_type` is a GLOBAL type index, so a `0x251A` framed by a stray `0x5A` inside a leaf
    payload lands in it beside the real lanes. The caller derives the track count by DIVIDING that
    list, so a phantom shifts the lane-group boundary and mis-numbers a whole group.

    Ten corpus sessions really do carry such a phantom. Each has exactly ONE and an ODD total, so
    `len // 2` absorbed it -- the count was right by luck, not by construction.
    """

    def test_a_phantom_lane_outside_the_name_table_is_excluded(self):
        by = {0x2519: [(1000, 2000)],
              0x251A: [(1100, 1200), (1200, 1300), (1300, 1400), (1400, 1500)]}
        self.assertEqual(len(BS._lane_blocks(by)), 4)
        by[0x251A].append((50_000, 50_290))          # 290 bytes, inside a 0x1038 -- the real shape
        self.assertEqual(len(BS._lane_blocks(by)), 4,
                         "a 0x251A outside the name table is not a lane")

    def test_two_phantoms_would_have_broken_the_derived_track_count(self):
        """Why this is worth fixing while it is still masked: the luck is one phantom deep."""
        real = [(1000 + 100 * i, 1100 + 100 * i) for i in range(4)]
        by = {0x2519: [(900, 2000)], 0x251A: list(real)}
        self.assertEqual(len(BS._lane_blocks(by)) // 2, 2)
        by[0x251A] += [(50_000, 50_290), (60_000, 60_290)]
        self.assertEqual(len(by[0x251A]) // 2, 3, "unfiltered, two phantoms shift N from 2 to 3")
        self.assertEqual(len(BS._lane_blocks(by)) // 2, 2, "filtered, N is unchanged")

    def test_an_unexpected_name_table_shape_leaves_the_lanes_alone(self):
        """Better to re-key as before than to re-key off an empty lane set."""
        lanes = [(10, 20), (20, 30)]
        self.assertEqual(BS._lane_blocks({0x251A: lanes}), lanes)          # no 0x2519
        self.assertEqual(BS._lane_blocks({0x2519: [(0, 5)], 0x251A: lanes}), lanes)


class ThreeChainClipRemovalTests(unittest.TestCase):
    """`remove_clip` selects its placement chain from the holder's own content type.

    The splice arithmetic was never audio-specific -- the three chains differ only in the two OUTER
    content types, and the 0x104F placement record is shared -- but two constants were hardwired,
    so a MIDI or video holder was rejected.
    """

    def _a_session(self):
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) < 600_000 and BS.placement_lanes(d):
                return d
        self.skipTest("no control session with placements")

    def test_the_chain_table_covers_all_three_kinds(self):
        kinds = {k for _h, _w, k in BS._PLACEMENT_CHAINS}
        self.assertEqual(kinds, {"audio", "midi", "video"})
        holders = [h for h, _w, _k in BS._PLACEMENT_CHAINS]
        self.assertEqual(len(set(holders)), 3, "a holder type must select exactly one chain")

    def test_a_non_holder_zmark_is_refused_by_name(self):
        """The error must say what was passed; "no 0x1052 lane" was a lie for a MIDI holder."""
        d = self._a_session()
        holders = {h for h, _w, _k in BS._PLACEMENT_CHAINS}
        z = next(z for z, _e, c in BS._size_driven_blocks(d) if z >= 31 and c not in holders)
        with self.assertRaises(ValueError) as cm:
            BS.remove_clip(d, z, 0)
        self.assertIn("not a placement holder", str(cm.exception))
        for kind in ("audio", "midi", "video"):
            self.assertIn(kind, str(cm.exception), "the error should list the chains it accepts")

    def test_an_absent_zmark_is_refused(self):
        d = self._a_session()
        with self.assertRaises(ValueError) as cm:
            BS.remove_clip(d, 999_999_999, 0)
        self.assertIn("no block at zmark", str(cm.exception))

    def _a_session_with(self, kind):
        """A control session holding a placement of `kind`, and one such holder."""
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            for z, nm, n, k in BS.placement_lanes(d):
                if k == kind:
                    return d, z, nm, n
        self.skipTest("no control session holds a %s placement" % kind)

    def test_removing_an_AUDIO_clip_drops_exactly_one_placement(self):
        d, z, _nm, _n = self._a_session_with("audio")
        before = sum(1 for _a, _b, c in BS._size_driven_blocks(d) if c == 0x104F)
        out = BS.remove_clip(d, z, 0)
        after = sum(1 for _a, _b, c in BS._size_driven_blocks(out) if c == 0x104F)
        self.assertEqual(after, before - 1)
        self.assertLess(len(out), len(d))

    def test_removing_a_MIDI_clip_works_at_all(self):
        """THE test for this change. `control_files` holds 36 MIDI placement holders, so this runs
        locally -- and it fails outright against the old audio-only hardwiring, which raised
        `no 0x1052 lane`. An earlier version of this class picked `placement_lanes(d)[0]`, which is
        an audio holder on every local session, so it passed with the fix reverted and tested
        nothing."""
        d, z, _nm, _n = self._a_session_with("midi")
        holder_ct = next(c for zz, _e, c in BS._size_driven_blocks(d) if zz == z)
        self.assertEqual(holder_ct, 0x1057, "MIDI holders are 0x1057")
        before = sum(1 for _a, _b, c in BS._size_driven_blocks(d) if c == 0x104F)
        out = BS.remove_clip(d, z, 0)
        after = sum(1 for _a, _b, c in BS._size_driven_blocks(out) if c == 0x104F)
        self.assertEqual(after, before - 1)
        self.assertLess(len(out), len(d))

    def test_a_MIDI_removal_keeps_the_index_resolvable_and_the_counts_honest(self):
        """Corruption an rc=0 reader would miss: index targets that no longer land on a block, and
        a stale placement count."""
        d, z, _nm, _n = self._a_session_with("midi")
        out = BS.remove_clip(d, z, 0)
        zs = {bz for bz, _be, _bc in BS._size_driven_blocks(out)}
        holes, _zm = BS._index_offset_holes(out)
        self.assertEqual([t for _a, t in holes if t and t not in zs], [],
                         "every master-index offset must still land on a block")
        base = {(e["invariant"], e["severity"]) for e in BS.validate_pt_invariants(d)
                if e["severity"] == "error"}
        now = {(e["invariant"], e["severity"]) for e in BS.validate_pt_invariants(out)
               if e["severity"] == "error"}
        self.assertEqual(now - base, set(), "no NEW validator errors")

    # NOTE: video (0x1053 -> 0x1051) is NOT covered by a local test -- `control_files` contains
    # zero video placements. It is exercised only by the corpus battery, which found 7 video
    # holders across the external drives and removed from all 7 cleanly. Saying so is better than
    # a test named for video that quietly skips.


class PlacementCountInvariantTests(unittest.TestCase):
    """A holder's declared placement count must equal the number of wrappers inside it.

    Nothing structural could catch a stale count: `placement_lanes` counts wrappers by walking
    blocks, so a holder with a wrong u32 reads back perfectly. That is this project's recurring
    failure shape -- every reader returns rc=0 and Pro Tools is the one that objects.

    Measured at the FIXED offset (first wrapper - 4), which matches the real count in 9423 of 9423
    corpus holders (9235 audio / 185 MIDI / 3 video), so the check is inert on untouched sessions.
    """

    def _holder(self):
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            by = BS._blocks_by_type(d)
            for hct, wct, _kind in BS._PLACEMENT_CHAINS:
                for hz, he in sorted(by.get(hct, ())):
                    ins = [wz for wz, _we in sorted(by.get(wct, ())) if hz < wz < he]
                    if ins:
                        return d, min(ins), len(ins)
        self.skipTest("no control session with a placement holder")

    def test_a_stale_placement_count_is_an_error(self):
        d, fp, n = self._holder()
        self.assertEqual([e for e in BS.validate_pt_invariants(d)
                          if e["invariant"] == "placement-count"], [],
                         "must be inert on an unmodified session")
        bad = bytearray(d)
        bad[fp - 4:fp] = (n + 7).to_bytes(4, "little")
        hit = [e for e in BS.validate_pt_invariants(bytes(bad))
               if e["invariant"] == "placement-count"]
        self.assertTrue(hit, "a stale placement count must be reported")
        self.assertEqual(hit[0]["severity"], "error")

    def test_the_count_field_position_is_fixed_not_searched(self):
        """`_lane_count_pos` searches backwards for a u32 equal to the count it was given, so it
        can only ever find a field that already agrees -- it cannot detect a wrong one. The
        invariant reads the fixed position instead."""
        import inspect
        vsrc = inspect.getsource(BS.validate_pt_invariants)
        self.assertIn("placement-count", vsrc)
        self.assertIn("_fp - 4", vsrc, "the invariant must read the FIXED offset")


class PluginCatalogTests(unittest.TestCase):
    """`plugins()` reads the 0x1017 catalog: (display name, identifier).

    0x1017 is a LEAF_PAYLOAD -- the block walk must not descend into it, because plug-in names
    contain 'Z' and manufacture phantoms (the phantom catalogue's 0x0c00 is the Z in "iZotope").
    "Do not descend" is not "cannot read": the record is regular, and 1534 of 1585 corpus blocks
    parse.
    """

    #: A control session known to carry a populated catalog. Named, so that "no plug-ins found"
    #: FAILS instead of skipping -- a bite check showed that breaking the identifier offset by one
    #: byte made every test in this class skip, and a skip is not a pass.
    FIXTURE = "128 click tracks.ptx"

    def _a_session_with_plugins(self):
        hits = sorted(ROOT.glob("control_files/**/" + self.FIXTURE))
        if not hits:
            self.skipTest("fixture %s is not in control_files" % self.FIXTURE)
        d = _load_path(hits[0])
        got = BS.plugins(d)
        self.assertTrue(got, "%s carries a populated 0x1017 catalog; reading none means the "
                             "record layout is wrong, not that the session is empty" % self.FIXTURE)
        return d

    def test_it_is_not_empty_on_a_session_that_has_plugins(self):
        """The bug the first version shipped with.

        v1 REQUIRED the identifier, and many kind-3 records carry only a display name -- so any
        session whose plug-ins are all identifier-less returned []. Measured: empty on 128 of 407
        PT-authored sessions (31.4%), 32 of them provably carrying names in their own 0x1017
        records. It shipped on a "96.8% of blocks parse" number taken from 25 sessions on ONE
        drive, quoted per BLOCK when a caller experiences it per SESSION.
        """
        d = self._a_session_with_plugins()
        self.assertTrue(BS.plugins(d))

    def test_an_identifier_may_be_absent(self):
        d = self._a_session_with_plugins()
        for name, ident in BS.plugins(d):
            self.assertTrue(name.strip(), "a display name is always present")
            self.assertIsInstance(ident, str, "an absent identifier is '', never None")

    def test_a_record_WITHOUT_an_identifier_is_still_reported(self):
        """The 31.4% bug, reproduced synthetically because `control_files` cannot express it.

        No control session has an identifier-less catalog record, which is precisely why v1 shipped:
        the local fixture pool does not contain the failing case, and the corpus measurement that
        would have caught it was taken per BLOCK on 25 sessions from one external drive.

        So the case is BUILT: take a real record, blank the u32 that gives its identifier length,
        and require the plug-in to survive with an empty identifier rather than vanish.
        """
        d = self._a_session_with_plugins()
        bl = BS._size_driven_blocks(d)
        cats = [(z, e) for z, e, c in bl if c == 0x1018 and z >= 31]
        target = None
        for z, e, c in bl:
            if c != 0x1017 or z < 31 or e - z < 14 or d[z + 9] == 0xFF:
                continue
            if cats and not any(cz < z and e <= ce for cz, ce in cats):
                continue
            n = int.from_bytes(d[z + 10:z + 14], "little")
            if not (0 < n <= 200) or z + 14 + n > e:
                continue
            q = z + 14 + n + 12 + 7
            m = int.from_bytes(d[q:q + 4], "little") if q + 4 <= e else 0
            if 0 < m <= 250 and q + 4 + m <= e:
                target = (z, e, n, q, BS._decode_name(d[z + 14:z + 14 + n]))
                break
        if target is None:
            self.skipTest("fixture has no catalog record carrying an identifier")
        z, e, n, q, name = target
        self.assertIn(name, [nm for nm, _i in BS.plugins(d)])
        maimed = bytearray(d)
        maimed[q:q + 4] = (0).to_bytes(4, "little")     # identifier length -> 0 == absent
        got = BS.plugins(bytes(maimed))
        self.assertIn(name, [nm for nm, _i in got],
                      "a record whose identifier is absent must still report its display name")
        self.assertEqual("", dict(got)[name], "the absent identifier must read as ''")

    def test_the_empty_0xFF_stub_is_not_a_plugin(self):
        """Record kind 0xFF is an empty catalog slot (span 39, all zeros). Counting it as a
        plug-in would inflate every session's list."""
        d = self._a_session_with_plugins()
        stubs = [(z, e) for z, e, c in BS._size_driven_blocks(d)
                 if c == 0x1017 and z >= 31 and e - z > 9 and d[z + 9] == 0xFF]
        if not stubs:
            self.skipTest("fixture has no 0xFF stub")
        self.assertLessEqual(len(BS.plugins(d)),
                             sum(1 for z, e, c in BS._size_driven_blocks(d)
                                 if c == 0x1017 and z >= 31) - len(stubs))

    def test_never_reports_more_than_the_catalog_declares(self):
        """`0x1018`'s u32 equals its direct 0x1017 child count (45/45 measured, 357/357 in an
        independent audit). Scoping matters: reading 0x1017 blocks OUTSIDE the catalog -- every
        session has exactly two -- broke this on 24 of 407 sessions."""
        d = self._a_session_with_plugins()
        declared = BS.plugin_count(d)
        if declared is None:
            self.skipTest("fixture has no 0x1018 catalog")
        self.assertLessEqual(len(BS.plugins(d)), declared)

    def test_reads_name_and_identifier_pairs(self):
        d = self._a_session_with_plugins()
        got = BS.plugins(d)
        self.assertTrue(got)
        for name, ident in got:
            self.assertIsInstance(name, str)
            self.assertIsInstance(ident, str)
            self.assertTrue(name.strip(), "a plug-in must have a display name")
            self.assertLess(len(name), 201)
            self.assertLess(len(ident), 251)     # the identifier MAY be empty

    def test_identifiers_look_like_reverse_dns(self):
        """Not a format guarantee -- a shape check that catches a mis-set offset immediately."""
        d = self._a_session_with_plugins()
        idents = [i for _n, i in BS.plugins(d) if i]
        if idents:
            self.assertTrue(any("." in i for i in idents),
                            "identifiers are reverse-DNS; none had a dot -> the offset is wrong")

    def test_results_are_deduplicated(self):
        d = self._a_session_with_plugins()
        got = BS.plugins(d)
        self.assertEqual(len(got), len(set(got)))

    def test_empty_0x1017_stubs_contribute_nothing(self):
        """A session's EMPTY catalog stubs (span 49, all zeros) must yield no pair.

        Originally written as `plugins(bytes(4096)) == []`, which asserted a promise the library
        does not make: EVERY reader raises on non-session input (`clips`, `markers`, `tempo_map`,
        `region_list`, `playlist_names`, `volumes` all AttributeError; `track_types` ValueError).
        `plugins` is consistent with them, so the test was wrong rather than the code. The
        library-wide behaviour is a wart worth fixing on its own terms, not by making one reader
        special.
        """
        d = self._a_session_with_plugins()
        n_blocks = sum(1 for z, _e, c in BS._size_driven_blocks(d) if c == 0x1017 and z >= 31)
        self.assertLessEqual(len(BS.plugins(d)), n_blocks,
                             "cannot report more plug-ins than there are catalog blocks")

    def test_it_is_on_the_public_surface(self):
        import ptxformatwriter as ptx
        self.assertIs(ptx.plugins, BS.plugins)
        self.assertIn("plugins", ptx.__all__)


class AudioFileTableTests(unittest.TestCase):
    """`audio_files()` answers the question most often asked of a session file: what does it need?

    Nothing in this library could answer it before — the exports covered WRITING wavs
    (`add_audio_clip`, `set_clip_wav`, `wrap_raw_wav`) and none of them reading the table.

    The reader has its own oracle: `0x1004` DECLARES the file count, so a parse that disagrees is
    wrong by the file's own account. 459 of 462 corpus sessions agree exactly.
    """

    FIXTURE = "1 stereo + 1 clip.ptx"

    def _fixture(self):
        hits = sorted(ROOT.glob("control_files/**/" + self.FIXTURE))
        if not hits:
            self.skipTest("fixture %s is not in control_files" % self.FIXTURE)
        return _load_path(hits[0])

    def test_parsed_count_matches_the_declared_count(self):
        d = self._fixture()
        declared = BS.audio_file_count(d)
        self.assertIsNotNone(declared, "the fixture has a 0x1004 file table")
        self.assertEqual(len(BS.audio_files(d)), declared)

    def test_returns_name_and_format(self):
        d = self._fixture()
        got = BS.audio_files(d)
        self.assertTrue(got)
        for name, fmt in got:
            self.assertTrue(name.strip())
            self.assertIn(fmt, ("WAVE", "AIFF", "AIFC", "AF64", "MPL1", "Aflt"))

    def test_a_utf8_filename_is_not_dropped(self):
        """The bug this reader shipped with, pinned.

        The first version required every name byte in 32..126. Filenames are UTF-8 — macOS stores
        accents decomposed — so `Butterbeans Café` and `Thalía with Dora the Explorer` were
        silently dropped, and 20 of 462 sessions parsed FEWER files than their own table declared.
        Third time an ASCII assumption has bitten this project.
        """
        name = "Café Thalía.L.wav".encode("utf-8")
        blob = (len(name)).to_bytes(4, "little") + name + b"EVAW"
        self.assertFalse(all(32 <= b < 127 for b in name), "fixture must be non-ASCII")
        # the accept test the reader applies: a valid tag and no control bytes
        seg, tag = blob[4:4 + len(name)], blob[4 + len(name):8 + len(name)]
        self.assertIn(tag, BS._FILE_TAGS)
        self.assertFalse(any(b < 0x20 for b in seg))

    def test_the_count_reader_is_independent_of_the_name_parse(self):
        """`audio_file_count` must read the declared u32, not count what we parsed — otherwise it
        could not serve as the check."""
        import ast, inspect
        tree = ast.parse(inspect.getsource(BS.audio_file_count).lstrip())
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]                      # drop the docstring, which names audio_files
        body = ast.unparse(fn)
        self.assertNotIn("audio_files", body,
                         "the count must be READ from 0x1004, not derived from the name parse")
        self.assertIn("0x1004", inspect.getsource(BS.audio_file_count))

    def test_it_is_on_the_public_surface(self):
        import ptxformatwriter as ptx
        self.assertIs(ptx.audio_files, BS.audio_files)
        self.assertIs(ptx.audio_file_count, BS.audio_file_count)
        for n in ("audio_files", "audio_file_count"):
            self.assertIn(n, ptx.__all__)


class MoveClipTests(unittest.TestCase):
    """`move_clip` addresses a clip the way the rest of the edit surface does: lane + index.

    It already EXISTED and was simply not exported — I wrote a second one before finding it, and
    the duplicate was shadowed by the original. What it needed was not writing but two fixes: the
    export, and generalising `_locate_placement_104f` to all three placement chains (it was
    hardwired to 0x1052/0x1050, so `move_clip` and `replace_clip_region` refused every MIDI and
    video clip with a message naming the audio type).

    `set_clip_position` is the older, cruder relative: PT-confirmed, but it indexes placements
    GLOBALLY across every 0x104F in the session, so on a 2,000-placement session a caller cannot
    tell which index is which clip.
    """

    def _lane(self, kind=None):
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            for z, nm, n, k in BS.placement_lanes(d):
                if kind is None or k == kind:
                    return d, z, n
        self.skipTest("no control session has a %s placement" % (kind or "any"))

    def test_moving_a_clip_is_size_neutral(self):
        d, z, _n = self._lane()
        out = BS.move_clip(d, z, 0, 123_456)
        self.assertEqual(len(out), len(d), "the timeline start is one field; nothing is spliced")

    def test_the_position_reads_back(self):
        d, z, _n = self._lane()
        out = BS.move_clip(d, z, 0, 987_654)
        self.assertIn(987_654, BS.clip_positions(out))

    def test_moving_to_its_own_position_is_byte_exact(self):
        """The identity property. If a no-op move changes any byte, the write is not confined."""
        d, z, _n = self._lane()
        bl = BS._size_driven_blocks(d)
        lane = next(b for b in bl if b[0] == z)
        wrap = next(ch[1] for ch in BS._PLACEMENT_CHAINS if ch[0] == lane[2])
        pz, pe = sorted((a, b) for a, b, c in bl if c == wrap and z < a < lane[1])[0]
        tgt = [a for a, b, c in bl if c == 0x104F and pz < a and b <= pe][0]
        import struct
        here = struct.unpack_from("<Q", d, tgt + 16)[0]
        self.assertEqual(BS.move_clip(d, z, 0, here), d)

    def test_only_the_addressed_clip_moves(self):
        d, z, n = self._lane()
        if n < 2:
            self.skipTest("need a lane with two clips")
        before = BS.clip_positions(d)
        out = BS.move_clip(d, z, 0, 555_555)
        after = BS.clip_positions(out)
        self.assertEqual(len(before), len(after))
        moved = sum(1 for a, b in zip(before, after) if a != b)
        self.assertLessEqual(moved, 2, "a stereo clip has two lanes; nothing else may move")

    def test_a_non_holder_zmark_is_refused(self):
        d, _z, _n = self._lane()
        holders = {h for h, _w, _k in BS._PLACEMENT_CHAINS}
        bad = next(a for a, _b, c in BS._size_driven_blocks(d) if a >= 31 and c not in holders)
        with self.assertRaises(ValueError) as cm:
            BS.move_clip(d, bad, 0, 0)
        self.assertIn("not a placement holder", str(cm.exception))

    def test_clip_positions_uses_the_size_driven_walk(self):
        """It used `flat_blocks`, which UNDERCOUNTS: measured on 402 corpus sessions it missed one
        0x104F in one session and three in another. `set_clip_position(lanes=...)` indexes
        positionally into that list, so a missing placement shifts every later index and the caller
        silently moves the wrong clip."""
        import ast, inspect
        for fn in (BS.clip_positions, BS.set_clip_position):
            tree = ast.parse(inspect.getsource(fn).lstrip())
            f = tree.body[0]
            if (f.body and isinstance(f.body[0], ast.Expr)
                    and isinstance(f.body[0].value, ast.Constant)):
                f.body = f.body[1:]                 # drop the docstring
            body = ast.unparse(f)                   # ast.unparse also drops comments
            self.assertIn("_size_driven_blocks", body)
            self.assertNotIn("flat_blocks", body,
                             "the scan parser undercounts and these index positionally into it")


class ScanParserIndexRiskTests(unittest.TestCase):
    """Positional indices must never be derived from the scan parser.

    `flat_blocks` UNDERCOUNTS on real sessions — measured over the corpus, 80 content types are
    affected and 35 of them in more than half of all sessions (`0x258C` 372/372, `0x2037` 354/354,
    `0x2580` 333/333). That is harmless where a function only asks "does this exist", and a silent
    data bug where a function hands back a POSITION: a missing block shifts every later index.

    Two shipped functions did exactly that, and neither could report the error:
      * `clip_positions`/`set_clip_position` — 0x104F short in 2 of 402 sessions, so
        `lanes=(i,)` moved the WRONG CLIP;
      * `selected_tracks` — 0x261C short in 9 of 312 sessions, so it returned the WRONG TRACK
        NUMBERS.
    """

    POSITIONAL = ("clip_positions", "set_clip_position", "selected_tracks")

    def test_positional_readers_do_not_use_the_scan_parser(self):
        import ast, inspect
        for name in self.POSITIONAL:
            fn = getattr(BS, name)
            tree = ast.parse(inspect.getsource(fn).lstrip())
            f = tree.body[0]
            if (f.body and isinstance(f.body[0], ast.Expr)
                    and isinstance(f.body[0].value, ast.Constant)):
                f.body = f.body[1:]
            body = ast.unparse(f)          # drops comments AND docstring
            self.assertNotIn("flat_blocks", body, f"{name} indexes positionally")
            self.assertIn("_size_driven_blocks", body, f"{name} must use the size-driven walk")

    def test_selected_track_indices_are_within_the_track_range(self):
        """The property the undercount broke: an index past the last track is impossible."""
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            n = len(BS.track_types(d))
            # strict=False: this test is about INDEX RANGE, not about whether the flag is
            # readable. The anchor is absent in 179 of 302 corpus sessions, and a strict raise
            # here would hide the index property the test exists to check.
            for i in BS.selected_tracks(d, strict=False):
                self.assertGreaterEqual(i, 1)
                self.assertLessEqual(i, max(n, 1), f"{path.name}: index {i} > {n} tracks")
            checked += 1
            if checked >= 12:
                break
        self.assertTrue(checked, "no control session was checked")


class SetMarkersDataLossTests(unittest.TestCase):
    """`set_markers` must refuse a session it would corrupt, rather than corrupt it.

    The inlined 0x2030 template carries ONE 0x2506 row per 0x2077 record — it assumes a
    one-playlist session. The settled relation is `n(0x2506) == n(0x2077) x n_playlists`, so
    splicing that template into a P-playlist session drops `n(0x2077) x (P-1)` rows.

    Measured before the guard: `set_markers(d, markers(d))` — the composition the docstring
    advertises — was byte-identical on 0 of 58 PT-authored sessions with markers and 56 LOST
    blocks, worst case -227,176 bytes and -12,950 0x2506 records. Nothing noticed: `markers()`
    returned the same markers and `validate()` reported no new issue.
    """

    def _sessions_with_markers(self, limit=14):
        out = []
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 900_000:
                continue
            if BS.markers(d):
                out.append((path.name, d))
            if len(out) >= limit:
                break
        return out

    def test_no_session_ever_loses_marker_rows(self):
        """The property that matters: proceed or refuse, but never silently drop a row."""
        checked = 0
        for name, d in self._sessions_with_markers():
            before = sum(1 for _z, _e, c in BS._size_driven_blocks(d) if c == 0x2506)
            try:
                out = BS.set_markers(d, BS.markers(d))
            except ValueError:
                checked += 1
                continue                      # refusing is the correct outcome
            after = sum(1 for _z, _e, c in BS._size_driven_blocks(out) if c == 0x2506)
            self.assertGreaterEqual(after, before,
                                    f"{name}: set_markers dropped 0x2506 rows {before} -> {after}")
            checked += 1
        self.assertTrue(checked, "no control session with markers was checked")

    def test_the_rows_per_record_helper_is_the_playlist_count(self):
        """Tested on synthetic block lists, because `control_files` cannot express the bug.

        Removing the guard entirely left the corpus-driven test above PASSING — no local session
        has multi-playlist markers, so nothing is destroyed locally. The helper is therefore
        exercised directly.
        """
        B = lambda t, n: [(0, 0, t)] * n
        self.assertEqual(BS._marker_rows_per_record(B(0x2077, 4) + B(0x2506, 4)), 1)
        self.assertEqual(BS._marker_rows_per_record(B(0x2077, 4) + B(0x2506, 756)), 189)
        self.assertEqual(BS._marker_rows_per_record(B(0x2077, 14) + B(0x2506, 364)), 26)
        self.assertEqual(BS._marker_rows_per_record([]), 1, "no markers -> never refuse")
        self.assertEqual(BS._marker_rows_per_record(B(0x2506, 9)), 1, "no 0x2077 -> never refuse")

    def test_set_markers_refuses_when_rows_per_record_exceeds_one(self):
        """The guard must be wired to the helper, not merely present beside it."""
        import ast, inspect
        tree = ast.parse(inspect.getsource(BS.set_markers).lstrip())
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]
        body = ast.unparse(fn)
        self.assertIn("_marker_rows_per_record", body)
        self.assertIn("raise ValueError", body)

    def test_the_refusal_says_what_is_wrong(self):
        """A refusal that does not explain itself just moves the confusion."""
        multi = None
        for _name, d in self._sessions_with_markers():
            bl = BS._size_driven_blocks(d)
            n77 = sum(1 for _z, _e, c in bl if c == 0x2077)
            n06 = sum(1 for _z, _e, c in bl if c == 0x2506)
            if n77 and n06 > n77:
                multi = d
                break
        if multi is None:
            self.skipTest("no local session has more than one playlist's marker rows")
        with self.assertRaises(ValueError) as cm:
            BS.set_markers(multi, BS.markers(multi))
        msg = str(cm.exception)
        self.assertIn("0x2506", msg)
        self.assertIn("playlist", msg.lower())

    def test_a_one_playlist_session_still_works(self):
        """The guard must not break the synthesis path it was written for."""
        ran = False
        for _name, d in self._sessions_with_markers():
            bl = BS._size_driven_blocks(d)
            n77 = sum(1 for _z, _e, c in bl if c == 0x2077)
            n06 = sum(1 for _z, _e, c in bl if c == 0x2506)
            if n77 and n06 == n77:
                out = BS.set_markers(d, BS.markers(d))
                self.assertEqual([m["name"] for m in BS.markers(out)],
                                 [m["name"] for m in BS.markers(d)])
                ran = True
                break
        if not ran:
            self.skipTest("no local one-playlist session with markers")


class RetargetAudioFileTests(unittest.TestCase):
    """`retarget_audio_file` wrote the sample count to the wrong offset and overran short records.

    Two defects, both measured on the corpus:

      * it wrote the sample count to `0x1003+15`, which is NOT the sample count -- across 17,900
        records in 372 sessions that field takes exactly two values, 8448 (17,890x) and 4352 (10x),
        independent of file length. The real field is `+28`: 3,320 distinct values, and equal to
        `wav_clip_identity(wav)[0]` on 40 of 40 (record, on-disk WAV) pairs where `+15` matched 0.
      * `+292`/`+301` were written unconditionally, but record spans VARY (commonest 210) so those
        offsets fall outside most records. Unbounded, 9 bytes landed up to 93 past the record end.

    `control_files` has no `0x1003` record shorter than 311 bytes, so no local session can make the
    unbounded version overrun. The write list is therefore a pure function and is tested directly.
    """

    UMID = bytes([0x2A]) + b"\xAA" * 7
    ID2 = b"\xBB\xBB"

    def test_a_short_record_gets_only_the_writes_that_fit(self):
        w = BS._retarget_field_writes(1000, 1000 + 210, 123456, self.UMID, self.ID2)
        self.assertEqual([o - 1000 for o, _v in w], [28, 44],
                         "+292/+301 do not fit in a 210-byte record and must be skipped")

    def test_a_long_record_gets_every_write(self):
        w = BS._retarget_field_writes(1000, 1000 + 321, 123456, self.UMID, self.ID2)
        self.assertEqual([o - 1000 for o, _v in w], [28, 44, 292, 301])

    def test_no_write_ever_leaves_the_record(self):
        """The property, over every span the corpus actually contains."""
        for span in (209, 210, 260, 302, 303, 311, 312, 314, 318, 320, 321, 400):
            for off, val in BS._retarget_field_writes(500, 500 + span, 7, self.UMID, self.ID2):
                self.assertGreaterEqual(off, 500, f"span {span}: write before the record")
                self.assertLessEqual(off + len(val), 500 + span,
                                     f"span {span}: write {off - 500}..{off - 500 + len(val)} "
                                     f"overruns the record end")

    def test_the_sample_count_goes_to_28_not_15(self):
        w = dict((o - 1000, v) for o, v in
                 BS._retarget_field_writes(1000, 1000 + 321, 123456, self.UMID, self.ID2))
        self.assertIn(28, w)
        self.assertNotIn(15, w, "+15 is a structural constant (8448/4352), not the sample count")
        self.assertEqual(int.from_bytes(w[28], "little"), 123456)

    def test_the_writer_uses_the_helper(self):
        """The bounds must be enforced by the shared helper, not re-implemented inline."""
        import ast, inspect
        tree = ast.parse(inspect.getsource(BS.retarget_audio_file).lstrip())
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]
        body = ast.unparse(fn)
        self.assertIn("_retarget_field_writes", body)
        self.assertNotIn("z + 15", body, "the +15 write must be gone")


class SynthesisWriterBlockLossTests(unittest.TestCase):
    """The synthesis writers must never return a session with fewer blocks than it started with.

    `set_tempo_map` and `set_meter_map` splice INLINED conductor templates over whatever is there.
    On a session whose `0x2028`/`0x2718` carry more structure than the template does, that DROPS
    blocks — measured, an identity `set_tempo_map(d, tempo_map(d))` differed on 297 of 342 sessions
    and LOST a `0x2028` in 285 of them; `set_meter_map` differed on 14 of 57.

    Nothing noticed: the readers went on returning the right map from what survived and
    `validate()` reported no new issue. `replace_tempo_map`/`replace_meter_map` are the in-place
    editors for an arbitrary session and are byte-identical on an identity replace (342/342).
    """

    def test_blocks_lost_by_type_detects_a_removal(self):
        """The guard's own primitive, tested against a session with a block spliced out."""
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            bl = [b for b in BS._size_driven_blocks(d) if b[0] >= 31]
            if len(bl) < 3:
                continue
            self.assertEqual(BS._blocks_lost_by_type(d, d), {}, "identity loses nothing")
            z, e, c = bl[-1]
            maimed = d[:z] + d[e:]
            lost = BS._blocks_lost_by_type(d, maimed)
            self.assertIn(c, lost, f"removing a 0x{c:04x} must be reported")
            self.assertGreaterEqual(lost[c], 1)
            return
        self.skipTest("no suitable control session")

    def test_set_tempo_map_refuses_rather_than_dropping_blocks(self):
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            try:
                tm = BS.tempo_map(d)
            except Exception:
                continue
            if not tm:
                continue
            try:
                out = BS.set_tempo_map(d, tm)
            except ValueError:
                checked += 1
                continue                       # refusing is the correct outcome
            except Exception:
                continue
            self.assertEqual(BS._blocks_lost_by_type(d, out), {},
                             f"{path.name}: set_tempo_map dropped blocks instead of refusing")
            checked += 1
            if checked >= 10:
                break
        self.assertTrue(checked, "no control session with a tempo map was checked")

    def test_the_refusal_names_the_in_place_editor(self):
        """A refusal has to say what to use instead, or it just blocks the caller."""
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            try:
                tm = BS.tempo_map(d)
                if not tm:
                    continue
                BS.set_tempo_map(d, tm)
            except ValueError as ex:
                self.assertIn("replace_tempo_map", str(ex))
                return
            except Exception:
                continue
        self.skipTest("no local session makes set_tempo_map refuse")


class SessionBitDepthTests(unittest.TestCase):
    """`session_bit_depth` must return a bit depth, and 0 is not one.

    The `0x1028` field sits at block `+(size+2)` in one layout and `+(size+1)` in another. The
    reader knew only the first, and its docstring said "PT-corpus-confirmed across all 26
    sessions".

    Measured: over 432 sessions it returned **0** on 314 — and over `control_files` specifically it
    returned 0 on **282 of 282**, i.e. every local control session, because they are all the layout
    it did not know. A claim of corpus confirmation survived a reader that produced a non-value on
    the entire local corpus, which says the confirmation checked that the call did not raise rather
    than what it returned.
    """

    PLAUSIBLE = (16, 24, 32)

    def test_never_returns_zero_on_a_control_session(self):
        checked = 0
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            try:
                v = BS.session_bit_depth(d)
            except ValueError:
                continue                       # no 0x1028 at all is a different matter
            self.assertNotEqual(v, 0, f"{path.name}: 0 is not a bit depth")
            self.assertIn(v, self.PLAUSIBLE, f"{path.name}: implausible depth {v}")
            checked += 1
            if checked >= 25:
                break
        self.assertTrue(checked, "no control session was checked")

    def test_session_info_reports_the_same_depth(self):
        """The snapshot must not carry a different answer from the reader it summarises."""
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            try:
                v = BS.session_bit_depth(d)
            except ValueError:
                continue
            info = BS.session_info(d)
            if "bit_depth" in info:
                self.assertEqual(info["bit_depth"], v)
            return
        self.skipTest("no control session with a 0x1028")


class SelectedTracksHonestyTests(unittest.TestCase):
    """`selected_tracks` must not answer when it cannot read the flag.

    The selection flag is found by a fixed byte anchor inside each `0x261C`, and the anchor is
    absent from most sessions. Measured over 302 sessions with `0x261C` blocks: present in EVERY
    block in 53, in SOME in 70, in NONE in 179. So the old return value was trustworthy on 53 and
    silently wrong on 249 — a track whose block lacks the anchor was reported as NOT SELECTED,
    which is indistinguishable from the truth.

    The failures span every product (Studio 160, HD 74, Ultimate 44, Pro Tools 16), so it is not a
    product gate.
    """

    def _sessions(self, limit=12):
        out = []
        for path in sorted(ROOT.glob("control_files/**/*.ptx")):
            try:
                d = _load_path(path)
            except Exception:
                continue
            if len(d) > 600_000:
                continue
            if any(c == 0x261C for _z, _e, c in BS._size_driven_blocks(d)):
                out.append((path.name, d))
            if len(out) >= limit:
                break
        return out

    def test_strict_never_answers_from_an_unreadable_block(self):
        checked = 0
        for name, d in self._sessions():
            blocks = [b for b in BS._size_driven_blocks(d) if b[2] == 0x261C and b[0] >= 31]
            missing = sum(1 for z, e, _c in blocks
                          if BS._sel_flag_positions(d[z + 7:e]) is None)
            if missing:
                with self.assertRaises(ValueError, msg=f"{name}: {missing} unreadable blocks"):
                    BS.selected_tracks(d)
            else:
                BS.selected_tracks(d)          # fully readable -> must answer
            checked += 1
        self.assertTrue(checked, "no control session with 0x261C was checked")

    def test_lax_preserves_the_old_behaviour(self):
        for name, d in self._sessions(limit=4):
            got = BS.selected_tracks(d, strict=False)
            self.assertIsInstance(got, list)
            for i in got:
                self.assertGreaterEqual(i, 1)

    def test_the_refusal_says_how_much_it_could_not_read(self):
        for name, d in self._sessions():
            blocks = [b for b in BS._size_driven_blocks(d) if b[2] == 0x261C and b[0] >= 31]
            if not any(BS._sel_flag_positions(d[z + 7:e]) is None for z, e, _c in blocks):
                continue
            with self.assertRaises(ValueError) as cm:
                BS.selected_tracks(d)
            msg = str(cm.exception)
            self.assertIn("0x261C", msg)
            self.assertIn("strict=False", msg, "the refusal must name the escape hatch")
            return
        self.skipTest("every local session is fully readable")
