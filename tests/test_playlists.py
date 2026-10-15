"""Playlists: which playlists a track owns, which one it plays, and which no track owns.

The model (see the section comment at `body_synth._PLAYLIST_LIST_LAYOUT`): two registries -- audio
`0x1014`, MIDI `0x1057` -- and one OWNERSHIP LIST per audio/MIDI/instrument track container,
`[u32 active][count][count x u32 registry index]`, just before a fixed child. The reference sessions
live on the user's volumes; every test that needs one skips when it is not mounted, and the synth
checks run everywhere.
"""

from pathlib import Path
import re
import unittest

from ptxformatwriter.core import PTFFormat
from ptxformatwriter import body_synth as BS


def _load(p) -> bytes:
    f = PTFFormat()
    f.load(str(p), 48000)
    return f.unxored_data()


def _first(*paths):
    for p in paths:
        if Path(p).exists():
            return Path(p)
    return None


REMEMBER = _first("/Volumes/Dropbox SSD/Dropbox/MB_REMEMBER/REMEMBER/Session File Backups/"
                  "REMEMBER MAIN.bak.022.ptx")
CHRISTMAS_HACK = "/Volumes/Hack 2TB SSD Old/Users/jonkubis/Music/Pro Tools/Celebrate/" \
                 "CHRISTMAS IN MY HEART/Session File Backups/CHRISTMAS IN MY HEART.bak.%03d.ptx"
DWTS = _first("/Volumes/Mac 4TB SSD/Users/jonkubis/Music/Pro Tools/DWTS PT 2603/old/DWTS PT 2603 a plus.ptx")
SOCAL = _first("/Volumes/Dropbox SSD/Dropbox/PTL 2025 tracks and stuff/#1 Christmas Time In Southern "
               "California/Christmas Time In Southern California/Christmas Time In Southern California.ptx")
COUNT_ON_ME = _first("/Volumes/Dropbox SSD/Dropbox/RJG (1)/Pro Tools Sessions/Count On Me/"
                     "Count On Me Rehya's Input 110 BPM.ptx")


class PlaylistModelTests(unittest.TestCase):

    @unittest.skipUnless(REMEMBER, "REMEMBER MAIN backup not mounted")
    def test_track_types_is_one_entry_per_track(self):
        """285 playlist headers, 31 audio tracks. `track_types` used to return the 285."""
        d = _load(REMEMBER)
        self.assertEqual(len(BS.playlist_headers(d)), 285)
        tt = BS.track_types(d)
        sets = BS.track_playlist_sets(d)
        self.assertEqual(len(tt), 31)
        self.assertEqual([t.name for t in tt], [s.name for s in sets])
        self.assertEqual([t.offset for t in tt], [s.active.offset for s in sets])
        self.assertEqual(sum(len(s.playlists) for s in sets), 285, "every playlist is owned")
        by = {s.name: [p.name for p in s.playlists] for s in sets}
        # the active one is listed among its own, in the list's (name) order
        self.assertEqual(by["BC CHROUS.35"], ["BC CHROUS"] + ["BC CHROUS.%02d" % i for i in range(1, 36)])
        # a track renamed after its alternates were made: names cannot attribute these, the list can
        self.assertEqual(by["BC VERSE"], ["BC VERSE", "BC VOX"] + ["BC VOX.%02d" % i for i in range(1, 26)])
        self.assertEqual(by["BC PRE"][-1], "BC PRE")
        self.assertTrue(all(n.startswith("BC CHORUS") for n in by["BC PRE"][:-1]))
        # owned through the list although the GUID maps at the end of this 0x261C are empty
        self.assertEqual(by["MB PRE.01"], ["MB PRE", "MB PRE.01"])
        self.assertEqual(by["MB CHORUS.01"], ["MB CHORUS", "MB CHORUS.01"])

    @unittest.skipUnless(REMEMBER, "REMEMBER MAIN backup not mounted")
    def test_audio_playlist_lanes_are_its_channels_in_registry_order(self):
        """Each audio header names its lanes; here they are the cumulative channel count over the
        registry, and the lane total is the track lane list's length (286 lanes, one stereo)."""
        d = _load(REMEMBER)
        audio = [p for p in BS.session_playlists(d) if p.kind == "audio"]
        cum = 0
        for p in audio:
            self.assertEqual(p.lanes, tuple(range(cum, cum + p.channels)), p.name)
            cum += p.channels
        self.assertEqual(cum, 286)
        self.assertEqual(len({p.guid for p in audio}), 285, "one GUID per playlist")

    @unittest.skipUnless(Path(CHRISTMAS_HACK % 0).exists(), "CHRISTMAS IN MY HEART chain not mounted")
    def test_a_switch_changes_the_active_index_target_not_the_group(self):
        """bak.000 -> bak.001 switches the instrument track `JK PNO` from playlist .01 to .04 and
        changes nothing else about its playlists. Pro Tools re-saves the MIDI registry with the new
        active FIRST and remaps the list, so the active index stays 0 while what it points at moves."""
        a, b = _load(CHRISTMAS_HACK % 0), _load(CHRISTMAS_HACK % 1)
        ta = [t for t in BS.track_playlist_sets(a) if t.kind == "instrument"]
        tb = [t for t in BS.track_playlist_sets(b) if t.kind == "instrument"]
        self.assertEqual(len(ta), 1)
        self.assertEqual((ta[0].active.name, tb[0].active.name), ("JK PNO.01", "JK PNO.04"))
        self.assertEqual([p.name for p in ta[0].playlists], [p.name for p in tb[0].playlists])
        self.assertEqual((ta[0].active.index, tb[0].active.index), (0, 0))
        midi_b = [p.name for p in BS.session_playlists(b) if p.kind == "midi"]
        self.assertEqual(midi_b, ["JK PNO.04", "JK PNO", "JK PNO.01", "JK PNO.02", "JK PNO.03",
                                  "JK PNO.05", "JK PNO.06"], "active first, then the rest by name")

    @unittest.skipUnless(DWTS, "DWTS PT 2603 not mounted")
    def test_unassigned_playlists_are_reported(self):
        """Pro Tools keeps playlists no track owns. This show master has eight, holding clips."""
        d = _load(DWTS)
        un = BS.unassigned_playlists(d)
        self.assertEqual(len(un), 8)
        self.assertIn("DWTS Editor Ending", [p.name for p in un])
        sets = BS.track_playlist_sets(d)
        self.assertEqual(sum(len(s.playlists) for s in sets) + len(un), len(BS.session_playlists(d)))

    @unittest.skipUnless(SOCAL, "Christmas Time In Southern California not mounted")
    def test_active_comes_from_the_list_not_from_a_position(self):
        """Not every writer saves the registry actives-first: this session's audio actives sit at
        registry indices 6, 8, 10, ... and the leading entries are unnamed, unowned playlists."""
        d = _load(SOCAL)
        sets = [s for s in BS.track_playlist_sets(d) if s.kind == "audio"]
        self.assertEqual([s.active.index for s in sets][:3], [6, 8, 10])
        self.assertEqual(len(BS.track_types(d)), len(BS.track_playlist_sets(d)))
        self.assertTrue(any(p.name == "" for p in BS.unassigned_playlists(d)))

    @unittest.skipUnless(COUNT_ON_ME, "Count On Me not mounted")
    def test_midi_track_lists_index_the_midi_registry(self):
        d = _load(COUNT_ON_ME)
        midi = [s for s in BS.track_playlist_sets(d) if s.kind == "midi"]
        self.assertEqual(len(midi), 15)
        self.assertTrue(all(p.kind == "midi" for s in midi for p in s.playlists))
        self.assertEqual(sum(len(s.playlists) for s in midi), 29, "15 actives + 14 alternates")

    def test_synthesized_sessions_own_one_playlist_per_track(self):
        """Every synthesizer's output: one playlist per track, each track owning its own, in order --
        and so `track_types` and `playlist_headers` agree."""
        builds = {
            "mono 9": BS.synthesize_mono_inline(9),
            "stereo 12": BS.synthesize_stereo_inline(12),
            "mixed": BS.synthesize_mixed_inline([1, 2, 1, 2, 2, 1]),
            "midi [1,0,2]": BS.synthesize_mixed_midi_inline([1, 0, 2]),
            "midi [1,0,2,0,1,2]": BS.synthesize_mixed_midi_inline([1, 0, 2, 0, 1, 2]),
            "midi [1,0,2,1,1,0]": BS.synthesize_mixed_midi_inline([1, 0, 2, 1, 1, 0]),
            "click": BS.synthesize([1, 2, 1], click="top"),
        }
        for label, d in builds.items():
            with self.subTest(build=label):
                sets = BS.track_playlist_sets(d)
                self.assertTrue(sets, "ownership lists did not parse")
                self.assertTrue(all(len(s.playlists) == 1 and s.playlists[0] == s.active for s in sets))
                for kind in ("audio", "midi"):
                    act = [s.active.index for s in sets if s.active.kind == kind]
                    self.assertEqual(sorted(act), list(range(len(act))), kind)
                self.assertEqual(BS.unassigned_playlists(d), [])
                self.assertEqual(BS.track_types(d), BS.playlist_headers(d))


ROOT = Path(__file__).resolve().parents[1]
CLIPS3 = ROOT / "control_files" / "lots of stereo tracks" / "3 stereo 3 different clips.ptx"
MSM = ROOT / "control_files" / "mixed tracks" / "mono stereo mono.ptx"


def _groups(d):
    return {t.name: [p.name for p in t.playlists] for t in BS.track_playlist_sets(d)}


def _clips_by_owner(d):
    out = {}
    for _z, name, n in BS.clip_lanes(d):
        out[name] = out.get(name, 0) + n
    return out


@unittest.skipUnless(CLIPS3.exists() and MSM.exists(), "local control sessions missing")
class PlaylistEditTests(unittest.TestCase):
    """The playlist editors on PT-authored controls. Every edit must leave the session valid, the
    model must show exactly the intended change, and each edit must undo to the original BYTES --
    the editors rebuild the header and lane runs, the two index element lists and every list, so an
    off-by-one anywhere shows up as a byte difference."""

    def setUp(self):
        self.d = _load(CLIPS3)

    def test_the_registry_rewrite_is_an_identity_on_an_unedited_session(self):
        for path in (CLIPS3, MSM) + ((REMEMBER,) if REMEMBER else ()):
            d = _load(path)
            with self.subTest(session=Path(path).name):
                self.assertEqual(BS._AudioPlaylistPlan(d).build(), d)

    def test_new_playlist_switch_back_and_delete_round_trip(self):
        a = BS.new_playlist(self.d, "Audio 2")
        self.assertEqual(BS.validate(a), [])
        self.assertEqual(_groups(a)["Audio 2.01"], ["Audio 2", "Audio 2.01"])
        self.assertNotIn("Audio 2", _groups(a), "the track takes the new playlist's name")
        clips = _clips_by_owner(a)
        self.assertNotIn("Audio 2.01", clips, "a new playlist is empty")
        self.assertEqual(clips.get("Audio 2"), 2, "the old playlist keeps its stereo clip")
        b = BS.set_active_playlist(a, "Audio 2.01", "Audio 2")
        self.assertEqual(BS.validate(b), [])
        self.assertEqual(_groups(b)["Audio 2"], ["Audio 2", "Audio 2.01"])
        self.assertEqual(BS.delete_playlist(b, "Audio 2.01"), self.d)

    def test_new_playlist_without_activating(self):
        a = BS.new_playlist(self.d, "Audio 3", "Audio 3 take 2", activate=False)
        self.assertEqual(_groups(a)["Audio 3"], ["Audio 3", "Audio 3 take 2"])
        self.assertEqual(BS.delete_playlist(a, "Audio 3 take 2"), self.d)

    def test_duplicate_playlist_copies_the_clips(self):
        a = BS.duplicate_playlist(self.d, "Audio 1")
        self.assertEqual(BS.validate(a), [])
        clips = _clips_by_owner(a)
        self.assertEqual((clips.get("Audio 1"), clips.get("Audio 1.01")), (2, 2))
        b = BS.delete_playlist(BS.set_active_playlist(a, "Audio 1.01", "Audio 1"), "Audio 1.01")
        self.assertEqual(b, self.d)

    def test_rename_an_alternate_and_back(self):
        a = BS.new_playlist(self.d, "Audio 2", activate=False)
        b = BS.rename_playlist(a, "Audio 2.01", "Audio 2 B")
        self.assertEqual(_groups(b)["Audio 2"], ["Audio 2", "Audio 2 B"])
        self.assertEqual(BS.rename_playlist(b, "Audio 2 B", "Audio 2.01"), a)

    def test_the_active_playlist_cannot_be_deleted(self):
        with self.assertRaises(ValueError):
            BS.delete_playlist(self.d, "Audio 2")

    def test_remove_track_takes_its_alternates_unless_asked_to_keep_them(self):
        a = BS.duplicate_playlist(self.d, "Audio 2")
        gone = BS.remove_track(a, "Audio 2.01")
        self.assertEqual(sorted(_groups(gone)), ["Audio 1", "Audio 3"])
        self.assertEqual(BS.unassigned_playlists(gone), [])
        kept = BS.remove_track(a, "Audio 2.01", keep_alternates=True)
        self.assertEqual([p.name for p in BS.unassigned_playlists(kept)], ["Audio 2"])

    def test_duplicate_track_keeps_ownership_and_lanes_consistent(self):
        """Off the synth path the copy's header goes in after its source's, which moves every later
        registry index: every list must follow, the copy must own its own playlist, and each
        playlist's lanes must stay consecutive and cumulative."""
        for path, src in ((CLIPS3, "Audio 2"), (MSM, "Audio 1"), (MSM, "Audio 2")):
            d = _load(path)
            with self.subTest(session=path.name, track=src):
                x = BS.duplicate_track(d, src, "Audio Q")
                g = _groups(x)
                self.assertEqual(g["Audio Q"], ["Audio Q"])
                for name, members in _groups(d).items():
                    self.assertEqual(g[name], members)
                cum = 0
                for p in [p for p in BS.session_playlists(x) if p.kind == "audio"]:
                    self.assertEqual(p.lanes, tuple(range(cum, cum + p.channels)), p.name)
                    cum += p.channels
                self.assertEqual(BS.remove_track(x, "Audio Q"), d)


MIDI2 = ROOT / "control_files" / "various" / "two midi tracks one note each.ptx"


def _midi_placements(d):
    return {n: k for _z, n, k, kind in BS.placement_lanes(d, kinds=("midi",))}


@unittest.skipUnless(MIDI2.exists(), "local MIDI control missing")
class MidiPlaylistEditTests(unittest.TestCase):
    """The same editors on MIDI tracks, whose playlists are `0x1057` blocks holding their own
    placements (no lanes) and whose lists have a u32 count."""

    def setUp(self):
        self.d = _load(MIDI2)

    def test_new_playlist_switch_back_and_delete_round_trip(self):
        a = BS.new_playlist(self.d, "MIDI 1")
        self.assertEqual(BS.validate(a), [])
        self.assertEqual(_groups(a)["MIDI 1.01"], ["MIDI 1", "MIDI 1.01"])
        self.assertNotIn("MIDI 1.01", _midi_placements(a), "a new playlist is empty")
        self.assertEqual(_midi_placements(a).get("MIDI 1"), 1, "the note stays on the old playlist")
        b = BS.set_active_playlist(a, "MIDI 1.01", "MIDI 1")
        self.assertEqual(BS.delete_playlist(b, "MIDI 1.01"), self.d)

    def test_duplicate_playlist_copies_the_note(self):
        a = BS.duplicate_playlist(self.d, "MIDI 2")
        self.assertEqual(BS.validate(a), [])
        p = _midi_placements(a)
        self.assertEqual((p.get("MIDI 2"), p.get("MIDI 2.01")), (1, 1))

    def test_rename_an_alternate_and_back(self):
        a = BS.new_playlist(self.d, "MIDI 2", activate=False)
        b = BS.rename_playlist(a, "MIDI 2.01", "MIDI 2 B")
        self.assertEqual(_groups(b)["MIDI 2"], ["MIDI 2", "MIDI 2 B"])
        self.assertEqual(BS.rename_playlist(b, "MIDI 2 B", "MIDI 2.01"), a)

    def test_a_midi_list_is_read_forward(self):
        """`[1][2][1, 2]` also reads backwards as the shorter `[2][1][2]` -- a u32 count can be
        mistaken for an index. This edit produces exactly that list; it must read as two entries."""
        a = BS.new_playlist(self.d, "MIDI 2")
        b = BS.set_active_playlist(a, "MIDI 2.01", "MIDI 2")
        self.assertEqual(_groups(b)["MIDI 2"], ["MIDI 2", "MIDI 2.01"])


def _vol(path):
    return Path(path) if Path(path).exists() else None


VH = "/Volumes/Hack 2TB SSD Old/GS MK DK JK/20230210 JON VH NOT YET/Session File Backups/" \
     "20230210 JON VH NOT YET.bak.%03d.ptx"


def _runs(d):
    bl = BS._size_driven_blocks(d)
    h, l, _a, _b = BS._playlist_runs(d, bl)
    lists = [(act, idx) for _z0, _c0, act, idx, _s, _e in BS._ownership_lists(d, bl)]
    return [d[z:e] for z, e in h], [d[z:e] for z, e in l], lists


class ProToolsReplayTests(unittest.TestCase):
    """The strongest check available short of opening Pro Tools: replay an edit Pro Tools made (its
    own consecutive backups) and compare every playlist structure with what it saved."""

    @unittest.skipUnless(_vol(CHRISTMAS_HACK % 8), "CHRISTMAS IN MY HEART chain not mounted")
    def test_audio_switch_matches_pro_tools(self):
        """bak.007 -> .008: Pro Tools switched 23 tracks at once -- 22 audio and the instrument track."""
        a, b = _load(CHRISTMAS_HACK % 7), _load(CHRISTMAS_HACK % 8)
        ta = sorted(BS.track_playlist_sets(a), key=lambda t: t.container)
        tb = sorted(BS.track_playlist_sets(b), key=lambda t: t.container)
        x = a
        for u, v in zip(ta, tb):
            if u.active.name != v.active.name:
                x = BS.set_active_playlist(x, u.name, v.active.name)
        self.assertEqual(_runs(x), _runs(b), "headers, lanes (with clips) and lists, byte for byte")

    @unittest.skipUnless(_vol(CHRISTMAS_HACK % 1), "CHRISTMAS IN MY HEART chain not mounted")
    def test_midi_switch_matches_pro_tools(self):
        a, b = _load(CHRISTMAS_HACK % 0), _load(CHRISTMAS_HACK % 1)
        x = BS.set_active_playlist(a, "JK PNO.01", "JK PNO.04")
        bx, bb = BS._size_driven_blocks(x), BS._size_driven_blocks(b)
        self.assertEqual([x[z:e] for z, e, c in bx if c == 0x1057], [b[z:e] for z, e, c in bb if c == 0x1057])
        self.assertEqual([r[2:4] for r in BS._ownership_lists(x, bx)], [r[2:4] for r in BS._ownership_lists(b, bb)])
        g = lambda d, bl: [d[z + 17 : z + 25] for z, e, c in bl if c == 0x202C
                           and any(pz < z < pe for pz, pe, pc in bl if pc == 0x202E)]
        self.assertEqual(g(x, bx), g(b, bb), "the 0x202E records follow the registry")

    @unittest.skipUnless(_vol(VH % 11), "JON VH NOT YET chain not mounted")
    def test_new_playlist_matches_pro_tools(self):
        """bak.010 -> .011: New Playlist on `PRE GT MUTE`. Pro Tools also recorded a clip into the
        new playlist, so its lane and its (random) GUID are the only things allowed to differ."""
        a, b = _load(VH % 10), _load(VH % 11)
        x = BS.new_playlist(a, "PRE GT MUTE")
        (hx, lx, sx), (hb, lb, sb) = _runs(x), _runs(b)
        self.assertEqual(sx, sb)
        new = [p for p in BS.session_playlists(x) if p.name == "PRE GT MUTE.01"][0]
        strip = lambda h: h[: BS._header_fields(h)["guid"]] + h[BS._header_fields(h)["guid"] + 8 :]
        self.assertEqual([strip(h) for h in hx], [strip(h) for h in hb])
        self.assertEqual([l for i, l in enumerate(lx) if i not in new.lanes],
                         [l for i, l in enumerate(lb) if i not in new.lanes])


if __name__ == "__main__":
    unittest.main()
