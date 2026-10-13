"""Which track a lane belongs to, what the `0x2428` placements are not, and how a region's
variable section is laid out (spec §8, §9).

The evidence for all three is in sessions this repository cannot carry -- show masters with
hundreds of renamed imported tracks, thousands of `0x2428` records and trimmed regions -- and the
control sessions have none of those things. So the model is pinned here on hand-built bytes and on
a control session edited into the shapes the masters have, and the controls themselves are checked
to read exactly as they did before.
"""
from pathlib import Path
import unittest
from unittest import mock

from ptxformatwriter.core import PTFFormat
from ptxformatwriter import body_synth as BS

ROOT = Path(__file__).resolve().parents[1]
_BAR1 = next(iter(ROOT.glob("control_files/**/one clip bar 1.ptx")), None)
_CONTROLS = sorted(ROOT.glob("control_files/**/*.ptx"))


def _load(p: Path) -> bytes:
    ptf = PTFFormat()
    ptf.load(str(p), 48000)
    return ptf.unxored_data()


def _field(data: bytes, slot) -> int:
    w, at = slot
    return int.from_bytes(data[at : at + w], "little")


class RegionVariableSection(unittest.TestCase):
    def test_eight_widths_low_nibble_first(self):
        """S3501's REF stems carry a sync point: descriptor byte 2 declares a 2-byte sync in its LOW
        nibble and a 3-byte length in its HIGH nibble, and the sync's bytes come first."""
        body = bytes([0x00, 0x00, 0x32, 0x00, 0x00]) + (1064).to_bytes(2, "little") + (3_108_096).to_bytes(3, "little")
        slots = BS._region_slots(body, 0)
        self.assertEqual([w for w, _at in slots], [0, 0, 2, 3, 0, 0, 0, 0])
        self.assertEqual(_field(body, slots[BS._SLOT_SYNC]), 1064)
        self.assertEqual(_field(body, slots[BS._SLOT_LENGTH]), 3_108_096)

    def test_every_field_is_skipped_by_its_declared_width(self):
        """Slots 4..6 (descriptor bytes 3 and 4) are common in real sessions; whatever they mean,
        each consumes exactly the bytes its nibble declares."""
        body = bytes([0x00, 0x20, 0x13, 0x41, 0x02]) + bytes(range(1, 14))
        self.assertEqual([at for _w, at in BS._region_slots(body, 0)], [5, 5, 7, 10, 11, 12, 16, 18])

    @unittest.skipUnless(_BAR1, "clip control not present")
    def test_a_trimmed_regions_length_is_not_its_trim(self):
        """Give the control's region a head trim, the way most regions in a show master have one: the
        trim's field comes FIRST. The length must still read and write as the length."""
        data = _load(_BAR1)
        z, nl = BS._region_2628(data, 0)
        dz = z + 13 + nl
        length = BS.region_length(data, 0)
        self.assertEqual(BS.region_geometry(data, 0), {"offset": 0, "sync": 0, "length": length})

        trimmed = bytearray(BS._splice_in(data, dz + 5, (4800).to_bytes(2, "little")))
        trimmed[dz + 1] = (2 << 4) | (trimmed[dz + 1] & 0x0F)        # byte 1 high: a 2-byte trim
        trimmed = bytes(trimmed)
        self.assertEqual(BS.validate(trimmed), [])
        self.assertEqual(BS.region_geometry(trimmed, 0), {"offset": 4800, "sync": 0, "length": length})
        self.assertEqual(BS.region_length(trimmed, 0), length)

        out = BS.set_region_length(trimmed, 0, 4321)                  # fits: in place
        self.assertEqual(len(out), len(trimmed))
        self.assertEqual(BS.region_geometry(out, 0), {"offset": 4800, "sync": 0, "length": 4321})

        wide = BS.set_region_length(trimmed, 0, 70_000)               # widens the LENGTH, not the trim
        self.assertEqual(len(wide) - len(trimmed), 1)
        self.assertEqual(BS.region_geometry(wide, 0), {"offset": 4800, "sync": 0, "length": 70_000})
        self.assertEqual(wide[dz + 1] >> 4, 2)
        self.assertEqual(wide[dz + 2] >> 4, 3)
        self.assertEqual(BS.validate(wide), [])


class LaneOwnership(unittest.TestCase):
    def test_owners_by_position(self):
        lanes = [100, 200, 300, 400]
        self.assertEqual(BS._owners_by_position(lanes, [("Kick", 1), ("OH", 2), ("Snare", 1)]),
                         [(100, "Kick", 0, 1), (200, "OH", 0, 2), (300, "OH", 1, 2), (400, "Snare", 0, 1)])
        self.assertIsNone(BS._owners_by_position(lanes, [("Kick", 1), ("OH", 2)]))       # one lane short
        self.assertIsNone(BS._owners_by_position(lanes, [("Kick", 2), ("OH", 3)]))       # one lane over
        self.assertIsNone(BS._owners_by_position(lanes, [("Kick", 4), ("MIDI", 0)]))
        self.assertIsNone(BS._owners_by_position([], []))

    @unittest.skipUnless(_CONTROLS, "control files not present")
    def test_the_controls_read_as_they_did(self):
        """No control session renames an imported track, so every lane's owner is the name in its
        head, and `clip_lanes` reads exactly as it did before owners existed."""
        checked = 0
        for p in _CONTROLS:
            data = _load(p)
            bl = BS._size_driven_blocks(data)
            heads = [(z, BS._lane_track_name(data, z, e)) for z, e, c in bl if c == 0x1052]
            owners = BS.lane_owners(data)
            if owners is None:
                continue
            head = dict(heads)
            self.assertEqual([(z, n) for z, n, _ch, _k in owners], [(z, head[z]) for z, *_ in owners], msg=p.name)
            lanes = BS.clip_lanes(data)
            self.assertEqual([(z, n) for z, n, _k in lanes], [(z, head[z]) for z, *_ in lanes], msg=p.name)
            checked += 1
        self.assertGreater(checked, 0)


class CrossReferencePlacements(unittest.TestCase):
    """A track lane list with one placement, and a `0x2428` tree with one record in the same
    layout. Only the first is a clip."""

    def setUp(self):
        d = bytearray(200)
        d[40 + 16 : 40 + 24] = (1234).to_bytes(8, "little")
        d[120 + 16 : 120 + 24] = ((0x40 << 56) | 1_000_000_000_000).to_bytes(8, "little")
        self.data = bytes(d)
        self.blocks = [
            (30, 80, 0x1054), (32, 78, 0x1052), (36, 76, 0x1050), (40, 70, 0x104F),
            (100, 190, 0x2428), (110, 180, 0x1054), (115, 175, 0x1052), (118, 170, 0x1050), (120, 160, 0x104F),
        ]

    def test_readers_skip_the_cross_reference_tree(self):
        with mock.patch.object(BS, "_size_driven_blocks", return_value=self.blocks):
            self.assertEqual(BS.clip_positions(self.data), [1234])
            self.assertEqual([z for z, _n, _k in BS.clip_lanes(self.data)], [32])
            self.assertEqual([z for z, _n, _k, _kind in BS.placement_lanes(self.data)], [32])

    def test_moving_every_clip_leaves_the_cross_reference_tree_alone(self):
        with mock.patch.object(BS, "_size_driven_blocks", return_value=self.blocks):
            out = BS.set_clip_position(self.data, 5555)
        self.assertEqual(int.from_bytes(out[56:64], "little"), 5555)
        self.assertEqual(out[120:200], self.data[120:200])


if __name__ == "__main__":
    unittest.main()
