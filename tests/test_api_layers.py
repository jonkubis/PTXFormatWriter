"""The curated `ptxformatwriter.workbench` surface re-exports the preferred session-construction
API (from-scratch synthesis and the low-level I/O primitive). The beatmap MIDI+stems
application is no longer part of the library — it lives in `examples/beatmap_example.py`."""
import unittest

from ptxformatwriter import writer, workbench
from ptxformatwriter.body_synth import synthesize_stereo_session


class ApiLayerTests(unittest.TestCase):
    def test_workbench_exposes_preferred_session_construction_surface(self):
        self.assertIs(workbench.synthesize_stereo_session, synthesize_stereo_session)
        self.assertIs(workbench.encrypt_session_data, writer.encrypt_session_data)

    def test_beatmap_pipeline_is_not_in_the_library(self):
        """The beatmap application moved out of the package; neither the workbench nor the
        top-level namespace re-exports it (it's `examples/beatmap_example.py` now)."""
        import ptxformatwriter as ptx
        self.assertFalse(hasattr(workbench, "build_session_from_beatmap"))
        self.assertFalse(hasattr(ptx, "beatmap"))
        self.assertFalse(hasattr(ptx, "build_session_from_beatmap"))


class PublicSurfaceTests(unittest.TestCase):
    """The read layer must be reachable from the package root, not only from `body_synth`.

    It was not, for as long as the read layer existed: `clips`, `markers`, `tempo_map`,
    `meter_map`, `region_names` and `validate_pt_invariants` were importable only as
    `ptxformatwriter.body_synth.<name>`, so a caller following the package docstring would have
    concluded the library can BUILD sessions but not INSPECT them.

    The existing suite could not catch that, because every other test imports from `body_synth`
    directly. **A test that imports internals cannot detect a missing public export**, so this
    one deliberately goes through the package root.
    """

    #: Everything the package intends to offer for reading an arbitrary session.
    READ_LAYER = (
        # whole-session snapshot + the format fields it reports
        "session_info", "session_product", "session_sample_rate", "session_bit_depth",
        "session_start_bar", "base_tempo", "base_meter",
        # placements and the objects they refer to
        "clips", "clip_lanes", "clip_names", "clip_positions", "placement_lanes",
        "playlist_names", "track_playlists", "track_types", "region_list", "region_names",
        # conductor
        "markers", "tempo_map", "meter_map",
        # machine context
        "volumes", "midi_devices",
        # checking
        "validate", "validate_pt_invariants",
    )

    def test_every_reader_is_reachable_from_the_package_root(self):
        import ptxformatwriter as ptx
        from ptxformatwriter import body_synth
        missing = [n for n in self.READ_LAYER if not hasattr(ptx, n)]
        self.assertEqual(missing, [], f"not exported from the package root: {missing}")
        for n in self.READ_LAYER:
            self.assertIs(getattr(ptx, n), getattr(body_synth, n),
                          f"ptx.{n} must BE body_synth.{n}, not a copy or a shadow")

    def test_every_reader_is_declared_in___all__(self):
        """Reachable is not enough — `from ptxformatwriter import *` must bring them too."""
        import ptxformatwriter as ptx
        undeclared = [n for n in self.READ_LAYER if n not in ptx.__all__]
        self.assertEqual(undeclared, [], f"missing from __all__: {undeclared}")

    def test_every___all___entry_actually_resolves(self):
        """A name in `__all__` that does not exist breaks `import *` for everything after it."""
        import ptxformatwriter as ptx
        broken = [n for n in ptx.__all__ if not hasattr(ptx, n)]
        self.assertEqual(broken, [], f"declared in __all__ but absent: {broken}")
