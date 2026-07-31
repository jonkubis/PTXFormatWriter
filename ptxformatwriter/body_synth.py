"""Body synthesis (Pass 1): grow a session's block body to N tracks.

The body (everything except the final 0x0002 index) has no structured
absolute-pointer network — cross-block references live only in the index. So
growing the body is purely structural: insert per-track child blocks into their
parents, grow the ordinal lists, set the per-N first-block counter, and recompute
every enclosing block_size. After the body is grown, regenerate the index with
`final_index.compose_index`.

Implementation is byte-level: blocks are spliced in at exact offsets and every
enclosing parent's `block_size` field is bumped by the inserted length. This
matches the real lane-major layout that `compose_index` resolves against.
"""
from __future__ import annotations

import functools as _functools
import re
from dataclasses import dataclass

from .core import Block, PTFFormat
from . import writer as W

_NAME_RE = re.compile(rb"(?:Audio|Click|MIDI) (\d+)")  # match every track-type name prefix
                                                      # (final_index._NAME_RE already does)
_TRACK_NAME = re.compile(rb"(?:Audio|Click|MIDI) \d+")


def parse(data: bytes) -> PTFFormat:
    return W.parse_unxored(data)


def _own_track_name(data: bytes, b: Block) -> "str | None":
    """The track name in a block's OWN bytes (children blanked), or None. Used to
    locate the click's per-track blocks for reordering (the click is named
    'Click 1'); a container that merely contains a named child returns None."""
    own = bytearray(data[b.offset : b.offset + b.block_size])
    for c in b.child:
        cs, ce = (c.offset - 7) - b.offset, (c.offset + c.block_size) - b.offset
        for i in range(max(0, cs), min(ce, len(own))):
            own[i] = 0
    m = _TRACK_NAME.search(bytes(own))
    return m.group(0).decode() if m else None


def flat_blocks(ptf: PTFFormat) -> list[Block]:
    out: list[Block] = []

    def rec(b: Block) -> None:
        out.append(b)
        for c in b.child:
            rec(c)

    for b in ptf.blocks:
        rec(b)
    out.sort(key=lambda b: b.offset)
    return out


def block_bytes(data: bytes, b: Block) -> bytes:
    return data[b.offset - 7 : b.offset + b.block_size]


def _ancestor_chain(ptf: PTFFormat, parent_zmark: int) -> list[int]:
    """The block at `parent_zmark` plus every block that strictly contains it
    (its ancestors), as a list of zmark offsets. These are exactly the blocks
    whose `block_size` must grow when a child is inserted into the parent."""
    blocks = flat_blocks(ptf)
    by_zmark = {b.offset - 7: b for b in blocks}
    p = by_zmark[parent_zmark]
    pstart, pend = parent_zmark, p.offset + p.block_size
    chain = [parent_zmark]
    for b in blocks:
        bstart, bend = b.offset - 7, b.offset + b.block_size
        if bstart < pstart and pend <= bend:  # strictly contains the parent
            chain.append(bstart)
    return chain


@dataclass
class Insertion:
    offset: int        # absolute byte offset to insert at (within parent content)
    blob: bytes        # bytes to insert (a full child block, or own-bytes)
    parent_zmark: int  # zmark of the block being grown (its size + ancestors bump)


def apply_insertions(data: bytes, ptf: PTFFormat, insertions: list[Insertion]) -> bytes:
    """Insert each blob into its parent and bump the parent + all its ancestors'
    `block_size`. Disambiguating the parent explicitly (rather than inferring from
    the offset) is required because an insertion offset sits on a block boundary
    that is shared between a parent's content-end and the next sibling's start."""
    size_delta: dict[int, int] = {}  # zmark -> total added bytes
    for ins in insertions:
        for zmark in _ancestor_chain(ptf, ins.parent_zmark):
            size_delta[zmark] = size_delta.get(zmark, 0) + len(ins.blob)

    out = bytearray(data)
    for zmark, delta in size_delta.items():
        size_pos = zmark + 3
        old = int.from_bytes(out[size_pos : size_pos + 4], "little")
        out[size_pos : size_pos + 4] = (old + delta).to_bytes(4, "little")
    for ins in sorted(insertions, key=lambda x: x.offset, reverse=True):
        out[ins.offset : ins.offset] = ins.blob
    return bytes(out)


# --- stereo track unit + single-track grow ----------------------------------
from . import final_index as _FI  # noqa: E402  (avoid import cycle at top)


@dataclass
class StereoTrackUnit:
    """The block subtrees a single empty AUDIO track contributes to the body.
    Each is full block bytes (children inline). Lane order is lane-major for
    0x251a (lane0, lane1) and track-adjacent for 0x1052.

    `b1052` holds one 0x1052 audio lane PER CHANNEL: 2 for stereo, 1 for mono.
    The number of lanes is the track's channel count, so `len(b1052)` discriminates
    mono (1) from stereo (2). The class name is historical; it serves both."""

    b1014: bytes
    b1052: tuple[bytes, ...]
    b251a: tuple[bytes, bytes]
    b210b: bytes
    b261c: bytes
    b2589: bytes
    name_entry: bytes  # 34-byte entry for the 0x2519 own-bytes name table

    @property
    def channels(self) -> int:
        return len(self.b1052)


def _by_type(ptf: PTFFormat, ct: int) -> list[Block]:
    return [b for b in flat_blocks(ptf) if b.content_type == ct]


def _parent_zmarks(ptf: PTFFormat) -> dict[int, int]:
    pm: dict[int, int] = {}

    def rec(b: Block) -> None:
        for c in b.child:
            pm[c.offset - 7] = b.offset - 7
            rec(c)

    for b in ptf.blocks:
        rec(b)
    return pm


# The track kinds the 0x2624 display list actually indexes, derived by resolving every entry of
# every display list in the corpus back to its block: 6,456 entries across 139 sessions, ZERO
# unresolved. 0x261c audio (4029), 0x261e click/aux (1819), 0x261d master (274), 0x261f VCA (248),
# 0x2620 MIDI (70), 0x2621 (15), 0x2623 (1). NOTE this is NOT `_TRACK_RECORD_TYPES`, which carries
# 0x263b instead of 0x2623 -- a display list has never been observed to point at a 0x263b, and
# including a type the list does not contain would make the enumeration LONGER than the list it
# is supposed to index.
_DISPLAY_ORDER_TYPES = frozenset({0x261C, 0x261D, 0x261E, 0x261F, 0x2620, 0x2621, 0x2623})
_NAME_ENTRY_SUFFIX = 23        # `<6 fixed><4 tag 0x2a><8 id><ordinal:u32><1 pad>`
_NAME_ENTRY_SUFFIX_SHORT = 21  # the same, with a 2-byte ordinal instead of 4
# Upper bound on a name-table entry's BYTE length. Not a format constant -- a sanity bound that
# stops a garbage `u32` from being read as a length. The previous value (64) was below what Pro
# Tools actually emits: real clip names reach 100 bytes, and the cap silently ENDED the walk
# there. Observed maximum is 100 across 288 sessions; 255 leaves headroom while still rejecting
# wild lengths. The real discriminator is UTF-8 validity (see `_name_entry_head`), not this.
# Wrapper -> the child it carries in 100% of untouched corpus instances (§5c). Used by
# `validate_pt_invariants` to catch a splice that dropped a child.
# Each conductor-ruler container holds exactly one 0x2619 (its NAME) and exactly one content
# block. Measured 645 rulers over 240 sessions, zero exceptions (§5c).
_RULER_CONTENT = {0x2718: 0x2028, 0x2719: 0x2029, 0x271A: 0x2030, 0x271B: 0x2433, 0x271C: 0x2437}
# The types that hold exactly one 0x2011. Measured 2,040 instances, zero exceptions. Stated as a
# FLOOR: this membership has been revised upward twice as the sample widened.
_ONE_2011_HOLDERS = frozenset({0x2016, 0x2017, 0x2019, 0x201A, 0x206C,
                               0x2505, 0x2507, 0x259C, 0x4834, 0x4841})

_MANDATED_CHILD = {
    0x1050: 0x104F, 0x1056: 0x104F, 0x1051: 0x104F,   # the three placement chains
    0x2629: 0x2628, 0x2625: 0x2626, 0x2038: 0x2037, 0x203B: 0x2037,
    0x261C: 0x261B, 0x200B: 0x200A, 0x2104: 0x2103,
}
# The empty-terminator types and their fixed spans -- byte-constant in every instance measured.
_TERMINATOR_SPAN = {0x2037: 9, 0x2626: 9, 0x4301: 41, 0x4420: 11, 0x1048: 13, 0x1600: 8}

_NAME_ENTRY_MAX_BYTES = 255


def _name_table_entries(table: bytes) -> list[bytes]:
    """Split a 0x2519 own-byte name table into per-track entries (track order).
    Each entry is `<name_len:u32> "Audio k" <23-byte suffix>`, so single-digit
    names give 34-byte entries and multi-digit ("Audio 10"...) give 35+. The final
    entry is clamped to the table end (its last 2 bytes fall past the parsed
    own-byte boundary); the wholesale transplant in `synthesize` fixes exactness."""
    first = _NAME_RE.search(table)
    if first is None:
        return []
    pos = first.start() - 4  # the u32 length prefix precedes the name
    entries: list[bytes] = []
    while pos + 4 <= len(table):
        nlen = int.from_bytes(table[pos : pos + 4], "little")
        if nlen <= 0 or nlen > 64:
            break
        step = 4 + nlen + _NAME_ENTRY_SUFFIX
        entries.append(table[pos : min(pos + step, len(table))])
        pos += step
    return entries


def _name_table_region_end(data: bytes, z: int, e: int, bl) -> int:
    """Where the `0x2519` inline-entry region really ends.

    Normally the first framed child of the name-table block. But a child can be a PHANTOM: an
    entry's 8-byte GUID may contain 0x5A, which the size-driven walk frames as a block sitting
    INSIDE the entry region (see `_inline_name_entries`). Taking that as the bound truncates the
    table, and -- because `_name_entry_span` CLAMPS an entry to this value -- it also silently
    chops entries at or past the phantom, so a splice copied a malformed entry and corrupted
    everything after it.

    Resolved the same way in both readers: if walking to the block's own end yields MORE entries
    than walking to the first child, the child was a phantom and `e` is the real bound.
    """
    end = min((cz for cz, _ce, _cc in bl if z < cz < e), default=e)
    if end == e:
        return end

    # Classify each "child" rather than guess a bound. Walk the entries permissively (to the block's
    # own end) and measure every entry by its MINIMUM possible span -- the SHORT 21-byte trailer. A
    # PHANTOM starts strictly inside that span; a REAL child cannot, because the entry before it has
    # to end at or before where it begins. That breaks the circularity, since the short-form span
    # needs no bound to compute.
    #
    # The bound matters twice over: `_name_entry_span` CLAMPS the final entry to it, and that clamp
    # is what gives that entry its short trailer. Returning the block end (or the walk terminus)
    # loses the clamp, so the last entry claims a 23-byte trailer overrunning the first real child
    # by two bytes -- which is what corrupted a last-entry duplicate.
    q = z + 9 + 0x14 + _name_table_shift(data, z)
    if _name_entry_head(data, q, e) is None:
        for c in range(q, min(q + 64, max(q, e - 8)), 2):
            if _name_entry_head(data, c, e) is not None:
                q = c
                break
    spans = []
    while True:
        h = _name_entry_head(data, q, e)
        if h is None:
            break
        _nm, ne = h
        spans.append((q, ne + _NAME_ENTRY_SUFFIX_SHORT))
        q = _next_name_entry(data, ne, e)
    real = [cz for cz, _ce, _cc in bl
            if z < cz < e and not any(a < cz < b for a, b in spans)]
    return min(real) if real else e


def _name_entry_span(data: bytes, name_off: int, bl) -> "tuple[int, int] | None":
    """The exact byte-span `[start, end)` of the `0x2519` inline name entry whose name
    begins at `name_off`. A normal entry is `<len:u32><name><23-byte trailer>`, but the
    LAST entry in the table carries a SHORT (21-byte) trailer -- it is bounded by the end
    of the inline-entry region (the first framed child of the `0x2519` block). Using the
    fixed 23-byte suffix on the last entry over-splices 2 bytes and misframes the table ->
    Pro Tools "end of stream". Returns None if `name_off` is not inside a `0x2519` block.
    `bl` is the size-driven block list."""
    host = None
    for z, e, c in bl:
        if c == 0x2519 and z < name_off < e and (host is None or z > host[0]):
            host = (z, e, c)
    if host is None:
        return None
    z, e, _c = host
    region_end = _name_table_region_end(data, z, e, bl)
    start = name_off - 4
    nlen = int.from_bytes(data[start : start + 4], "little")
    full_end = start + 4 + nlen + _NAME_ENTRY_SUFFIX
    return (start, min(full_end, region_end))   # clamp: the final entry's trailer is short


def _name_table_region(data: bytes, ptf: PTFFormat) -> tuple[int, int]:
    """(content_start, first_child_zmark) of the single 0x2519 block's own-byte
    name table."""
    b = _by_type(ptf, 0x2519)[0]
    return b.offset, min(c.offset - 7 for c in b.child)


def _set_name_table(body: bytes, library_data: bytes) -> bytes:
    """Replace the synthesized 0x2519 own-byte name table with the library's.
    Track-name entries are variable length (multi-digit names are longer) and the
    last entry has a boundary quirk; transplanting the target control's table
    reproduces it exactly and recomputes the 0x2519 (+ ancestor) block sizes."""
    ptf = parse(body)
    start, first_child = _name_table_region(body, ptf)
    lptf = parse(library_data)
    lstart, lfirst = _name_table_region(library_data, lptf)
    lib_tbl = library_data[lstart:lfirst]
    delta = len(lib_tbl) - (first_child - start)
    bz = _by_type(ptf, 0x2519)[0].offset - 7
    out = bytearray(body)
    if delta:
        for zmark in _ancestor_chain(ptf, bz):
            sp = zmark + 3
            old = int.from_bytes(out[sp : sp + 4], "little")
            out[sp : sp + 4] = (old + delta).to_bytes(4, "little")
    out[start:first_child] = lib_tbl
    return bytes(out)


def _fix_name_table_special_last(data: bytes, n: int) -> bytes:
    """Rebuild the 0x2519 own-byte name table as [header][n-1 NORMAL entries][1 SPECIAL last].

    The 0x2519 name table is [22-byte header (track count u32 @ +16)][n-1 NORMAL entries][1
    SPECIAL last entry], where the SPECIAL entry is exactly a NORMAL entry with its trailing
    2 bytes dropped (a 21-byte suffix instead of 23; GUID + all other bytes identical). When a
    build has a matching n-track control, `_set_name_table` transplants a correct table. When
    it does NOT (the grow path), `grow_one_track` strands the donor's SPECIAL last entry
    mid-table and appends the rest in NORMAL form, so Pro Tools reads entries at the normal
    stride, hits the stranded short entry, misaligns, and runs past the block -> "end of stream
    encountered" (PT-confirmed root cause, 2026-06-19). This rebuilds the correct ordering.

    Size-preserving (normalize the stranded entry +2, specialize the true last -2), so it needs
    no block-size or index fixup. Names are "Audio/MIDI/Click 1..n" at synthesis time (before any
    rename); entries are located by name match (`_NAME_RE`) rather than by a hardcoded prefix so
    the fix applies to every track type — a fixed "Audio " search silently no-ops on MIDI/Click
    and re-opens the stranded-special bug (PT-confirmed on a 9-MIDI build, 2026-06-20).
    Safe no-op if the entries aren't all present or the size would change."""
    ptf = parse(data)
    b2519 = _by_type(ptf, 0x2519)
    if not b2519:
        return data
    b = b2519[0]
    first_child = min(c.offset - 7 for c in b.child)
    start = b.offset
    region = data[start:first_child]
    matches = list(_NAME_RE.finditer(region))  # Audio/MIDI/Click <n> by name, any digit count
    if len(matches) != n:
        return data
    pref = [m.start() - 4 for m in matches]  # entry start = name start - 4-byte length prefix
    if any(p < 4 for p in pref) or pref != sorted(pref):
        return data
    norm = []
    for i, m in enumerate(matches):
        end = pref[i + 1] if i + 1 < n else len(region)
        ent = region[pref[i] : end]
        full = 4 + (m.end() - m.start()) + _NAME_ENTRY_SUFFIX  # 4 (len) + name + 23 (suffix)
        ent = ent + b"\x00" * (full - len(ent)) if len(ent) < full else ent[:full]
        norm.append(ent)
    norm[-1] = norm[-1][:-2]  # last entry -> SPECIAL form
    new_region = region[: pref[0]] + b"".join(norm)
    if len(new_region) != len(region):
        return data
    return data[:start] + new_region + data[first_child:]


def extract_track(data: bytes, track: int, total: int, channels: int = 2) -> StereoTrackUnit:
    """Extract audio track `track` (1-based) from a control with `total` audio
    tracks of `channels` channels each (2=stereo, 1=mono). Per-track blocks are
    deterministic per track index, so any uniform control with >= track tracks
    yields the same unit.

    0x1052 audio lanes are channel-major: an all-`channels` control lays them out
    as `channels` consecutive lanes per track, so track k's lanes are at indices
    `channels*(k-1) .. channels*(k-1)+channels-1`. 0x251a is lane-major (2 per
    track regardless of channel count): lane0 = k-1, lane1 = total + (k-1)."""
    ptf = parse(data)
    bb = lambda b: block_bytes(data, b)
    a14 = _by_type(ptf, 0x1014)
    a52 = _by_type(ptf, 0x1052)
    a51 = _by_type(ptf, 0x251a)
    a0b = _by_type(ptf, 0x210b)
    a1c = _by_type(ptf, 0x261c)
    a89 = _by_type(ptf, 0x2589)
    # 0x2519 own-byte name entry for this track. Entries are VARIABLE length:
    # `<name_len:u32> "Audio k" <23-byte suffix>`, so single-digit names are 34
    # bytes and multi-digit ("Audio 10"...) are 35+. A fixed stride misaligns the
    # table for >=10 tracks, so parse each entry by its length prefix.
    b2519 = _by_type(ptf, 0x2519)[0]
    first_child = min(c.offset - 7 for c in b2519.child)
    table = data[b2519.offset : first_child]
    name_entry = _name_table_entries(table)[track - 1]
    # 0x1052 audio lanes are track-major (each track contributes `channels` lanes).
    # The base index of track `track`'s lanes = the cumulative channel count of all
    # tracks before it. For a uniform control that's `channels*(track-1)`, but a
    # MIXED source (e.g. mono+stereo) needs the real running total, else we'd grab
    # an adjacent track's lane.
    audio = [t for t in track_types(data) if t.kind in ("mono", "stereo")]
    base = sum(t.channels for t in audio[: track - 1]) if len(audio) >= track else channels * (track - 1)
    return StereoTrackUnit(
        b1014=bb(a14[track - 1]),
        b1052=tuple(bb(a52[base + i]) for i in range(channels)),
        b251a=(bb(a51[track - 1]), bb(a51[total + (track - 1)])),
        b210b=bb(a0b[track - 1]),
        b261c=bb(a1c[track - 1]),
        b2589=bb(a89[track - 2]),  # 0x2589 count is N-1; track k's is index k-2
        name_entry=name_entry,
    )


def _patch_u32(buf: bytearray, pos: int, val: int) -> None:
    buf[pos : pos + 4] = int(val).to_bytes(4, "little")


def grow_one_track(data: bytes, base_n: int, unit: StereoTrackUnit) -> bytes:
    """Insert one empty audio track (-> base_n+1 tracks) into the body `data`
    (a body without the final index). Works for mono or stereo: the number of
    0x1052 audio lanes appended = `unit.channels`, and the 0x1054 channel count is
    set to `channels * (base_n+1)` (uniform session). Returns the new body."""
    n = base_n + 1
    ptf = parse(data)
    pm = _parent_zmarks(ptf)
    ins: list[Insertion] = []

    def append_after(block: Block, blob: bytes) -> None:
        ins.append(Insertion(block.offset + block.block_size, blob, pm[block.offset - 7]))

    a14 = _by_type(ptf, 0x1014)
    append_after(a14[-1], unit.b1014)
    a52 = _by_type(ptf, 0x1052)
    for lane in unit.b1052:                   # 1 (mono) or 2 (stereo) lanes
        append_after(a52[-1], lane)
    a51 = _by_type(ptf, 0x251a)               # lane-major: lane0 mid-run, lane1 end
    append_after(a51[base_n - 1], unit.b251a[0])
    append_after(a51[-1], unit.b251a[1])
    append_after(_by_type(ptf, 0x210b)[-1], unit.b210b)
    append_after(_by_type(ptf, 0x261c)[-1], unit.b261c)
    append_after(_by_type(ptf, 0x2589)[-1], unit.b2589)

    # 0x2519 own-bytes name entry (inserted before the first child)
    b2519 = _by_type(ptf, 0x2519)[0]
    first_child = min(c.offset - 7 for c in b2519.child)
    ins.append(Insertion(first_child, unit.name_entry, b2519.offset - 7))

    # 0x202a ordinal append (u16 before the fe ff terminator)
    for b in _by_type(ptf, 0x202a):
        s = b.offset - 7
        seg = data[s : b.offset + b.block_size]
        idx0 = seg.find(b"\xff\xff\xff\xff\x00\x00")
        feff = seg.find(b"\xfe\xff", idx0 + 8)
        ins.append(Insertion(s + feff, (n - 1).to_bytes(2, "little"), s))

    body = bytearray(apply_insertions(data, ptf, ins))
    # 0x1054 = the session's TOTAL audio channels. Reading it from the grown body
    # (sum of every track's channel count) is correct for uniform AND mixed
    # sessions; `unit.channels * n` would only hold when every track has the same
    # channel count (a too-small value -> Pro Tools "end of stream").
    _patch_counts(body, n, channel_count(bytes(body)))
    return bytes(body)


# --- library-free per-track unit synthesis -----------------------------------
# A uniform stereo control's per-track unit is DETERMINISTIC in the track index k
# (name digits, the two 0x1052 lane indices 2k-2/2k-1, the track number k, ten
# sequential element ids 10(k-1)+1..10(k-1)+10, and the k/k-1 counters) EXCEPT for
# two self-contained random fields: an 8-byte handle and a 160-byte blob (both in
# the 0x261c subtree, also mirrored at a few sites). Pro Tools requires those two
# only to be PRESENT -- not unique, not any specific value (PT-confirmed: a build
# that duplicates them across tracks loads fine). So track k's unit is GENERATED by
# re-keying a template unit (extracted once from a 512-track control and inlined in
# `_stereo_data`) -- no per-N library needed. The deterministic re-key is byte-exact
# vs `extract_track(512_control, k)` for k=3..63 in BOTH digit ranges; offsets after
# the variable-length "Audio k" name shift by +1 for k>=10.
_STEREO_UNIT_KEYS = ("b1014", "b1052_0", "b1052_1", "b251a_0", "b251a_1",
                     "b210b", "b261c", "b2589", "name_entry")


def _stereo_unit_blocks(u: "StereoTrackUnit") -> dict:
    return {"b1014": u.b1014, "b1052_0": u.b1052[0], "b1052_1": u.b1052[1],
            "b251a_0": u.b251a[0], "b251a_1": u.b251a[1], "b210b": u.b210b,
            "b261c": u.b261c, "b2589": u.b2589, "name_entry": u.name_entry}


def _stereo_unit_map(digits: int) -> dict:
    """Patch sites per unit block for a `digits`-digit track number (1/2/3 digits ->
    tracks 1-9 / 10-99 / 100-512). Each `lin` entry is (offset, width, k->value)
    written little-endian; `name` is where the decimal "Audio k" digits go;
    `guid`/`blob` are the self-contained random fields. Offsets after the
    variable-length name shift by `digits-1`. Field widths are sized for the max
    value at N=512: the lane indices (2k-2/2k-1 -> 1022/1023), track number, and the
    k-1 counter all need u16; the element ids + the other counters are u32."""
    s = digits - 1
    eid = lambda i: (lambda k: 10 * (k - 1) + 1 + i)
    return {
        "b1014": {"name": (19, digits), "guid": [41 + s],
                  "lin": [(25 + s, 2, lambda k: 2 * k - 2), (27 + s, 2, lambda k: 2 * k - 1),
                          (52 + s, 2, lambda k: 2 * k - 2), (56 + s, 2, lambda k: 2 * k - 1)]},
        "b1052_0": {"name": (19, digits), "lin": [], "guid": []},
        "b1052_1": {"name": (19, digits), "lin": [], "guid": []},
        "b251a_0": {"name": (21, digits), "lin": [(40 + s, 2, lambda k: k)], "guid": [36 + s, 58 + s]},
        "b251a_1": {"name": (21, digits), "lin": [(40 + s, 2, lambda k: k)], "guid": [36 + s, 58 + s]},
        "b210b": {"name": (23, digits), "lin": [], "guid": [36 + s]},
        "b261c": {"name": (46, digits), "guid": [62 + s, 78 + s, 180 + s], "blob": (1150 + s, 1310 + s),
                  "lin": [(1065 + s + 4 * i, 4, eid(i)) for i in range(10)]
                         + [(1337 + s, 4, lambda k: k - 1), (1343 + s, 2, lambda k: k - 1),
                            (1521 + s, 4, lambda k: k), (1846 + s, 4, lambda k: k - 1)]},
        "name_entry": {"name": (10, digits), "lin": [(29 + s, 2, lambda k: k)], "guid": [25 + s]},
    }


_STEREO_MAP_1, _STEREO_MAP_2, _STEREO_MAP_3 = _stereo_unit_map(1), _stereo_unit_map(2), _stereo_unit_map(3)


def synth_stereo_unit(k: int, tmpl1: "StereoTrackUnit", tmpl2: "StereoTrackUnit",
                      tmpl3: "StereoTrackUnit", *, unique: bool = True) -> "StereoTrackUnit":
    """Generate empty stereo track `k`'s unit by re-keying a template (tmpl1/2/3 =
    1/2/3-digit, i.e. tracks 1-9 / 10-99 / 100-512; from a 512-track control).
    Deterministic fields are byte-identical to `extract_track(512_control, k)`. The
    8-byte handle + 160 B blob are regenerated unique-per-track when `unique` (else
    left as the template's -- PT accepts duplicates). Replaces the per-N stereo
    library. The digit-matched template keeps the name length-prefix + block sizes
    correct, so the same-width digit substitution never shifts the layout."""
    digits = len(str(k))
    if digits > 3:
        raise ValueError(f"synth_stereo_unit supports tracks up to 999 (3-digit); got k={k}")
    blocks = _stereo_unit_blocks({1: tmpl1, 2: tmpl2, 3: tmpl3}[digits])
    M = {1: _STEREO_MAP_1, 2: _STEREO_MAP_2, 3: _STEREO_MAP_3}[digits]
    # The block parser scans payloads for the magic 0x5A. The deterministic fields match a
    # real control byte-for-byte (so they parse cleanly), but the GENERATED free fields are
    # the only bytes that differ -- so keep the handle + blob clear of 0x5A, else e.g.
    # handle 0xE000+346 = 0x..5A forms a phantom block when the grow re-parses (offset of
    # track 346 ~ the ~1 MB failure point). Both are free (PT needs only their presence), so
    # 0x5A -> 0x5B is harmless. (The structural 0x2624 track-count 0x5A is handled in the
    # parser; this covers the free-field 0x5A that no per-type skip could.)
    no5a = lambda b: bytes(0x5B if x == 0x5A else x for x in b)
    guid = no5a((0xE000 + k).to_bytes(2, "little"))  # high range avoids scaffold-track collisions
    blob = no5a(bytes([0x00, 0xFF, 0x7F, 0x0A, 0x00]) + bytes((k * 131 + i * 97) & 0xFF for i in range(155)))
    out = {}
    for key in _STEREO_UNIT_KEYS:
        buf = bytearray(blocks[key])
        spec = M.get(key)  # b2589 has no patch sites (its overview byte is set later); copy verbatim
        if spec is not None:
            o, w = spec["name"]
            buf[o:o + w] = str(k).encode()
            for off, wd, fn in spec["lin"]:
                buf[off:off + wd] = int(fn(k)).to_bytes(wd, "little")
            if unique:
                for off in spec["guid"]:
                    buf[off:off + 2] = guid
                if "blob" in spec:
                    a, b = spec["blob"]
                    buf[a:b] = blob
        out[key] = bytes(buf)
    return StereoTrackUnit(b1014=out["b1014"], b1052=(out["b1052_0"], out["b1052_1"]),
                           b251a=(out["b251a_0"], out["b251a_1"]), b210b=out["b210b"],
                           b261c=out["b261c"], b2589=out["b2589"], name_entry=out["name_entry"])


# --- mono: same audio structure as stereo but ONE 0x1052 lane + channel index k-1 ----
# Verified byte-exact vs extract_track(512-mono, k, channels=1) for k=2..511 (det-proof).
# The element-id base 18871 is the 512-mono control's id pool; the invariant PT needs is the
# +10/track, +1/slot stride (the absolute base is a free pool, rebuilt by the index).
_MONO_UNIT_KEYS = ("b1014", "b1052_0", "b251a_0", "b251a_1", "b210b", "b261c", "b2589", "name_entry")


def _mono_unit_blocks(u: "StereoTrackUnit") -> dict:
    return {"b1014": u.b1014, "b1052_0": u.b1052[0], "b251a_0": u.b251a[0], "b251a_1": u.b251a[1],
            "b210b": u.b210b, "b261c": u.b261c, "b2589": u.b2589, "name_entry": u.name_entry}


def _mono_unit_map(digits: int) -> dict:
    """Mono per-track patch sites (see _stereo_unit_map). One lane; channel index = k-1;
    track number = k; ten u32 element-ids 10*k+18871+i; k/k-1 counters. b2589 is verbatim."""
    s = digits - 1
    eid = lambda i: (lambda k: 10 * k + 18871 + i)
    return {
        "b1014": {"name": (19, digits), "guid": [35 + s],
                  "lin": [(25 + s, 2, lambda k: k - 1), (50 + s, 2, lambda k: k - 1)]},
        "b1052_0": {"name": (19, digits), "lin": [], "guid": []},
        "b251a_0": {"name": (21, digits), "guid": [32 + s, 54 + s], "lin": [(40 + s, 2, lambda k: k)]},
        "b251a_1": {"name": (21, digits), "guid": [32 + s, 54 + s], "lin": [(40 + s, 2, lambda k: k)]},
        "b210b": {"name": (23, digits), "guid": [32 + s], "lin": []},
        "b261c": {"name": (46, digits), "guid": [58 + s, 74 + s, 176 + s], "blob": (1111 + s, 1271 + s),
                  "lin": [(1026 + s + 4 * i, 4, eid(i)) for i in range(10)]
                         + [(1298 + s, 2, lambda k: k - 1), (1304 + s, 2, lambda k: k - 1),
                            (1482 + s, 2, lambda k: k), (1812 + s, 2, lambda k: k - 1)]},
        "name_entry": {"name": (10, digits), "guid": [21 + s], "lin": [(29 + s, 2, lambda k: k)]},
    }


_MONO_MAP_1, _MONO_MAP_2, _MONO_MAP_3 = _mono_unit_map(1), _mono_unit_map(2), _mono_unit_map(3)


def synth_mono_unit(k: int, tmpl1: "StereoTrackUnit", tmpl2: "StereoTrackUnit",
                    tmpl3: "StereoTrackUnit", *, unique: bool = True) -> "StereoTrackUnit":
    """Generate empty mono track `k`'s unit by re-keying a template (tmpl1/2/3 = 1/2/3-digit,
    from a 512-mono control). Mono counterpart of `synth_stereo_unit`: one 0x1052 lane,
    channels=1. Deterministic fields byte-identical to `extract_track(512_mono, k, channels=1)`;
    handle + blob regenerated unique-per-track (and kept clear of the 0x5A magic) when `unique`."""
    digits = len(str(k))
    if digits > 3:
        raise ValueError(f"synth_mono_unit supports tracks up to 999 (3-digit); got k={k}")
    blocks = _mono_unit_blocks({1: tmpl1, 2: tmpl2, 3: tmpl3}[digits])
    M = {1: _MONO_MAP_1, 2: _MONO_MAP_2, 3: _MONO_MAP_3}[digits]
    no5a = lambda b: bytes(0x5B if x == 0x5A else x for x in b)
    guid = no5a((0xE000 + k).to_bytes(2, "little"))
    blob = no5a(bytes([0x00, 0xFF, 0x7F, 0x0A, 0x00]) + bytes((k * 131 + i * 97) & 0xFF for i in range(155)))
    out = {}
    for key in _MONO_UNIT_KEYS:
        buf = bytearray(blocks[key])
        spec = M.get(key)  # b2589 has no patch sites -> verbatim
        if spec is not None:
            o, w = spec["name"]
            buf[o:o + w] = str(k).encode()
            for off, wd, fn in spec["lin"]:
                buf[off:off + wd] = int(fn(k)).to_bytes(wd, "little")
            if unique:
                for off in spec["guid"]:
                    buf[off:off + 2] = guid
                if "blob" in spec:
                    a, b = spec["blob"]
                    buf[a:b] = blob
        out[key] = bytes(buf)
    return StereoTrackUnit(b1014=out["b1014"], b1052=(out["b1052_0"],),
                           b251a=(out["b251a_0"], out["b251a_1"]), b210b=out["b210b"],
                           b261c=out["b261c"], b2589=out["b2589"], name_entry=out["name_entry"])


# --- MIDI: its OWN block set (no 0x1014/0x1052/0x261c), channels=0 -------------------
# Verified byte-exact vs the 8-MIDI control for k=2..8 (det-proof). Only SINGLE-DIGIT data
# exists (the control has 8 tracks), so multi-digit names + the u8->u16 widening of the
# track#/counter fields are EXTRAPOLATED and gated (raise) until a >=10-track MIDI control
# confirms them. The MIDI unit is a dict (it does not fit the audio StereoTrackUnit).
_MIDI_UNIT_KEYS = ("b1057", "b210b", "b2620", "b251a_0", "b251a_1", "b2589", "name_entry")


def extract_midi_unit(data: bytes, k: int, total: int) -> dict:
    """Extract MIDI track `k`'s unit (a dict of block bytes) from a `total`-MIDI control.
    MIDI's per-track blocks: 0x1057 (parent 0x1058), 0x210b (0x2107), 0x2620 (the big
    self-contained subtree, parent 0x2624), two lane-major 0x251a (parent 0x2519), 0x2589
    (0x258a, count N-1), and the 0x2519 name entry. NO 0x1014/0x1052/0x261c."""
    ptf = parse(data)
    bt = lambda ct: sorted(_by_type(ptf, ct), key=lambda b: b.offset)
    bb = lambda b: block_bytes(data, b)
    b2519 = _by_type(ptf, 0x2519)[0]
    fc = min(c.offset - 7 for c in b2519.child)
    entries = _name_table_entries(data[b2519.offset:fc])
    return {"b1057": bb(bt(0x1057)[k - 1]), "b210b": bb(bt(0x210b)[k - 1]),
            "b2620": bb(bt(0x2620)[k - 1]), "b251a_0": bb(bt(0x251a)[k - 1]),
            "b251a_1": bb(bt(0x251a)[total + (k - 1)]), "b2589": bb(bt(0x2589)[k - 2]),
            "name_entry": entries[k - 1]}


def _midi_unit_map(digits: int) -> dict:
    """MIDI per-track patch sites (single-digit proven). Track number = k, k-1 counters; the
    b2620 tail counter is addressed FROM the block END (its trailing u32-counted blob shifts
    absolute offsets). b2589 + the random handles are verbatim. Widths stay u8 (single-digit)."""
    s = digits - 1
    return {
        "b1057": {"name": (18, digits), "lin": []},
        "b210b": {"name": (22, digits), "lin": []},
        "b251a_0": {"name": (20, digits), "lin": [(39 + s, 1, lambda k: k)]},
        "b251a_1": {"name": (20, digits), "lin": [(39 + s, 1, lambda k: k)]},
        "b2620": {"name": (36, digits), "lin": [(183 + s, 1, lambda k: k - 1), (191 + s, 1, lambda k: k - 1),
                  (448 + s, 1, lambda k: k), ("TAIL", 18, lambda k: k - 1)]},
        "name_entry": {"name": (9, digits), "lin": [(28 + s, 1, lambda k: k)]},
    }


def synth_midi_unit(k: int, template: dict, *, unique: bool = True) -> dict:
    """Generate MIDI track `k`'s unit (dict) by re-keying a single-digit MIDI `template`
    (from `extract_midi_unit`). Deterministic fields byte-identical to the real unit; the
    random handles + b2620 trailing blob are kept verbatim from the template (PT needs them
    only present, and accepts duplicates). SINGLE-DIGIT ONLY (k <= 9)."""
    if len(str(k)) > 1:
        raise ValueError(
            f"synth_midi_unit currently supports single-digit MIDI tracks (k <= 9); got k={k}. "
            f"Multi-digit MIDI needs a >=10-track MIDI control (extrapolated map, gated).")
    M = _midi_unit_map(1)
    out = {}
    for key in _MIDI_UNIT_KEYS:
        buf = bytearray(template[key])
        spec = M.get(key)  # b2589 -> verbatim
        if spec is not None:
            o, w = spec["name"]
            buf[o:o + w] = str(k).encode()
            for off, wd, fn in spec["lin"]:
                pos, wid = (len(buf) - wd, 1) if off == "TAIL" else (off, wd)
                buf[pos:pos + wid] = int(fn(k)).to_bytes(wid, "little")
        out[key] = bytes(buf)
    return out


def grow_one_midi_track(data: bytes, base_n: int, unit: dict) -> bytes:
    """Insert one empty MIDI track (-> base_n+1 tracks) into the body `data` (no final index).
    `unit` is a MIDI unit dict (from `synth_midi_unit`/`extract_midi_unit`). MIDI adds 0 audio
    channels (0x1054 unchanged). The MIDI counterpart of `grow_one_track`: its own block set
    (0x1057, 0x2620, 0x210b, 0x251a x2 lane-major, 0x2589, name entry) appended after the last
    instance in each parent. The MIDI INDEX side is `final_index.add_midi_track` (pass the track
    index in `compose_index(..., midi_tracks=...)`)."""
    n = base_n + 1
    ptf = parse(data)
    pm = _parent_zmarks(ptf)
    ins: list[Insertion] = []

    def append_after(block: Block, blob: bytes) -> None:
        ins.append(Insertion(block.offset + block.block_size, blob, pm[block.offset - 7]))

    append_after(_by_type(ptf, 0x1057)[-1], unit["b1057"])   # MIDI I/O (parent 0x1058)
    append_after(_by_type(ptf, 0x2620)[-1], unit["b2620"])   # MIDI playlist subtree (parent 0x2624)
    append_after(_by_type(ptf, 0x210b)[-1], unit["b210b"])
    a51 = _by_type(ptf, 0x251a)                               # lane-major: lane0 mid-run, lane1 end
    append_after(a51[base_n - 1], unit["b251a_0"])
    append_after(a51[-1], unit["b251a_1"])
    append_after(_by_type(ptf, 0x2589)[-1], unit["b2589"])   # overview (parent 0x258a, count N-1)

    b2519 = _by_type(ptf, 0x2519)[0]                          # name entry before the first child
    first_child = min(c.offset - 7 for c in b2519.child)
    ins.append(Insertion(first_child, unit["name_entry"], b2519.offset - 7))

    for b in _by_type(ptf, 0x202a):                          # ordinal append (u16 before fe ff)
        s = b.offset - 7
        seg = data[s : b.offset + b.block_size]
        idx0 = seg.find(b"\xff\xff\xff\xff\x00\x00")
        feff = seg.find(b"\xfe\xff", idx0 + 8)
        ins.append(Insertion(s + feff, (n - 1).to_bytes(2, "little"), s))

    body = bytearray(apply_insertions(data, ptf, ins))
    _patch_counts(body, n, channel_count(bytes(body)))       # MIDI = 0 channels (no change to 0x1054)
    return bytes(body)


def add_click(data: bytes, clean_ref: bytes, click_ref: bytes) -> bytes:
    """Add the session's (single) click track to audio session `data` (full unxored
    bytes, body + index), placed as the LAST track, via the structural diff-replay in
    `click_clone`. Returns full unxored bytes (ready for `writer.encrypt_session_data`).

    `clean_ref` / `click_ref` are a CLEAN CONTROL PAIR sharing the same audio: an
    audio-only session and the SAME session with a Click track added (e.g.
    `lots of stereo tracks/{N stereo tracks, N stereo plus click}.ptx`). The recursive
    diff between them isolates the click's exact contribution (playlist 0x261e + the
    click-plugin block, the two overview 0x2589 entries, the per-track 0x200b chains, the
    "Click 1" 0x2519 name entry, the 0x1018 plugin-registry growth, and the session
    click registration 0x2064); replaying it onto `data` adds the click.

    `clean_ref`'s audio LAYOUT must match `data`'s (same track count + types) so the
    diff's splice offsets line up with `data`'s blocks. This is the same kind of
    constraint the uniform synthesizers place on their library (a matching-N control).
    BYTE-EXACT when `data` IS `clean_ref` (validated 1 and 2 stereo + click); for a
    structurally-identical synthesized `data` the click still splices in correctly.
    PT-confirmed for 1 audio + click.

    NOTE: the click's 0x2064 carries `click_ref`'s embedded session path; if `data` is
    a different session folder than the ref, path-normalize the result (the click-only
    path chimera). Same-session (the validation case) needs no normalization."""
    from . import click_clone as _CC
    patch = _CC.derive_click_patch(clean_ref, click_ref)
    return _CC.apply_click_patch(data, patch, data)


def add_click_anyN(data: bytes, clean_ref: bytes, click_ref: bytes, at_top: bool = False) -> bytes:
    """Add a Click track to an audio session `data` of ANY track count, sourcing the click from
    a clean/click control PAIR of a DIFFERENT (smaller) layout. Unlike `add_click` (which needs
    `clean_ref`'s audio layout to match `data`), this uses the layout-independent structural
    re-key in `click_clone` (Frontier 1): it splices the click's fixed footprint by owner
    SIGNATURE (end-anchored), replicates the per-audio-track 0x261b rewrite across every track,
    rebuilds the click's 0x2519 name-table entry (incl. the `02 00` separator) + its two
    lane-major 0x251a lanes, and re-stamps the track count baked into the click's own bytes.

    The click is placed LAST; `data`'s own session name/path is preserved (the click control's
    0x2067 is NOT transplanted). `clean_ref`/`click_ref` should be a >= 2-audio pair (so the
    per-track rep is detectable), e.g. `2 stereo tracks.ptx` + `2 stereo plus click.ptx`.
    PT-CONFIRMED at N=3 and N=12 (stereo). Returns full unxored bytes (ready for
    `writer.encrypt_session_data`).

    `at_top=True` places the Click as the FIRST (top) track instead of the bottom,
    via `move_click_to_top` (the edit-window display order lives in the index's
    playlist-order list). PT-CONFIRMED at N=2 and N=12 (stereo).

    NAME-ROBUST: the structural re-key matches the target's audio tracks by their canonical
    `Audio N` names, so a click added AFTER tracks were renamed used to silently drop (no
    `Audio N` to match). This now normalizes the audio tracks to `Audio N` for the splice
    (routed through unique temporaries, so even a non-first `Audio N` name can't collide),
    then restores the original names afterward — the PT-confirmed names-last direction, and
    BYTE-IDENTICAL to building with canonical names and renaming last. So the click no longer
    depends on rename ORDER (you may rename before or after; the old 'rename last' rule is gone)."""
    from . import click_clone as _CC
    tt = track_types(data)
    aidx = [i for i, t in enumerate(tt) if t.kind in ("mono", "stereo")]
    canon = {i: f"Audio {k + 1}" for k, i in enumerate(aidx)}
    need_norm = any(tt[i].name != canon[i] for i in aidx)
    d = data
    if need_norm:                       # rename audio tracks -> Audio N for the splice
        d = set_track_names(d, {i: f"ClkNormTmp{k}" for k, i in enumerate(aidx)})  # unique temps
        d = set_track_names(d, canon)                                              # -> canonical
    spatch = _CC.derive_click_patch_structural(clean_ref, click_ref)
    out = _CC.apply_click_patch_structural(d, spatch, d)
    if at_top:
        if any(t.kind == "midi" for t in track_types(out)):
            # move_click_to_top's display list is audio-only (drops MIDI 0x2620); use the
            # MIDI-aware index reorder to put the click first, rest in current display order.
            cur = _container_display_offsets(out)        # [existing display..., Click]
            tpo = {o: i for i, (o, _t) in enumerate(track_playlist_order(out))}
            out = reorder_tracks(out, [tpo[o] for o in [cur[-1]] + cur[:-1]])
        else:
            out = move_click_to_top(out)
    if need_norm:                       # restore the caller's original audio-track names
        now = track_types(out)          # click is not an audio track; audio order is preserved
        anow = [i for i, t in enumerate(now) if t.kind in ("mono", "stereo")]
        out = set_track_names(out, {i: tt[aidx[k]].name for k, i in enumerate(anow)})
    # This splices the click's per-track subtree from a DONOR pair, so the result carries the
    # DONOR's 0x261B identity ranges -- and, applied to a session of a different size, carries some
    # of them TWICE. Two tracks sharing a range are one object to Pro Tools (see 0x261B in the
    # spec): it crashes on close. Measured on PT's own `4 mono tracks.ptx`, this produced
    # [13681, 13691, 13681, 13681, 13701] -- two collisions -- and validate() flagged it
    # `track-id-uniqueness`. The ids are session-scoped identities, so making them unique is enough;
    # they do not have to be the session's original numbers.
    # ...and renumber the playlist ordinals. The click is cloned from a DONOR click track and
    # inherits its ordinal (3), so a 4-track session with a click on top came out [3,1,2,3,4] --
    # a REPEAT, and Pro Tools resolves a track through this ordinal, so two tracks on one slot
    # become one object and it crashes on close (PT-confirmed 2026-07-27). EVERY click-bearing
    # session this writer produced carried one. No-op when the column is already correct.
    return _normalize_one_playlist_ordinals(_reallocate_duplicate_track_ids(out))


# --- content transplant (clip / tempo / meter) from a matched control pair -----
#
# A whole class of session content — an audio clip, a tempo change, a meter change —
# is added the SAME way Pro Tools writes it: a handful of top-level blocks grow or
# change while the rest of the session is untouched. Given a CLEAN/MODIFIED control
# PAIR (the same session without / with the content), the contribution is exactly the
# set of top-level blocks that differ. `_transplant_top_level` copies a chosen set of
# those blocks from the modified ref onto `data`, keeps `data`'s own identity/display,
# and robustly repairs the master index with `final_index.reindex_after_resize`
# (capturing the index entries from the ref, whose index still parses, and refilling
# every offset for the assembled layout — also rewriting the first-block index
# pointer). `data` must share scaffold with the clean ref so the blocks line up — the
# same matched-pair constraint as `add_click`. BYTE-EXACT when `data` IS the clean ref.


def changed_top_level_types(clean_ref: bytes, mod_ref: bytes,
                            exclude: "tuple[int, ...]" = (0x2067, 0x0002)) -> list[int]:
    """The content_types of top-level blocks that DIFFER (in bytes) between a matched
    clean/modified control pair, excluding session-identity (0x2067 name/MRU) and the
    master index (0x0002). This is the content's structural footprint."""
    c = {b.content_type: b for b in parse(clean_ref).blocks}
    mblocks = parse(mod_ref).blocks
    # the first top-level block is the index-offset pointer (its content_type encodes the
    # index offset's low u16); it is DERIVED (set by reindex), never transplanted — and
    # its type shifts whenever a block is resized, so it must not enter the change set.
    first_off = min(b.offset for b in mblocks)
    m = {b.content_type: b for b in mblocks}
    out: list[int] = []
    for ct, mb in m.items():
        if ct in exclude or mb.offset == first_off:
            continue
        cb = c.get(ct)
        mbytes = mod_ref[mb.offset - 7 : mb.offset + mb.block_size]
        cbytes = clean_ref[cb.offset - 7 : cb.offset + cb.block_size] if cb else None
        if cbytes != mbytes:
            out.append(ct)
    return out


def _transplant_top_level(data: bytes, ref: bytes, types) -> bytes:
    """Replace each top-level block of `data` whose content_type is in `types` with
    `ref`'s block of that type, append `ref`'s master index (it carries the content's
    index entries), then repair every index offset for the assembled layout via
    `final_index.reindex_after_resize`. Returns full unxored bytes."""
    types = set(types)
    ref_by_type = {b.content_type: b for b in parse(ref).blocks}
    for ct in types:
        if ct not in ref_by_type:
            raise ValueError(f"ref missing top-level block 0x{ct:04x}")
    data_refs = W.top_level_refs(data)
    if not data_refs or data_refs[-1].block.content_type != 0x0002:
        raise ValueError("data has no trailing 0x0002 master index")

    out = bytearray(data[: data_refs[0].start])  # file header (PT version + XOR info)
    for r in data_refs[:-1]:  # body blocks only (exclude the trailing 0x0002 index)
        ct = r.block.content_type
        if ct in types:
            rb = ref_by_type[ct]
            out += ref[rb.offset - 7 : rb.offset + rb.block_size]
        else:
            out += r.data
    ref_index = _FI.final_index_ref(ref)
    out += ref[ref_index.start :]
    return _FI.reindex_after_resize(ref, bytes(out))


def _grow_blocks_from(data: bytes, ref: bytes, types) -> bytes:
    """Like `_transplant_top_level`, but for GROW-only changes that add NO index holes
    (tempo/meter/marker maps just enlarge existing session-global blocks). Swaps the
    `types` blocks from `ref` into `data`, keeps DATA's own master index, and reindexes
    against DATA — so `data` may have any track count independent of `ref` (the conductor
    is track-agnostic). `_transplant_top_level` reindexes against the ref, which corrupts
    the index when data's track count differs from ref's; this preserves data's holes."""
    types = set(types)
    ref_by_type = {b.content_type: b for b in parse(ref).blocks}
    for ct in types:
        if ct not in ref_by_type:
            raise ValueError(f"ref missing top-level block 0x{ct:04x}")
    data_refs = W.top_level_refs(data)
    if not data_refs or data_refs[-1].block.content_type != 0x0002:
        raise ValueError("data has no trailing 0x0002 master index")
    out = bytearray(data[: data_refs[0].start])
    for r in data_refs[:-1]:
        ct = r.block.content_type
        if ct in types:
            rb = ref_by_type[ct]
            out += ref[rb.offset - 7 : rb.offset + rb.block_size]
        else:
            out += r.data
    data_index = _FI.final_index_ref(data)
    out += data[data_index.start :]              # keep DATA's index (its track holes)
    return _FI.reindex_after_resize(data, bytes(out))


def _grow_blocks_into(data: bytes, blocks_by_type: "dict[int, bytes]") -> bytes:
    """Replace `data`'s top-level blocks (keyed by content_type) with the given new blocks
    (resized session-global blocks that add NO index holes — a tempo/meter/marker map), keep
    DATA's own master index, and reindex against DATA. The donor-free counterpart of
    `_grow_blocks_from`: the new blocks are supplied directly (built from inlined templates)
    instead of pulled from a reference session — so only the named blocks change and DATA
    keeps everything else (its own view-state, I/O, etc.)."""
    data_refs = W.top_level_refs(data)
    if not data_refs or data_refs[-1].block.content_type != 0x0002:
        raise ValueError("data has no trailing 0x0002 master index")
    present = {r.block.content_type for r in data_refs[:-1]}
    for ct in blocks_by_type:
        if ct not in present:
            raise ValueError(f"data missing top-level block 0x{ct:04x}")
    out = bytearray(data[: data_refs[0].start])
    for r in data_refs[:-1]:
        ct = r.block.content_type
        out += blocks_by_type[ct] if ct in blocks_by_type else r.data
    out += data[_FI.final_index_ref(data).start :]
    return _FI.reindex_after_resize(data, bytes(out))


def _grow_block_into(data: bytes, content_type: int, new_block: bytes) -> bytes:
    """Replace `data`'s single top-level block of `content_type` (see `_grow_blocks_into`)."""
    return _grow_blocks_into(data, {content_type: new_block})


# Session/hardware-config blocks (I/O routing/channels + a few session-state blocks)
# that Pro Tools may rewrite on save independently of any content. They never carry an
# audio/MIDI clip, so a content transplant must keep the TARGET's version — otherwise a
# reference saved in a different context would import its I/O routing. Excluding them is
# a no-op for a same-session control pair (they don't appear in that pair's delta).
_REF_VOLATILE_TYPES = frozenset({0x1022, 0x2603, 0x2033, 0x2058, 0x4823})


def add_audio_clip(data: bytes, clean_ref: bytes, clip_ref: bytes) -> bytes:
    """Add audio clip(s) to session `data` by transplanting the clip contribution from
    a matched control PAIR: `clean_ref` (a session WITHOUT the clip) and `clip_ref` (the
    SAME session WITH the clip(s) added). Mirrors `add_click`.

    Transplants every top-level block that differs between the pair (minus session
    identity, the index, and `_REF_VOLATILE_TYPES`): the name table 0x2519, the
    audio-file path index 0x0f3d, the file table 0x1004 (wav descriptor 0x1003 + the
    "Audio Files" 0x103a list), the region library 0x262a (the .L/.R 0x2629 regions),
    the placement table 0x1054 (per-lane 0x1050/0x104f placements — one set per clip),
    and the edit playlist 0x2624 (which carries each clip's timeline 3-point). The
    master index is repaired by `_transplant_top_level`; `data` keeps its own name.

    Because it copies whatever the pair differs by, `clip_ref` can hold ONE clip, MANY
    clips on a track (`two clips same track.ptx` -> 4 placements, reused regions), or a
    clip of a DIFFERENT wav (`clip diff wav.ptx`) — each is reproduced byte-exact (mod
    session-name) when `data` IS `clean_ref`. `data`/`clean_ref` must share audio layout
    so the blocks line up. Returns full unxored bytes (ready for `encrypt_session_data`).
    PT-CONFIRMED for 1 stereo + 1 clip."""
    types = [t for t in changed_top_level_types(clean_ref, clip_ref)
             if t not in _REF_VOLATILE_TYPES]
    return _transplant_top_level(data, clip_ref, types)


# Session-global, track-AGNOSTIC clip blocks: the file table (wav descriptor), the
# region library (the .L/.R regions) and the audio-file path index. Identical
# regardless of which track the clip lands on, so they transplant wholesale.
_CLIP_GLOBAL_TYPES = (0x1004, 0x262A, 0x0F3D)


def _clip_lane_tails(clip_ref: bytes) -> "tuple[bytes, bytes]":
    """The two clip-bearing 0x1052 lanes' bytes-after-name (channel 0 = .L, channel
    1 = .R), in lane order. These tails (`01 00 00 00` + a 0x1050/0x104f placement)
    are track-INDEPENDENT — only the lane's name differs between tracks, and that is
    kept from the target lane. Pulled from any 1-clip session's clip lanes."""
    import struct
    p = parse(clip_ref)
    lanes = [b for b in flat_blocks(p)
             if b.content_type == 0x1052 and any(c.content_type == 0x1050 for c in b.child)]
    if len(lanes) < 2:
        raise ValueError("clip_ref has no stereo clip lane to copy")

    def tail(lane: Block) -> bytes:
        lb = block_bytes(clip_ref, lane)
        namelen = struct.unpack_from("<I", lb, 9)[0]
        return lb[13 + namelen:]
    return tail(lanes[0]), tail(lanes[1])


def _insert_clip_lanes(b1054: bytes, track_index: int, tail0: bytes, tail1: bytes) -> bytes:
    """Rebuild a 0x1054 placement block, turning track `track_index`'s two empty
    0x1052 lanes into clip lanes (keep each lane's name, swap its after-name tail for
    the clip tail). Lanes are track-major (2 per stereo track); block_size is bumped."""
    import struct
    out = bytearray(b1054[:9])  # zmark + btype + size + content_type
    i, lane = 9, 0
    while i < len(b1054):
        if b1054[i] != 0x5A:
            out += b1054[i:i + 1]; i += 1; continue
        csize = struct.unpack_from("<I", b1054, i + 3)[0]
        cct = struct.unpack_from("<H", b1054, i + 7)[0]
        child = b1054[i:i + 7 + csize]
        if cct == 0x1052:
            if lane in (2 * track_index, 2 * track_index + 1):
                namelen = struct.unpack_from("<I", child, 9)[0]
                tail = tail0 if lane == 2 * track_index else tail1
                newc = bytearray(child[:13 + namelen] + tail)
                struct.pack_into("<I", newc, 3, len(newc) - 7)
                child = bytes(newc)
            lane += 1
        out += child
        i += 7 + csize
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def _synth_clip_index(clean_data: bytes, target_body: bytes) -> bytes:
    """Synthesize the 0x0002 index for a clip-bearing body from a CLIP-FREE session's
    records. PT-CONFIRMED that a clip adds exactly one index hole: a 0x0f3c marker
    (the path-volume pointer) appended to the single 0x0f3d path record; no existing
    indexed block changes rank. So: take `clean_data`'s records, refill every offset
    against `target_body`'s layout (resolving each hole's type by the block it points
    at, K-independent), then append the 0x0f3c marker. Reproduces the real control's
    index byte-exact."""
    import copy
    cref = _FI.final_index_ref(clean_data)
    czt, cbt = _FI.block_layout(clean_data)
    crecs = _FI.parse_records(cref.data, set(cbt), set(czt))
    _tzt, tbt = _FI.block_layout(target_body)
    recs = copy.deepcopy(crecs)

    def refill(off: int) -> int:
        t = czt.get(off)
        return tbt[t][cbt[t].index(off)] if t is not None else off

    for r in recs:
        for c in r.child_refs:
            c.offset = refill(c.offset)
        for e in r.elements:
            e.offsets = [refill(o) for o in e.offsets]
    path_records = [r for r in recs if r.content_type == 0x0F3D]
    if len(path_records) != 1:
        raise ValueError(f"expected one 0x0f3d index record, found {len(path_records)}")
    path_records[0].elements.append(_FI.Element(rel=0, tag1=0x04, offsets=[tbt[0x0F3C][0]]))
    return _FI.serialize_final_block(recs)


def add_clip_to_track(data: bytes, clip_ref: bytes, track_index: int,
                      *, position_file_samples: "int | None" = None) -> bytes:
    """Insert ONE stereo audio clip onto track `track_index` (0-based) of the clean
    (clip-free) N-stereo session `data`, generalizing clip placement to ANY track of
    ANY N. `clip_ref` is any 1-clip session (e.g. `one clip bar 2.ptx`) supplying the
    track-agnostic wav/region/path blocks and the placement lane content.

    Recipe (PT-CONFIRMED on tracks 1/2/3 of a 3-stereo): transplant the session-global
    clip blocks (`_CLIP_GLOBAL_TYPES`) from `clip_ref`; turn track K's two 0x1052 lanes
    into clip lanes (`_insert_clip_lanes`, channel 0/.L and 1/.R by lane order); and
    SYNTHESIZE the master index (`_synth_clip_index`) — the placement plus that index
    are the entire load-bearing footprint (the 0x2519 clip flag and 0x2624 position
    block are display state Pro Tools rebuilds). The clip's timeline start lives solely
    in the 0x104f field, so pass `position_file_samples` to place it (else it inherits
    `clip_ref`'s). `data` must be clip-FREE (single clip); returns full unxored bytes."""
    import struct
    ref_by = {b.content_type: b for b in parse(clip_ref).blocks}
    for ct in _CLIP_GLOBAL_TYPES:
        if ct not in ref_by:
            raise ValueError(f"clip_ref missing global clip block 0x{ct:04x}")
    tail0, tail1 = _clip_lane_tails(clip_ref)

    refs = W.top_level_refs(data)
    if not refs or refs[-1].block.content_type != 0x0002:
        raise ValueError("data has no trailing 0x0002 master index")
    body = bytearray(data[: refs[0].start])
    for r in refs[:-1]:  # body blocks (exclude the trailing index)
        ct = r.block.content_type
        if ct in _CLIP_GLOBAL_TYPES:
            rb = ref_by[ct]
            body += clip_ref[rb.offset - 7 : rb.offset + rb.block_size]
        elif ct == 0x1054:
            body += _insert_clip_lanes(r.data, track_index, tail0, tail1)
        else:
            body += r.data
    body = bytes(body)
    out = body + _synth_clip_index(data, body)
    out = _FI._set_first_block_index_pointer(out, len(body))
    if position_file_samples is not None:
        out = set_clip_position(out, position_file_samples)
    return out


def _count_block_parts(data: bytes, ct: int, child_ct: int):
    """Split a count-prefixed table block (0x1004 file table / 0x262a region lib) into
    (header9, prefix, [child_item_bytes], trailer). The block content is a u32 count at
    [0] + a prefix (list/header) + the repeated `child_ct` items + a trailer; the parser
    skips the count/inter-section u32s, so we capture them as prefix/trailer spans."""
    import struct
    blk = [b for b in parse(data).blocks if b.content_type == ct][0]
    items = [c for c in blk.child if c.content_type == child_ct]
    cstart = blk.offset + 2  # content starts after the 2-byte content_type
    first = (items[0].offset - 7) - cstart
    last = (items[-1].offset + items[-1].block_size) - cstart
    content = block_bytes(data, blk)[9:]
    return block_bytes(data, blk)[:9], content[:first], [block_bytes(data, it) for it in items], content[last:]


def _rebuild_count_block(header9: bytes, prefix: bytes, items, trailer: bytes, count: int) -> bytes:
    """Reassemble a count-prefixed table block from parts with `count` at content[0]."""
    import struct
    content = bytearray(prefix) + b"".join(items) + trailer
    struct.pack_into("<I", content, 0, count)
    out = bytearray(header9) + content
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def _all_clip_lane_tails(clip_ref: bytes) -> "list[bytes]":
    """Every clip-bearing 0x1052 lane's bytes-after-name, in lane order
    (clip0 .L, clip0 .R, clip1 .L, clip1 .R, ...). Each tail's 0x104f payload[2] is the
    region index it binds to (0,1,2,…) — the linkage is positional, no GUIDs."""
    import struct
    out = []
    for L in (b for b in flat_blocks(parse(clip_ref))
              if b.content_type == 0x1052 and any(c.content_type == 0x1050 for c in b.child)):
        lb = block_bytes(clip_ref, L)
        out.append(lb[13 + struct.unpack_from("<I", lb, 9)[0]:])
    return out


def _place_clip_lanes(b1054: bytes, assignments, tails) -> bytes:
    """Rebuild a 0x1054 placing clip `ci` (tails[2ci],tails[2ci+1]) on track
    `assignments[ci]`'s two lanes. Each tail keeps its region-index (payload[2])."""
    import struct
    track_tail = {t: (tails[2 * ci], tails[2 * ci + 1]) for ci, t in enumerate(assignments)}
    out = bytearray(b1054[:9])
    i, lane = 9, 0
    while i < len(b1054):
        if b1054[i] != 0x5A:
            out += b1054[i:i + 1]; i += 1; continue
        csize = struct.unpack_from("<I", b1054, i + 3)[0]
        cct = struct.unpack_from("<H", b1054, i + 7)[0]
        child = b1054[i:i + 7 + csize]
        if cct == 0x1052:
            tk = lane // 2
            if tk in track_tail:
                namelen = struct.unpack_from("<I", child, 9)[0]
                newc = bytearray(child[:13 + namelen] + track_tail[tk][lane % 2])
                struct.pack_into("<I", newc, 3, len(newc) - 7)
                child = bytes(newc)
            lane += 1
        out += child
        i += 7 + csize
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def add_clips_to_tracks(data: bytes, clip_ref: bytes, track_indices,
                        *, position_file_samples: "int | None" = None) -> bytes:
    """Place MULTIPLE stereo clips — one per entry of `track_indices` (0-based) — onto
    the clean N-stereo session `data`, in one shot. `clip_ref` is a session whose M
    clips supply the wav/region/path content; `len(track_indices)` must equal M, and
    clip i lands on track `track_indices[i]`.

    PT-CONFIRMED via the `3 stereo 3 different clips.ptx` byte-pair. Multi-clip is the
    single-clip recipe scaled: the file table 0x1004 (count + filename list + M wav
    descriptors), the region library 0x262a (2M regions), and the placements 0x1054
    (each clip's two lanes carry region index 2i / 2i+1 via 0x104f payload[2] — the
    region<->placement link is POSITIONAL, no GUIDs) are assembled from `clip_ref`; all
    M clips SHARE ONE 0x0f3d path-volume, so the index still gains exactly ONE 0x0f3c
    marker (`_synth_clip_index` is unchanged from the single-clip case). `data` must be
    clip-FREE. `position_file_samples` places the clips: an int moves them ALL to one
    position (the head-sync case); a list/tuple (one entry per clip, parallel to
    `track_indices`) gives each clip its OWN position (each clip's start is an
    independent 0x104f field). Returns full unxored bytes.

    Reproduces the control's load-bearing blocks (0x1004/0x262a/0x0f3d/0x1054/index)
    byte-exact; 0x2519/0x2624/0x2016/0x2587 are display state Pro Tools rebuilds."""
    cp = parse(clip_ref)
    h4, pre4, units, tr4 = _count_block_parts(clip_ref, 0x1004, 0x1003)
    h2a, pre2a, regs, tr2a = _count_block_parts(clip_ref, 0x262A, 0x2629)
    m = len(units)
    if len(track_indices) != m:
        raise ValueError(f"clip_ref has {m} clips but {len(track_indices)} tracks given")
    tails = _all_clip_lane_tails(clip_ref)
    ref_by = {b.content_type: b for b in cp.blocks}
    if 0x0F3D not in ref_by:
        raise ValueError("clip_ref missing the 0x0f3d path block")
    f3d_block = block_bytes(clip_ref, ref_by[0x0F3D])
    blk1004 = _rebuild_count_block(h4, pre4, units, tr4, m)
    blk262a = _rebuild_count_block(h2a, pre2a, regs, tr2a, 2 * m)

    refs = W.top_level_refs(data)
    if not refs or refs[-1].block.content_type != 0x0002:
        raise ValueError("data has no trailing 0x0002 master index")
    body = bytearray(data[: refs[0].start])
    for r in refs[:-1]:
        ct = r.block.content_type
        if ct == 0x1004:
            body += blk1004
        elif ct == 0x262A:
            body += blk262a
        elif ct == 0x0F3D:
            body += f3d_block
        elif ct == 0x1054:
            body += _place_clip_lanes(r.data, list(track_indices), tails)
        else:
            body += r.data
    body = bytes(body)
    out = body + _synth_clip_index(data, body)
    out = _FI._set_first_block_index_pointer(out, len(body))
    if position_file_samples is not None:
        if isinstance(position_file_samples, (list, tuple)):
            if len(position_file_samples) != m:
                raise ValueError(f"{len(position_file_samples)} positions for {m} clips")
            out = _set_clip_positions(out, {track_indices[i]: position_file_samples[i]
                                            for i in range(m)})
        else:
            out = set_clip_position(out, position_file_samples)
    return out


def _set_clip_positions(data: bytes, track_to_pos: "dict[int, int]") -> bytes:
    """Set each given track's clip to its own timeline position (FILE samples). Finds
    track K's two 0x1052 lanes by name ("Audio K+1") and writes the 0x104f position
    field (payload[7:15]) of each — so different clips can sit at different positions."""
    import struct
    out = bytearray(data)
    for lane in flat_blocks(parse(bytes(out))):
        if lane.content_type != 0x1052:
            continue
        lb = block_bytes(bytes(out), lane)
        namelen = struct.unpack_from("<I", lb, 9)[0]
        name = lb[13:13 + namelen].decode("latin1", "replace")
        if not name.startswith("Audio "):
            continue
        try:
            track = int(name.split()[1]) - 1
        except (IndexError, ValueError):
            continue
        if track not in track_to_pos:
            continue
        field = struct.pack("<Q", int(track_to_pos[track]))
        for gain in lane.child:
            for plc in gain.child:
                if plc.content_type == 0x104F:
                    out[plc.offset + 9 : plc.offset + 17] = field
    return bytes(out)


def wav_clip_identity(wav_path) -> "tuple[int, bytes, bytes]":
    """Read the Pro-Tools-relevant identity from a BWF/PT WAV:
    (sample_count, umid_material, id2). `sample_count` = data-chunk bytes / fmt block
    align. `umid_material` = the 8-byte `umid` chunk body `2a <hash4> ef <B> 80` (the
    file's content ID Pro Tools matches on). `id2` = the 2 bytes at the bext SMPTE-UMID
    marker (`06 0a 2b 34`) + 24. Raises if the WAV lacks `fmt`/`data`/`umid`/`bext`
    (a raw WAV with no BWF UMID can't be linked without first wrapping it)."""
    import struct
    data = open(wav_path, "rb").read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"not a RIFF/WAVE file: {wav_path}")
    chunks, i = {}, 12
    while i + 8 <= len(data):
        cid = data[i:i + 4]; sz = struct.unpack("<I", data[i + 4:i + 8])[0]
        chunks[cid] = (i + 8, sz); i += 8 + sz + (sz & 1)
    for need in (b"fmt ", b"data", b"umid", b"bext"):
        if need not in chunks:
            raise ValueError(f"WAV has no {need.decode().strip()!r} chunk (not a PT/BWF WAV): {wav_path}")
    fo, _ = chunks[b"fmt "]; block_align = struct.unpack("<H", data[fo + 12:fo + 14])[0]
    _do, dsz = chunks[b"data"]; sample_count = dsz // max(1, block_align)
    uo, _ = chunks[b"umid"]; umid_material = data[uo + 7:uo + 15]
    bo, _ = chunks[b"bext"]; bext = data[bo:bo + 602]
    m = bext.find(bytes.fromhex("060a2b34"))
    id2 = bext[m + 24:m + 26]
    return sample_count, umid_material, id2


def _wav_chunks(data: bytes) -> "list[tuple[bytes, int, int]]":
    out, i = [], 12
    while i + 8 <= len(data):
        import struct
        cid = data[i:i + 4]; sz = struct.unpack("<I", data[i + 4:i + 8])[0]
        out.append((cid, i + 8, sz)); i += 8 + sz + (sz & 1)
    return out


def wrap_raw_wav(raw_data: bytes, template_wav: bytes, *, seed: str = "ptformat") -> bytes:
    """Wrap a RAW WAV (one with only `fmt`/`data`, no Pro Tools BWF identity) into a
    PT-friendly container so `set_clip_wav` can link it. Grafts the raw audio into
    `template_wav`'s chunk structure (a known-good PT/BWF WAV) and writes a freshly
    generated SMPTE-UMID — deterministic from `seed` + the audio — consistently into the
    `umid`, `regn`, and `bext` chunks (and the frame count into `regn`). Returns the
    wrapped WAV bytes; stage them in the session `Audio Files` folder, then pass to
    `set_clip_wav` (which reads back the generated identity).

    The raw WAV must match the template's PCM format (the clip controls are 44.1 kHz /
    24-bit / stereo); other sample rates / bit depths / channel counts need conversion
    first. The generated UMID is content+seed-derived so it is stable and effectively
    unique. NOTE: a wrapped stereo clip currently maps to one channel pending the
    stereo-lane channel decode."""
    import struct, hashlib
    rc = {cid: (o, sz) for cid, o, sz in _wav_chunks(raw_data)}
    for need in (b"fmt ", b"data"):
        if need not in rc:
            raise ValueError(f"raw WAV has no {need.decode().strip()!r} chunk")
    raw_fmt = raw_data[rc[b"fmt "][0]:rc[b"fmt "][0] + rc[b"fmt "][1]]
    rdo, rdsz = rc[b"data"]; pcm = raw_data[rdo:rdo + rdsz]
    block_align = struct.unpack("<H", raw_fmt[12:14])[0]
    frames = rdsz // max(1, block_align)

    h = hashlib.sha256(seed.encode("utf-8", "surrogateescape") + pcm[:4096]).digest()
    hash4, b_byte, id2 = h[0:4], bytes([h[4] & 0x7F or 1]), h[5:7]
    material = b"\x2a" + hash4 + b"\xef" + b_byte + b"\x80"  # 0x1001/regn umid material

    body = bytearray()
    for cid, o, sz in _wav_chunks(template_wav):
        chunk = bytearray(template_wav[o:o + sz])
        if cid == b"data":
            chunk = bytearray(pcm)
        elif cid == b"fmt ":
            chunk = bytearray(raw_fmt)
        elif cid == b"umid":
            chunk[7:15] = material
        elif cid == b"regn":
            chunk[11:19] = material
            chunk[28:32] = int(frames).to_bytes(4, "little")
        elif cid == b"bext":
            mk = chunk.find(bytes.fromhex("060a2b34"))
            chunk[mk + 16:mk + 20] = hash4; chunk[mk + 21:mk + 22] = b_byte; chunk[mk + 24:mk + 26] = id2
        body += cid + struct.pack("<I", len(chunk)) + chunk
        if len(chunk) & 1:
            body += b"\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + bytes(body)


def set_clip_wav(clip_data: bytes, wav_path, *, filename: "str | None" = None,
                 region_name: "str | None" = None) -> bytes:
    """Re-point the single audio clip in `clip_data` (a 1-stereo session with one clip,
    e.g. `one clip bar 1.ptx`) at an arbitrary Pro-Tools/BWF WAV, so Pro Tools opens it
    and AUTO-LINKS to that WAV with no relink prompt. The WAV must be staged in the
    session's `Audio Files` folder under `filename` (default: the WAV's basename); Pro
    Tools names the region after the filename stem (override with `region_name`).

    What it patches (the full set Pro Tools resolves files by — PT-CONFIRMED):
      * 0x103a filename + 0x2628 region names (`<stem>.L` / `.R`)
      * 0x1001 length + 0x2628 region length = the WAV's frame count
      * the WAV's umid material (`wav_clip_identity`): in 0x1003 the 0x1001 copy keeps
        its `2a` marker (offset +44), but the 0x2106 copy is `00`-prefixed (offset +292,
        NOT `2a` — that stray byte is the subtle relink bug); the 2-byte id at +301
    Per-file timestamp (0x1003 +100/+172) and private file id (+202) do NOT gate linking
    (Pro Tools links the same WAV regardless), so they keep the donor's values and need
    no WAV mtime coherence. Returns full unxored bytes (ready for `encrypt_session_data`).

    NOTE: only PT/BWF WAVs (with a `umid`/`bext` UMID) can be linked this way; a raw WAV
    has no fingerprint to match and would need to be wrapped/imported first."""
    sample_count, umid_material, id2 = wav_clip_identity(wav_path)
    import os
    if filename is None:
        filename = os.path.basename(str(wav_path))
    stem = region_name if region_name is not None else filename.rsplit(".wav", 1)[0]

    parsed = parse(clip_data)
    # detect the existing clip's filename (0x103a audio entry) and region stem (0x2628)
    ft = [b for b in parsed.blocks if b.content_type == 0x1004][0]
    old_filename = None
    for ch in ft.child:
        if ch.content_type != 0x103A or ch.block_size <= 10:
            continue
        body = clip_data[ch.offset : ch.offset + ch.block_size]
        m = re.search(rb"([ -~]{4,})\.wav", body)
        if m:
            old_filename = m.group(0).decode("latin1"); break
    region = [b for b in flat_blocks(parsed) if b.content_type == 0x2628]
    rb = clip_data[region[0].offset - 7 : region[0].offset + region[0].block_size]
    rl = int.from_bytes(rb[9:13], "little")
    old_stem = rb[13:13 + rl].decode("latin1").rsplit(".", 1)[0]  # drop the ".L"
    if old_filename is None:
        raise ValueError("clip_data has no audio file entry to re-point")

    allb = flat_blocks(parsed)
    edits, size_delta = [], {}

    def _replace(old: str, new: str) -> None:
        ob, nb, i = old.encode(), new.encode(), 0
        while True:
            j = clip_data.find(ob, i)
            if j < 0:
                break
            if j >= 4 and int.from_bytes(clip_data[j - 4:j], "little") == len(ob):
                edits.append((j - 4, 4 + len(ob), len(nb).to_bytes(4, "little") + nb))
                for b in allb:
                    if b.offset - 7 <= j < b.offset + b.block_size:
                        size_delta[b.offset - 7] = size_delta.get(b.offset - 7, 0) + (len(nb) - len(ob))
            i = j + 1

    _replace(old_filename, filename)
    _replace(old_stem + ".L", stem + ".L")
    _replace(old_stem + ".R", stem + ".R")
    for z, d in size_delta.items():
        edits.append((z + 3, 4, (int.from_bytes(clip_data[z + 3:z + 7], "little") + d).to_bytes(4, "little")))
    for b in allb:
        if b.content_type == 0x1001:
            edits.append((b.offset - 7 + 15, 4, int(sample_count).to_bytes(4, "little")))
        if b.content_type == 0x2628:
            s = b.offset - 7; nl = int.from_bytes(clip_data[s + 9:s + 13], "little")
            edits.append((s + 13 + nl + 5, 2, int(sample_count).to_bytes(2, "little")))
    out = bytearray(clip_data)
    for pos, oldlen, nb in sorted(edits, key=lambda e: e[0], reverse=True):
        out[pos:pos + oldlen] = nb
    out = bytearray(_FI.reindex_after_resize(clip_data, bytes(out)))
    # umid identity (fixed-size): 0x1001 copy keeps 0x2a marker @+44, 0x2106 copy is
    # 00-prefixed @+292, 2-byte id @+301
    for b in [x for x in flat_blocks(parse(bytes(out))) if x.content_type == 0x1003]:
        z = b.offset - 7
        out[z + 44:z + 52] = umid_material
        out[z + 292:z + 300] = b"\x00" + umid_material[1:]
        out[z + 301:z + 303] = id2
    return bytes(out)


def _audio_filenames(data: bytes) -> "list[str]":
    """The session's audio filenames in 0x103a-list order (== 0x1003 wav order). Parses
    the length-prefixed entries (`02 00 00 00 00 <len:4> <name> EVAW`) so any stem
    length works (a regex with a min length would miss e.g. `01.wav`)."""
    import struct
    out = []
    for b in flat_blocks(parse(data)):
        if b.content_type != 0x103A or b.block_size <= 10:
            continue
        blk = data[b.offset:b.offset + b.block_size]
        # The list opens with the MEDIA-FOLDER name, length-prefixed at block offset +18. It is
        # "Audio Files" by default but is whatever the folder is actually called -- a relinked
        # session can carry e.g. "8192 wavs". Searching for the literal made every such session
        # parse as having ZERO audio files (3 in the corpus). Read the length prefix instead:
        # verified on 52/52 sessions that hold this list, and it agrees with the old literal
        # search on all 49 where the folder really is named "Audio Files".
        # NOTE `b.offset` is zmark+7 in this parser, so the zmark-relative +18/+22 become +11/+15.
        nl = struct.unpack_from("<I", blk, 11)[0] if len(blk) >= 15 else 0
        if not (0 < nl <= 64 and 15 + nl <= len(blk)):
            continue
        i = 15 + nl + 4                       # skip the folder name + the trailing u32
        while i + 9 <= len(blk):
            if blk[i:i + 4] != b"\x02\x00\x00\x00":
                break
            nlen = struct.unpack_from("<I", blk, i + 5)[0]
            name = blk[i + 9:i + 9 + nlen]
            out.append(name.decode("latin1", "replace"))
            i += 9 + nlen + 4  # name + the trailing "EVAW" marker (next entry's 02 follows)
    return out


def _repoint_clip(data: bytes, old_stem: str, wav_path, filename=None) -> bytes:
    """Re-point ONE clip (identified by its current region stem) to `wav_path`, leaving
    the session's other clips untouched. Mirrors set_clip_wav but targets only this
    clip's region names, the matching filename, that clip's two 0x2628 region lengths,
    and the single 0x1003 wav descriptor at its 0x103a-list position. Reindexes once."""
    import os, struct
    sample_count, umid_material, id2 = wav_clip_identity(wav_path)
    if filename is None:
        filename = os.path.basename(str(wav_path))
    stem = filename.rsplit(".wav", 1)[0]
    old_filename = old_stem + ".wav"
    wav_idx = _audio_filenames(data).index(old_filename)  # which 0x1003 (0x103a order)
    allb = flat_blocks(parse(data))
    edits, size_delta = [], {}

    def _replace(old: str, new: str) -> None:
        ob, nb, i = old.encode(), new.encode(), 0
        while True:
            j = data.find(ob, i)
            if j < 0:
                break
            if j >= 4 and int.from_bytes(data[j - 4:j], "little") == len(ob):
                edits.append((j - 4, 4 + len(ob), len(nb).to_bytes(4, "little") + nb))
                for b in allb:
                    if b.offset - 7 <= j < b.offset + b.block_size:
                        size_delta[b.offset - 7] = size_delta.get(b.offset - 7, 0) + (len(nb) - len(ob))
            i = j + 1

    _replace(old_filename, filename)
    _replace(old_stem + ".L", stem + ".L")
    _replace(old_stem + ".R", stem + ".R")
    for z, d in size_delta.items():
        edits.append((z + 3, 4, (int.from_bytes(data[z + 3:z + 7], "little") + d).to_bytes(4, "little")))
    for b in allb:  # this clip's two regions (by current name) get the new frame count
        if b.content_type == 0x2628:
            s = b.offset - 7
            nl = int.from_bytes(data[s + 9:s + 13], "little")
            if data[s + 13:s + 13 + nl].decode("latin1", "replace") in (old_stem + ".L", old_stem + ".R"):
                edits.append((s + 13 + nl + 5, 2, int(sample_count).to_bytes(2, "little")))
    target = [b for b in allb if b.content_type == 0x1003][wav_idx]
    for g in target.child:
        if g.content_type == 0x1001:
            edits.append((g.offset - 7 + 15, 4, int(sample_count).to_bytes(4, "little")))
    out = bytearray(data)
    for pos, oldlen, nb in sorted(edits, key=lambda e: e[0], reverse=True):
        out[pos:pos + oldlen] = nb
    out = bytearray(_FI.reindex_after_resize(data, bytes(out)))
    t1003 = [b for b in flat_blocks(parse(bytes(out))) if b.content_type == 0x1003][wav_idx]
    z = t1003.offset - 7
    out[z + 44:z + 52] = umid_material
    out[z + 292:z + 300] = b"\x00" + umid_material[1:]
    out[z + 301:z + 303] = id2
    return bytes(out)


def set_clip_wavs(data: bytes, wav_paths) -> bytes:
    """Re-point EACH clip of a multi-clip session to its own WAV, in region/track order
    (clip i, on regions 2i/2i+1, gets `wav_paths[i]`). Generalizes `set_clip_wav` to N
    clips: re-points one clip at a time (each a single-clip rename + reindex on the
    updated bytes), so the clips' file table / regions / placements all stay consistent.
    Each WAV must be a staged PT/BWF WAV. Returns full unxored bytes."""
    out = data
    for i, wav in enumerate(wav_paths):
        if wav is None:
            continue
        # the current stem of clip i = region 2i's name minus the ".L"
        regs = [b for b in flat_blocks(parse(out)) if b.content_type == 0x2628]
        import struct
        rb = out[regs[2 * i].offset - 7:regs[2 * i].offset + regs[2 * i].block_size]
        nl = struct.unpack_from("<I", rb, 9)[0]
        old_stem = rb[13:13 + nl].decode("latin1", "replace").rsplit(".", 1)[0]
        out = _repoint_clip(out, old_stem, wav)
    return out


def _region_template_name(region_2629: bytes) -> str:
    import struct
    # the 0x2628 name child sits at +9; name = <namelen:4><name> at +9+9
    nl = struct.unpack_from("<I", region_2629, 9 + 9)[0]
    return region_2629[9 + 13:9 + 13 + nl].decode("latin1", "replace")


def _build_region(template_2629: bytes, full_name: str, channel: int,
                  sample_count: int, guid16: bytes, findex: int) -> bytes:
    """Build one 0x2629 region from a `.L` template. Sets, then renames to `full_name`:
      * findex - the region->wav file index (0-based into the 0x103a file list), stored
        TWICE: once as a 4-byte value at the end of the 0x2628 name sub-block (offset
        16 + the 0x2628 size; the region-list/display copy the parser reads) and once in
        the region's fixed trailer (at len-8; the copy PT resolves for PLAYBACK). Setting
        only the first gives correct region names but plays the template's wav on every
        track (the 'all 8 tracks play bass' bug).
      * length - written as a VARIABLE-WIDTH three-point value (Ardour-style): the
        `lengthbytes` high-nibble at region offset (24 + namelen) selects a 2..4-byte
        little-endian sample count placed at (27 + namelen). A fixed 2-byte width
        truncates any clip over 65535 samples (~1.5 s) to its low 16 bits.
      * channel (@+78 in the 6-char template layout) and the region GUID (@+97).
    Only name/channel/length/GUID/findex gate linking; the rest is kept from the template.
    The fixed-offset fields are written on the template layout first, then carried through
    the length-widen and rename edits (each shifts only trailing bytes). Sizes recomputed."""
    import struct
    r = bytearray(template_2629)
    old = _region_template_name(template_2629).encode()
    L0 = len(old)
    # fixed-offset fields on the template (6-char-name) layout
    r[78] = channel
    r[97:113] = guid16
    struct.pack_into("<I", r, 16 + struct.unpack_from("<I", r, 12)[0], int(findex) & 0xFFFFFFFF)
    # widen the three-point length field to hold the full sample count
    nibble_pos, val_pos = 24 + L0, 27 + L0
    sc = max(0, int(sample_count)) & 0xFFFFFFFF
    w = max(2, (sc.bit_length() + 7) // 8) if sc else 2
    r[nibble_pos] = (w << 4) | (r[nibble_pos] & 0x0F)
    r = r[:val_pos] + bytearray(sc.to_bytes(w, "little")) + r[val_pos + 2:]
    struct.pack_into("<I", r, 12, struct.unpack_from("<I", r, 12)[0] + (w - 2))  # 0x2628 size
    # rename (shifts the three-point + trailing fields; their values are already written)
    new = full_name.encode()
    j = r.find(old)
    while j >= 0 and not (j >= 4 and int.from_bytes(r[j - 4:j], "little") == len(old)):
        j = r.find(old, j + 1)
    r = bytearray(r[:j - 4] + len(new).to_bytes(4, "little") + new + r[j + len(old):])
    struct.pack_into("<I", r, 12, struct.unpack_from("<I", r, 12)[0] + (len(new) - len(old)))  # 0x2628
    struct.pack_into("<I", r, 3, len(r) - 7)                                                    # 0x2629
    struct.pack_into("<I", r, len(r) - 8, int(findex) & 0xFFFFFFFF)  # playback findex (trailer copy)
    return bytes(r)


def _file_filetime(path) -> int:
    """A file's mtime as a Windows FILETIME (100-ns ticks since 1601-01-01 UTC) — the value
    Pro Tools stamps into the 0x1003 descriptor as the source-file timestamp."""
    import os
    return round((os.path.getmtime(path) + 11644473600) * 10_000_000)


def _build_wav_descriptor(template_1003: bytes, index: int, sample_count: int,
                          umid: bytes, id2: bytes, mtime_ft: "int | None" = None) -> bytes:
    """Build one 0x1003 wav descriptor: set the wav ordinal (@+9), the sample count (in
    the 0x1001 child @+15), and the umid identity (0x1001 copy @+44 with 2a, 0x2106 copy
    @+292 00-prefixed, 2-byte id @+301) — the same fields set_clip_wav patches.

    When `mtime_ft` (the source WAV's mtime as a Windows FILETIME) is given, also stamp the
    file-timestamp fields the way a fresh Pro Tools IMPORT does — @+100 and @+172 = the file
    mtime, @+259 = 0, and the @+182 flag = 1 (a clean clip template carries stale 2017-era
    timestamps + flag 0). PT keys waveform-overview-cache staleness on these; matching a real
    import is what lets PT build the overview on first open instead of needing a manual
    'Recalculate Waveform Overviews' (the cache itself lives in the session's WaveCache.wfm,
    keyed by UMID)."""
    import struct
    u = bytearray(template_1003)
    u[9] = index
    i = 9
    while i + 9 <= len(u):
        if u[i] == 0x5A and struct.unpack_from("<H", u, i + 7)[0] == 0x1001:
            struct.pack_into("<I", u, i + 15, sample_count)
            break
        i += 1
    u[44:52] = umid
    u[292:300] = b"\x00" + umid[1:]
    u[301:303] = id2
    if mtime_ft is not None:
        struct.pack_into("<Q", u, 100, int(mtime_ft))   # source-WAV mtime (PT overview-staleness key)
        struct.pack_into("<Q", u, 172, int(mtime_ft))   # second mtime copy
        struct.pack_into("<Q", u, 259, 0)               # PT zeros this on a fresh import
        u[182] = 1                                      # flag PT sets on import (template = 0)
    return bytes(u)


def _filename_list_trailer(clip_ref: bytes) -> bytes:
    """The bytes AFTER the filename entries in `clip_ref`'s 0x103a list: a
    `00 ffffffff` marker + the session folder's path components (Dropbox SSD / … /
    lots of stereo tracks). Folder-specific but filename/count-INDEPENDENT, so it
    transplants verbatim. Omitting it is the 'end of stream' bug."""
    import struct
    for b in flat_blocks(parse(clip_ref)):
        if b.content_type == 0x103A and b.block_size > 10:
            blk = block_bytes(clip_ref, b)
            anchor = blk.find(b"Audio Files")
            if anchor < 0:
                continue
            i = anchor + len(b"Audio Files") + 4
            while i + 9 <= len(blk) and blk[i:i + 4] == b"\x02\x00\x00\x00":
                nlen = struct.unpack_from("<I", blk, i + 5)[0]
                i += 9 + nlen + 4
            return blk[i:]
    return b""


def _renumber_path_trailer(trailer: bytes, d: int) -> bytes:
    """The folder-path trailer numbers its components in CONTINUATION of the D filename
    entries: component indices run D+1, D+2, … (the control's verbatim indices are only
    correct at its own D, else Pro Tools `out_of_range`s indexing them). Renumber for D.
    Trailer = `00 ffffffff <volname-len:4> <volname> <volID:4>` then components
    `01 <idx:4> <len:4> <name> 0000`."""
    import struct
    out = bytearray(trailer)
    if out[:5] != b"\x00\xff\xff\xff\xff":
        return trailer
    i = 5
    vnl = struct.unpack_from("<I", out, i)[0]
    i += 4 + vnl + 4  # volname-len + volname + volID
    idx = d + 1
    while i + 9 <= len(out) and out[i] == 0x01:
        struct.pack_into("<I", out, i + 1, idx)
        idx += 1
        clen = struct.unpack_from("<I", out, i + 5)[0]
        i += 1 + 4 + 4 + clen + 4
    return bytes(out)


def _build_filename_list(filenames: "list[str]", trailer: bytes) -> bytes:
    """Build the 0x103a 'Audio Files' list block for `filenames`. Header counts scale
    with D = len(filenames): `<8+D:u32> 01 <7+D:u32> <11> 'Audio Files' 0000` then each
    entry `02 00 00 00 00 <len:4> <name> EVAW`, then the folder-path `trailer` (its
    component indices renumbered to continue from D)."""
    import struct
    d = len(filenames)
    content = bytearray()
    content += (8 + d).to_bytes(4, "little") + b"\x01" + (7 + d).to_bytes(4, "little")
    content += (11).to_bytes(4, "little") + b"Audio Files" + b"\x00\x00\x00\x00"
    for fn in filenames:
        fb = fn.encode()
        content += b"\x02\x00\x00\x00\x00" + len(fb).to_bytes(4, "little") + fb + b"EVAW"
    content += _renumber_path_trailer(trailer, d)
    out = bytearray(b"\x5a\x01\x00") + (len(content) + 2).to_bytes(4, "little") + b"\x3a\x10" + content
    return bytes(out)


def _build_placement_lanes(b1054: bytes, track_clips: "dict", tail_template: bytes) -> bytes:
    """Rebuild a 0x1054 placing each track's clips. `track_clips[t]` = list of
    (region_index, position); each track lane gets that many 0x1050 placements (the lane
    count byte = K), each 0x104f's region/channel index (payload[2] = 2*region_index +
    channel) and position (payload[7:15]) patched. `tail_template` = one clip lane tail
    (`01 00 00 00` + a 0x1050{0x104f})."""
    import struct
    # tail_template = <count:4> <0x1050 block> <2-byte lane trailer>. Split the placement
    # block from the trailer: the trailer goes ONCE at the lane end, not per placement.
    plc_size = struct.unpack_from("<I", tail_template, 4 + 3)[0]
    plc_block = tail_template[4:4 + 7 + plc_size]
    lane_trailer = tail_template[4 + 7 + plc_size:]
    out = bytearray(b1054[:9])
    i, lane = 9, 0
    while i < len(b1054):
        if b1054[i] != 0x5A:
            out += b1054[i:i + 1]; i += 1; continue
        csz = struct.unpack_from("<I", b1054, i + 3)[0]
        cct = struct.unpack_from("<H", b1054, i + 7)[0]
        child = b1054[i:i + 7 + csz]
        if cct == 0x1052:
            track, chan = lane // 2, lane % 2
            if track in track_clips and track_clips[track]:
                nl = struct.unpack_from("<I", child, 9)[0]
                placements = bytearray()
                for region_index, pos in track_clips[track]:
                    plc = bytearray(plc_block)
                    fi = plc.find(b"\x4f\x10") + 2      # 0x104f payload start
                    plc[fi + 2] = 2 * region_index + chan
                    struct.pack_into("<Q", plc, fi + 7, int(pos))
                    placements += plc
                placements += lane_trailer
                newc = bytearray(child[:13 + nl]) + len(track_clips[track]).to_bytes(4, "little") + placements
                struct.pack_into("<I", newc, 3, len(newc) - 7)
                child = bytes(newc)
            lane += 1
        out += child
        i += 7 + csz
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


_CLIP_TEMPLATE_NAMES = ("CLIP_WAV_DESCRIPTOR", "CLIP_REGION", "CLIP_PATH", "CLIP_LANE_TAIL",
                        "CLIP_1004_HEADER", "CLIP_1004_TRAILER", "CLIP_262A_HEADER",
                        "CLIP_FILENAME_TRAILER")


def _extract_clip_templates(clip_ref: bytes) -> "tuple[bytes, ...]":
    """Extract the (<=512 B) clip byte templates from a clip control session, in the order
    (wav_descriptor 0x1003, region 0x2629, path 0x0f3d, lane_tail, 0x1004 header, 0x1004
    trailer, 0x262a header, 0x103a folder-path trailer). These are exactly what `_templates`
    inlines — pass a re-authored control here (or to `donorpack.write_inline_templates`) to
    regenerate the inlined copies."""
    import struct
    cp = parse(clip_ref)
    t1003 = block_bytes(clip_ref, [b for b in flat_blocks(cp) if b.content_type == 0x1003][0])
    treg = block_bytes(clip_ref, [b for b in flat_blocks(cp) if b.content_type == 0x2629][0])
    tpath = block_bytes(clip_ref, [b for b in cp.blocks if b.content_type == 0x0F3D][0])
    lane = [b for b in flat_blocks(cp)
            if b.content_type == 0x1052 and any(c.content_type == 0x1050 for c in b.child)][0]
    lb = block_bytes(clip_ref, lane)
    ttail = lb[13 + struct.unpack_from("<I", lb, 9)[0]:]
    h4, _pre4, _units, tr4 = _count_block_parts(clip_ref, 0x1004, 0x1003)
    h2a = block_bytes(clip_ref, [b for b in cp.blocks if b.content_type == 0x262A][0])[:9]
    trailer = _filename_list_trailer(clip_ref)
    return t1003, treg, tpath, ttail, h4, tr4, h2a, trailer


def build_audio_clips(data: bytes, tracks, clip_ref: "bytes | None" = None) -> bytes:
    """Build a complete multi-track audio session: place clips on a clean N-stereo
    session `data` from a HIERARCHICAL spec `tracks` — a list (one entry per track, in
    track order) of clip lists, each clip a `(wav_path, position_file_samples)` tuple — or
    `(wav_path, position, name)` to give the clip a display name different from the WAV's
    filename. Any number of clips per track, any number of tracks, each clip its own staged
    BWF WAV, position, and optional name. The byte templates (a 0x1003 wav descriptor, a
    `.L` 0x2629 region, the 0x0f3d path, a clip lane tail, the 0x1004 header/trailer) are
    INLINED in `_templates` (extracted from `3 stereo 3 different clips.ptx`), so `clip_ref`
    defaults to None and no donor file is needed; pass a `clip_ref` only to override them
    (e.g. to regenerate the inlined templates from a re-authored control).

    The session's audio content is assembled per DISTINCT wav (clips of the same wav
    share a region): D wav descriptors in 0x1004 (each built from the WAV's identity via
    `wav_clip_identity`), D region pairs in 0x262a (unique deterministic GUIDs), one
    shared 0x0f3d path; placements reference their wav's region by index (0x104f
    payload[2]); the index gains exactly one 0x0f3c marker (`_synth_clip_index`). `data`
    must be clip-FREE with at least as many tracks as `len(tracks)`. Returns unxored bytes."""
    import os, struct, hashlib
    if clip_ref is None:
        from . import _templates as _T
        t1003, treg, tpath, ttail = _T.CLIP_WAV_DESCRIPTOR, _T.CLIP_REGION, _T.CLIP_PATH, _T.CLIP_LANE_TAIL
        h4, tr4, h2a, trailer = (_T.CLIP_1004_HEADER, _T.CLIP_1004_TRAILER,
                                 _T.CLIP_262A_HEADER, _T.CLIP_FILENAME_TRAILER)
    else:
        t1003, treg, tpath, ttail, h4, tr4, h2a, trailer = _extract_clip_templates(clip_ref)

    clips = []  # (track, wav, position, name|None)
    for t, clist in enumerate(tracks):
        for spec in clist:
            clips.append((t, spec[0], spec[1], spec[2] if len(spec) > 2 else None))
    distinct = []
    for _t, w, _p, _n in clips:
        if w not in distinct:
            distinct.append(w)
    region_of = {w: j for j, w in enumerate(distinct)}
    # region (clip) display name per distinct wav: the first clip's name, else filename stem
    name_of = {}
    for _t, w, _p, n in clips:
        if n is not None and w not in name_of:
            name_of[w] = n

    descs, regions, filenames = [], [], []
    for j, w in enumerate(distinct):
        sc, umid, id2 = wav_clip_identity(w)
        fn = os.path.basename(str(w))
        filenames.append(fn)
        stem = name_of.get(w) or fn.rsplit(".wav", 1)[0]  # clip name, decoupled from filename
        descs.append(_build_wav_descriptor(t1003, j + 1, sc, umid, id2, _file_filetime(w)))

        def _guid(chan):
            return hashlib.sha256(f"ptformat-clip-{j}-{chan}".encode()).digest()[:16]
        regions.append(_build_region(treg, stem + ".L", 0, sc, _guid(0), j))
        regions.append(_build_region(treg, stem + ".R", 1, sc, _guid(1), j))

    blk1004 = _rebuild_count_block(h4, b"\x00\x00\x00\x00" + _build_filename_list(filenames, trailer),
                                   descs, tr4, len(distinct))
    blk262a = _rebuild_count_block(h2a, b"\x00\x00\x00\x00", regions, b"", 2 * len(distinct))

    track_clips: "dict" = {}
    for t, w, pos, _n in clips:
        track_clips.setdefault(t, []).append((region_of[w], pos))

    refs = W.top_level_refs(data)
    if not refs or refs[-1].block.content_type != 0x0002:
        raise ValueError("data has no trailing 0x0002 master index")
    body = bytearray(data[: refs[0].start])
    for r in refs[:-1]:
        ct = r.block.content_type
        if ct == 0x1004:
            body += blk1004
        elif ct == 0x262A:
            body += blk262a
        elif ct == 0x0F3D:
            body += tpath
        elif ct == 0x1054:
            body += _build_placement_lanes(r.data, track_clips, ttail)
        else:
            body += r.data
    body = bytes(body)
    out = body + _synth_clip_index(data, body)
    return _FI._set_first_block_index_pointer(out, len(body))


def clip_positions(data: bytes) -> "list[int]":
    """Return the timeline start (in FILE samples) of every 0x104f placement lane,
    in body order. A single stereo clip has two (lane .L, lane .R) with equal values."""
    import struct
    out = []
    for z, _e, c in _size_driven_blocks(data):
        if c == 0x104F and z >= 31:
            out.append(struct.unpack_from("<Q", data, z + 16)[0])
    return out


def set_clip_position(data: bytes, position_file_samples: int,
                      *, lanes: "tuple[int, ...] | None" = None) -> bytes:
    """Move audio clip placement(s) to an arbitrary timeline position. The clip's
    timeline start is governed SOLELY by the 0x104f placement position field (8-byte
    LE, in FILE samples = pos_seconds * file_rate, e.g. 44100), mirrored across the
    clip's lanes (one 0x104f per channel). PT-CONFIRMED by the bar1/bar2/bar3 controls:
    moving a clip changes ONLY this field — patching bar2's 0x104f to bar3's value
    reproduces real bar3 with zero unexpected diffs (the 0x2016/0x2056/0x2587/0x2624
    waveform-overview caches are recomputed by Pro Tools; the 0x262a region GUIDs are
    a save-nonce). The move is SIZE-NEUTRAL, so no reindex is needed.

    `position_file_samples` is in the WAV's own sample rate. A clip can only sit at a
    nonzero position if it was built from a pos>0 template (the 0->nonzero transition
    adds a 0x2038 sub-block to 0x2624 / +19 bytes); use a `one clip bar 2`-style ref.
    With `lanes` None, ALL placements move (correct for a single-clip session); pass a
    tuple of lane indices (body order) to move one clip's lane-pair. Returns unxored bytes."""
    import struct
    out = bytearray(data)
    # SIZE-DRIVEN, not `flat_blocks`. The scan parser UNDERCOUNTS on real sessions -- measured on
    # 402 corpus files, it misses one 0x104F in `Tree Lighting 2019_R5` and three in
    # `DWTS PT 2603 h.bak.006` -- and `lanes` indexes positionally into this very list. A placement
    # missing from the middle shifts every later index, so the caller silently moves THE WRONG
    # CLIP, with nothing in the result to show it. Same defect class as the 0x251A lane count.
    zs = [z for z, _e, c in _size_driven_blocks(data) if c == 0x104F and z >= 31]
    field = struct.pack("<Q", int(position_file_samples))
    sel = range(len(zs)) if lanes is None else lanes
    for i in sel:
        out[zs[i] + 16 : zs[i] + 24] = field
    return bytes(out)


def _find_bpm_double(block: bytes) -> "tuple[int, float] | None":
    """Locate the tempo's bpm float64 in a 0x2718 / 0x2028 block: the 8-byte window
    that decodes to a plausible musical tempo (20..999 bpm). Returns (offset, bpm)."""
    import struct
    for off in range(len(block) - 8):
        (v,) = struct.unpack_from("<d", block, off)
        if 20.0 <= v <= 999.0 and abs(v - round(v, 4)) < 1e-9:
            return off, v
    return None


def set_tempo(data: bytes, clean_ref: bytes, tempo_ref: bytes, bpm: "float | None" = None) -> bytes:
    """Set a single session tempo on `data` by transplanting the tempo change from a
    matched control PAIR: `clean_ref` (a default-tempo session, e.g. `Untitled.ptx`)
    and `tempo_ref` (the SAME session with an explicit tempo, e.g. `121bpm.ptx`).

    A tempo change touches the conductor scaffolding 0x1040 (value-independent — it
    just marks the conductor track "explicit") and stores the bpm as a float64 in BOTH
    0x2718 and 0x2028. Those blocks are transplanted from `tempo_ref`; if `bpm` is
    given it overwrites the ref's bpm double in the result (so any tempo can be set
    from one non-default ref). `data` keeps its own name/display. BYTE-EXACT content
    blocks when `data` IS `clean_ref`. Returns full unxored bytes."""
    import struct
    types = changed_top_level_types(clean_ref, tempo_ref)
    out = bytearray(_grow_blocks_from(data, tempo_ref, types))
    if bpm is None:
        return bytes(out)
    for ct in (0x2718, 0x2028):
        blocks = [b for b in parse(bytes(out)).blocks if b.content_type == ct]
        if not blocks:
            continue
        b = blocks[0]
        s, e = b.offset - 7, b.offset + b.block_size
        found = _find_bpm_double(bytes(out[s:e]))
        if found is None:
            raise ValueError(f"no bpm double found in 0x{ct:04x}")
        off, _old = found
        out[s + off : s + off + 8] = struct.pack("<d", float(bpm))
    return bytes(out)


def set_meter(data: bytes, clean_ref: bytes, meter_ref: bytes,
              numerator: "int | None" = None, denominator: "int | None" = None) -> bytes:
    """Set a single session meter (time signature) on `data` by transplanting the
    meter change from a matched control PAIR: `clean_ref` (a default-4/4 session, e.g.
    `Untitled.ptx`) and `meter_ref` (the SAME session with an explicit meter, e.g.
    `3-4 meter.ptx`).

    A meter change touches the conductor scaffolding 0x1040 plus the meter map blocks
    0x2719 and 0x2029, which carry the numerator/denominator as u32s after the "Meter"
    tag. Those blocks are transplanted from `meter_ref`; if `numerator`/`denominator`
    are given they overwrite the ref's values in the result. `data` keeps its own
    name/display. BYTE-EXACT content blocks when `data` IS `clean_ref`. Returns full
    unxored bytes."""
    types = changed_top_level_types(clean_ref, meter_ref)
    out = bytearray(_grow_blocks_from(data, meter_ref, types))
    if numerator is None and denominator is None:
        return bytes(out)
    for ct in (0x2719, 0x2029):
        blocks = [b for b in parse(bytes(out)).blocks if b.content_type == ct]
        if not blocks:
            continue
        b = blocks[0]
        s, e = b.offset - 7, b.offset + b.block_size
        body = bytes(out[s:e])
        m = body.find(b"Meter")
        if m < 0:
            continue
        # after "Meter"(5) + 0x0200 sep(2) + payload_len(4) + count(4) + pos(5) +
        # pad(3) + ordinal(4) => numerator u32, then denominator u32.
        num_off = m + 5 + 2 + 4 + 4 + 5 + 3 + 4
        den_off = num_off + 4
        if den_off + 4 > len(body):
            continue
        if numerator is not None:
            out[s + num_off : s + num_off + 4] = int(numerator).to_bytes(4, "little")
        if denominator is not None:
            out[s + den_off : s + den_off + 4] = int(denominator).to_bytes(4, "little")
    return bytes(out)


# Tempo/meter event positions are PT ticks (960000 per quarter); a record's stored
# position is ZERO_TICKS + tick. A tempo record is a 61-byte "Const..TMS" block (the
# bpm float64 at +40, position at +30); meter records sit after the ") Meter" tag,
# 52 bytes each (position at +0, ordinal +8, numerator +12, denominator +16). Both a
# map block (0x2028/0x2029) AND a ruler block (0x2718/0x2719) hold the same records.
_ZERO_TICKS = 0xE8D4A51000
_TEMPO_REC_SIG = bytes.fromhex("436f6e737401002e000000544d53")  # "Const..TMS"
_METER_TAG = b") Meter\x02"
TICKS_PER_QUARTER = 960000


def _block_spans(data: bytes, ct: int) -> "list[tuple[int, int]]":
    return [(b.offset - 7, b.offset + b.block_size)
            for b in parse(data).blocks if b.content_type == ct]


def _resize_tempo_block(block: bytes, n_records: int) -> bytes:
    """Grow/shrink a 0x2028 (map) or 0x2718 (ruler) block to exactly `n_records`
    61-byte "Const..TMS" records, replicating its first record as the template (the
    caller patches each record's pos/bpm/flags). The ") Tempo" header carries count
    (u32 @ rec0-8) and a length field (u32 @ rec0-12 = 4 + 61*N); block_size is @ +3.
    The fixed trailer after the records is preserved. rec0 is found via the record
    signature (NOT the 'Tempo' tag, which also appears in the 0x2718 0x2619 preamble)."""
    import struct
    rec0 = block.find(_TEMPO_REC_SIG)
    if rec0 < 0:
        return block
    # count contiguous existing records from rec0
    R, i = 0, rec0
    while block[i:i + len(_TEMPO_REC_SIG)] == _TEMPO_REC_SIG:
        R += 1
        i += 61
    template = block[rec0:rec0 + 61]
    trailer = block[rec0 + R * 61:]
    # Clone each SURVIVING record per index; use the template only for records beyond the ones
    # that already exist. Replicating record 0 for all N discards per-record bytes the caller
    # never patches, so an IDENTITY replace was not byte-exact: it rewrote 19 of 160 real
    # sessions (up to 820 bytes), with the dominant transitions all `value -> 0`. `0x2029` had
    # exactly this bug and was already fixed the same way (`_resize_meter_safe`); `0x2028` was
    # missed. Growing behaves as before -- new records still come from the template.
    kept = b"".join(block[rec0 + i * 61 : rec0 + (i + 1) * 61] if i < R else template
                    for i in range(n_records))
    new_block = bytearray(block[:rec0] + kept + trailer)
    struct.pack_into("<I", new_block, rec0 - 8, n_records)            # count
    struct.pack_into("<I", new_block, rec0 - 12, 4 + 61 * n_records)  # length
    struct.pack_into("<I", new_block, 3, len(new_block) - 7)          # block_size
    return bytes(new_block)


def _resize_meter_block(block: bytes, n_records: int) -> bytes:
    """Grow/shrink a meter block to `n_records` events. The meter map lives in a 0x2029
    block (top-level, OR nested inside the 0x2719 ruler) as: a ") Meter\\x02" header with
    length (u32 @tag+9 = 12 + 52*N) and count (u32 @tag+13), then N 36-byte records
    (pos@0, ordinal@8, num@12, den@16), then a trailing list of N 16-byte entries
    (ordinal:u32 + 0x01000000 + 8 zero) and a fixed 8-byte tail. Each event contributes
    36 + 16 = 52 bytes. Both the 0x2029 sub-block size (@tag-4) and, when nested in a
    0x2719 wrapper, the outer block_size (@3) are bumped. Caller patches per-event fields."""
    import struct
    tag = block.find(b") Meter\x02")
    if tag < 0:
        return block
    count_off = tag + 13
    rec0 = tag + 17
    count = struct.unpack_from("<I", block, count_off)[0]
    rec_template = block[rec0:rec0 + 36]
    trailing = block[rec0 + count * 36:]
    entry_template = trailing[:16] if count else b"\x01\x00\x00\x00\x01\x00\x00\x00" + b"\x00" * 8
    fixed_tail = trailing[count * 16:]
    new_block = bytearray(block[:rec0] + rec_template * n_records
                          + entry_template * n_records + fixed_tail)
    delta = len(new_block) - len(block)
    struct.pack_into("<I", new_block, count_off, n_records)
    struct.pack_into("<I", new_block, tag + 9, 12 + 52 * n_records)        # length
    struct.pack_into("<I", new_block, tag - 4,                              # 0x2029 size
                     struct.unpack_from("<I", new_block, tag - 4)[0] + delta)
    if tag != 7:                                                           # outer 0x2719
        struct.pack_into("<I", new_block, 3,
                         struct.unpack_from("<I", new_block, 3)[0] + delta)
    return bytes(new_block)


def _resize_record_blocks(data: bytes, resize) -> bytes:
    """Rebuild `data` replacing each top-level block via `resize(content_type, bytes)`
    (returns the new block bytes, or the original to leave unchanged). Body order is
    preserved; the trailing 0x0002 index is left untouched (caller reindexes)."""
    refs = W.top_level_refs(data)
    out = bytearray(data[: refs[0].start])
    for r in refs:
        out += resize(r.block.content_type, r.data)
    return bytes(out)


def set_tempo_map(data: bytes, events: "list[tuple[float, int]]",
                  tempo_ref: "bytes | None" = None, clean_ref: "bytes | None" = None) -> bytes:
    """Write a MID-SESSION tempo map (one or more tempo changes) onto `data`.

    `events` is a list of (bpm, position_in_ticks) in time order — e.g.
    `[(120, 0), (140, 4*TICKS_PER_QUARTER)]` is 120 bpm from the start then 140 bpm at bar 2.
    ANY number of events is supported (the record slots are BUILT). The first event is the
    session-start tempo (its position is normally 0).

    The tempo map lives, identically, in BOTH the map block 0x2028 and the ruler block 0x2718
    (each: a ") Tempo" header with count/length = 4+61*N, then N 61-byte "Const..TMS" records,
    then a fixed trailer). Each record carries the first-event flags (@21 & @39 = 1 for the
    initial tempo else 0), position (ZERO_TICKS + ticks @ +30), and bpm (float64 @ +40).

    The 0x2028/0x2718 templates are INLINED (`_templates.TEMPO_*`), so no donor is needed —
    only those two blocks are grown (DATA keeps its own view-state/I/O, exactly like
    `set_markers`); the tempo map is byte-identical to the donor path either way. Pass a
    `tempo_ref` (+ matching `clean_ref`) to override — that grows the full clean/ref delta
    (view-state included), which reproduces a control session byte-for-byte. Returns unxored."""
    import struct
    n = len(events)
    if n < 1:
        raise ValueError("events must be non-empty (the first event is the start tempo)")
    if tempo_ref is None:
        from . import _templates as _T
        out = bytearray(_grow_blocks_into(data, {
            0x2028: _resize_tempo_block(_T.TEMPO_MAP_TEMPLATE, n),
            0x2718: _resize_tempo_block(_T.TEMPO_RULER_TEMPLATE, n)}))
    else:
        if clean_ref is None:
            raise ValueError("override path needs clean_ref (the no-tempo pair of tempo_ref)")
        types = changed_top_level_types(clean_ref, tempo_ref)
        grown = _resize_record_blocks(
            tempo_ref,
            lambda ct, b: _resize_tempo_block(b, n) if ct in (0x2028, 0x2718) else b)
        gref = _FI.reindex_after_resize(tempo_ref, grown)
        out = bytearray(_grow_blocks_from(data, gref, types))
    for ct in (0x2028, 0x2718):
        for s, e in _block_spans(bytes(out), ct):
            recs, i = [], s
            while True:
                j = bytes(out).find(_TEMPO_REC_SIG, i, e)
                if j < 0:
                    break
                recs.append(j)
                i = j + 61
            if len(recs) != n:
                raise ValueError(f"0x{ct:04x} built {len(recs)} records, expected {n}")
            for idx, ((bpm, pos), r) in enumerate(zip(events, recs)):
                flag = 1 if idx == 0 else 0
                out[r + 21] = flag
                out[r + 39] = flag
                out[r + 30 : r + 35] = (_ZERO_TICKS + int(pos)).to_bytes(5, "little")
                out[r + 40 : r + 48] = struct.pack("<d", float(bpm))
    return bytes(out)


def set_meter_map(data: bytes, events: "list[tuple[int, int, int]]",
                  meter_ref: "bytes | None" = None, clean_ref: "bytes | None" = None) -> bytes:
    """Write a MID-SESSION meter map (one or more time-signature changes) onto `data`.

    `events` is a list of (numerator, denominator, position_in_ticks) in time order — e.g.
    `[(4, 4, 0), (3, 4, 4*TICKS_PER_QUARTER)]` is 4/4 from the start then 3/4 at bar 2. ANY
    number of events is supported. The first event is the session-start meter (position 0).

    The meter map lives in BOTH 0x2029 and the ruler 0x2719 (each: a 36-byte record + a
    16-byte trailing-list entry per event). Each record carries position (ZERO_TICKS + ticks
    @ +0), ordinal (1-based @ +8), numerator (@ +12), denominator (@ +16); each trailing-list
    entry carries its ordinal (@ +0).

    The 0x2029/0x2719 templates are INLINED (`_templates.METER_*`), so no donor is needed —
    only those two blocks are grown (DATA keeps its own view-state/I/O, like `set_markers`).
    Pass a `meter_ref` (+ matching `clean_ref`) to override and reproduce a control byte-for-
    byte (grows the full clean/ref delta). Returns full unxored bytes."""
    n = len(events)
    if n < 1:
        raise ValueError("events must be non-empty (the first event is the start meter)")
    if meter_ref is None:
        from . import _templates as _T
        out = bytearray(_grow_blocks_into(data, {
            0x2029: _resize_meter_block(_T.METER_MAP_TEMPLATE, n),
            0x2719: _resize_meter_block(_T.METER_RULER_TEMPLATE, n)}))
    else:
        if clean_ref is None:
            raise ValueError("override path needs clean_ref (the no-meter pair of meter_ref)")
        types = changed_top_level_types(clean_ref, meter_ref)
        grown = _resize_record_blocks(
            meter_ref,
            lambda ct, b: _resize_meter_block(b, n) if ct in (0x2029, 0x2719) else b)
        gref = _FI.reindex_after_resize(meter_ref, grown)
        out = bytearray(_grow_blocks_from(data, gref, types))
    for ct in (0x2029, 0x2719):
        for s, e in _block_spans(bytes(out), ct):
            m = bytes(out).find(_METER_TAG, s, e)
            if m < 0:
                continue
            # after ") Meter\x02" (tag) comes \x00 (sep) + length(4) + count(4), then
            # record 0; the trailing list (N 16-byte ordinal entries) follows the records.
            rec0 = m + len(_METER_TAG) + 1 + 4 + 4
            for i, (num, den, pos) in enumerate(events):
                r = rec0 + i * 36
                out[r : r + 5] = (_ZERO_TICKS + int(pos)).to_bytes(5, "little")
                out[r + 8 : r + 12] = int(i + 1).to_bytes(4, "little")     # ordinal
                out[r + 12 : r + 16] = int(num).to_bytes(4, "little")
                out[r + 16 : r + 20] = int(den).to_bytes(4, "little")
            trail0 = rec0 + n * 36
            for i in range(n):
                out[trail0 + i * 16 : trail0 + i * 16 + 4] = int(i + 1).to_bytes(4, "little")
    return bytes(out)


# --- replace tempo/meter maps on an ARBITRARY existing session ---------------
#
# set_tempo_map/set_meter_map are the SYNTHESIS-path writers: they find the
# conductor blocks via the scan parser (unreliable on real sessions) AND require
# the optional 0x2718/0x2719 lanes. The functions below are the size-driven
# counterparts: they resize the session's OWN top-level 0x2028/0x2029 (+ the lane
# when present) in place and repair the master index with a pure offset-shift
# (the block COUNT is unchanged), exactly as the clip edits do.

# Canonical (session-invariant) meter RECORD (36 B) + trailing-list ENTRY (16 B),
# used only to seed a grow of an EMPTY (count 0) meter block, which carries no
# record of its own to clone. Lifted from the PT-confirmed METER_MAP_TEMPLATE; a
# resize of that template to 1 record is byte-identical to a real count-1 block.
_METER_REC_SEED = bytes.fromhex(
    "0010a5d4e800000001000000040000000400000001000300000000000200000003000000")
_METER_ENTRY_SEED = bytes.fromhex("01000000010000000000000000000000")


def _resize_tempo_lane(block: bytes, n_records: int) -> bytes:
    """Resize a tempo block to `n_records` 61-byte records, correct whether `block` is the
    top-level 0x2028 map (records directly in it) OR the 0x2718 ruler lane (records inside a
    nested 0x2028 sub-block). Reuses `_resize_tempo_block` for the record splice + count/
    length + OUTER block_size, then additionally repairs the NESTED 0x2028 sub-block size
    when the ruler wraps one (its own 5A..2028 header sits before the first record)."""
    import struct
    new = bytearray(_resize_tempo_block(block, n_records))
    delta = len(new) - len(block)
    if delta == 0:
        return bytes(new)
    rec0 = new.find(_TEMPO_REC_SIG)
    nested, p = -1, 1
    while True:
        j = new.find(b"\x5a", p, rec0)
        if j < 0:
            break
        ct = int.from_bytes(new[j + 7 : j + 9], "little")
        bt = int.from_bytes(new[j + 1 : j + 3], "little")
        if ct == 0x2028 and not (bt & 0xFF00) and j + 9 <= rec0:
            nested = j
        p = j + 1
    if nested > 0:
        struct.pack_into("<I", new, nested + 3,
                         struct.unpack_from("<I", new, nested + 3)[0] + delta)
    return bytes(new)


def _resize_meter_safe(block: bytes, n_records: int) -> bytes:
    """Resize a meter block to exactly `n_records` 36-byte records + `n_records` 16-byte
    trailing entries, PRESERVING each surviving record's/entry's opaque bytes. Works on the
    top-level 0x2029 map, the 0x2719 ruler (nested 0x2029), and an EMPTY (count 0) block.

    A meter record carries an OPAQUE per-record field (byte +22 is 0x03/0x04 in the corpus,
    not derivable from num/den/tick), so unlike `_resize_meter_block` (which clones record 0
    N times and loses those bytes) we KEEP existing records/entries for the first
    min(old_count, N) slots and seed extras from the canonical template -- making an identity
    resize byte-exact. Updates count (@tag+13), length (@tag+9 = 12+52*N), the enclosing
    0x2029 size (@tag-4), and the outer ruler size (@+3) when nested (tag != 7)."""
    import struct
    m = block.find(b") Meter\x02")
    if m < 0:
        return block
    count = struct.unpack_from("<I", block, m + 13)[0]
    rec0 = m + len(b") Meter\x02") + 1 + 4 + 4
    old_recs = block[rec0 : rec0 + count * 36]
    old_entries = block[rec0 + count * 36 : rec0 + count * 52]
    tail = block[rec0 + count * 52 :]
    rec_tpl = old_recs[:36] if count else _METER_REC_SEED
    ent_tpl = old_entries[:16] if count else _METER_ENTRY_SEED
    recs, ents = bytearray(), bytearray()
    for i in range(n_records):
        recs += old_recs[i * 36 : i * 36 + 36] if i < count else rec_tpl
        ents += old_entries[i * 16 : i * 16 + 16] if i < count else ent_tpl
    new = bytearray(block[:rec0] + bytes(recs) + bytes(ents) + tail)
    delta = len(new) - len(block)
    struct.pack_into("<I", new, m + 13, n_records)
    struct.pack_into("<I", new, m + 9, 12 + 52 * n_records)
    struct.pack_into("<I", new, m - 4, struct.unpack_from("<I", new, m - 4)[0] + delta)
    if m != 7:
        struct.pack_into("<I", new, 3, struct.unpack_from("<I", new, 3)[0] + delta)
    return bytes(new)


def _rewrite_tempo_records(block: bytes, events) -> bytes:
    """Overwrite the per-event fields of a tempo block (0x2028 map OR 0x2718 ruler) after it
    was resized to len(events) records: flag @+21 & @+39 (=1 for event0 else 0), tick 5-byte
    @+30 (ZERO_TICKS+tick), bpm f64 @+40 -- mirrors set_tempo_map's loop."""
    import struct
    out = bytearray(block)
    recs, i = [], out.find(_TEMPO_REC_SIG)
    while i >= 0 and out[i : i + len(_TEMPO_REC_SIG)] == _TEMPO_REC_SIG:
        recs.append(i)
        i += 61
    if len(recs) != len(events):
        raise ValueError(f"tempo block has {len(recs)} records, expected {len(events)}")
    for idx, ((bpm, tick), r) in enumerate(zip(events, recs)):
        # +39 IS the first-record flag: measured over 3,247 real tempo records it is 1 in 309 of
        # them and essentially all are index 0. +21 is NOT -- it is an independent per-record
        # boolean, set in 1,419 of 2,938 NON-first records (~half), and the two disagree in 1,417
        # records. Deriving +21 from the index therefore overwrote real data: an IDENTITY replace
        # rewrote it 1->0 in 1,247 records across 15 sessions. Records are cloned per index by
        # `_resize_tempo_block`, so leaving +21 alone preserves each record's own value, and a
        # newly grown record inherits the template's. Its MEANING is unknown (a linear-ramp flag
        # is the obvious guess) and needs Pro Tools; preserving it does not require knowing it.
        # Leave a record alone entirely when it ALREADY holds this event. +39 is the
        # first-record flag in 3,245 of 3,247 real records -- but two sessions of one project
        # carry it on record 2 instead, so even this one is not derivable, only overwhelmingly
        # likely. Touching only records that actually change keeps every such exception intact
        # and makes an identity replace byte-exact.
        want_tick = (_ZERO_TICKS + int(tick)).to_bytes(5, "little")
        want_bpm = struct.pack("<d", float(bpm))
        if out[r + 30 : r + 35] == want_tick and out[r + 40 : r + 48] == want_bpm:
            continue
        out[r + 39] = 1 if idx == 0 else 0
        out[r + 30 : r + 35] = want_tick
        out[r + 40 : r + 48] = want_bpm
    return bytes(out)


def _meter_bar_numbers(events, start_bar: int) -> "list[int]":
    """Absolute bar number of each meter event. Event 0 is `start_bar` (the session's
    displayed first bar; usually 1, but PT can renumber, e.g. -2). Each later event's bar
    advances by the whole bars elapsed under the PREVIOUS meter across the tick span, rounded
    UP (a change landing mid-bar is placed at the following barline):
    `bars += ceil(tick_delta / ticks_per_bar)`, `ticks_per_bar = num * (960000*4 // den)`.
    CEIL (not floor) reproduces the stored +8/+0 bar fields exactly across the corpus."""
    bars = [int(start_bar)]
    for k in range(1, len(events)):
        pnum, pden, ptick = events[k - 1]
        tpb = pnum * (TICKS_PER_QUARTER * 4 // pden)
        span = events[k][2] - ptick
        bars.append(bars[-1] + (-(-span // tpb) if tpb else 0))   # ceil division
    return bars


def _rewrite_meter_records(block: bytes, events, start_bar: int) -> bytes:
    """Overwrite the per-event fields of a meter block (0x2029 map OR 0x2719 ruler) after a
    resize to len(events) records: per record tick 5-byte @+0 (ZERO_TICKS+tick), ABSOLUTE bar
    number @+8 (i32, see `_meter_bar_numbers` -- PT can renumber the first bar), num @+12,
    den @+16. Each 16-byte trailing entry @+0 carries the bar RELATIVE to the session start
    (`bar[i] - start_bar + 1`, a 1-based index; == the absolute bar only when start_bar==1)."""
    out = bytearray(block)
    m = out.find(b") Meter\x02")
    if m < 0:
        return bytes(out)
    rec0 = m + len(b") Meter\x02") + 1 + 4 + 4
    n = len(events)
    bars = _meter_bar_numbers(events, start_bar)
    # Capture each record's CURRENT bar before overwriting it, so the trailing entry below can
    # tell "this event did not move" from "this event changed".
    prev_bars = [int.from_bytes(out[rec0 + i * 36 + 8 : rec0 + i * 36 + 12], "little", signed=True)
                 for i in range(n)]
    for i, (num, den, tick) in enumerate(events):
        r = rec0 + i * 36
        out[r : r + 5] = (_ZERO_TICKS + int(tick)).to_bytes(5, "little")
        out[r + 8 : r + 12] = int(bars[i]).to_bytes(4, "little", signed=True)
        out[r + 12 : r + 16] = int(num).to_bytes(4, "little")
        out[r + 16 : r + 20] = int(den).to_bytes(4, "little")
    # The 16-byte trailing entry's leading i32 is NOT reliably `bar - start_bar + 1`. Measured
    # per event over the corpus: that relative form fits 467 of 509, the ABSOLUTE bar fits 441,
    # and 20 events match NEITHER -- so no formula can be imposed without rewriting real data.
    # (Both forms coincide whenever start_bar == 1, which is why one looked universal.) Writing
    # the relative form unconditionally rewrote a renumbered session's entry from -2 to 1 on 10
    # corpus sessions, and `meter_map` cannot see it, so the map round-tripped while the bytes
    # did not. Only rewrite an entry whose event actually MOVED; leave a still-correct one alone.
    trail0 = rec0 + n * 36
    for i in range(n):
        if i < len(prev_bars) and prev_bars[i] == bars[i]:
            continue
        out[trail0 + i * 16 : trail0 + i * 16 + 4] = (
            bars[i] - int(start_bar) + 1).to_bytes(4, "little", signed=True)
    return bytes(out)


def _splice_and_reindex(data: bytes, replacements) -> bytes:
    """Splice one or more resized top-level blocks into `data` and repair the master index
    for the net size delta (a pure offset-shift -- the block COUNT is unchanged). Each
    `replacements` item is `(zmark, old_end, new_block_bytes)` for a block whose OWN zmark is
    unchanged (only its trailing records grew/shrank). Mirrors remove_clip/add_clip's reindex:
    splice the bytes, slide every master-index offset that points PAST a splice by the
    cumulative delta, and repoint the first block at the moved index."""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    recs = _FI.parse_records(ref.data, {c for _z, _e, c in bl}, {z for z, _e, _c in bl})
    edits = sorted((z, old_end - z, nb) for z, old_end, nb in replacements)

    out = bytearray()
    prev = 0
    for z, cut, nb in edits:
        out += data[prev:z]
        out += nb
        prev = z + cut
    out += data[prev:]

    def delta_before(pos: int) -> int:
        return sum(len(nb) - cut for z, cut, nb in edits if z < pos)

    def shift(abs_pos: int, val: int) -> None:
        d = delta_before(val)
        if d == 0:
            return
        na = abs_pos + delta_before(abs_pos + 1)
        out[na : na + 4] = (val + d).to_bytes(4, "little")

    for r in recs:
        for c in r.child_refs:
            shift(ref.start + r.start + c.rel + 2, c.offset)
        for el in r.elements:
            for i, o in enumerate(el.offsets):
                shift(ref.start + r.start + el.rel + 5 + 4 * i, o)

    first_offset = min(z for z, _e, _c in bl) + 7
    total_delta = sum(len(nb) - cut for _z, cut, nb in edits)
    out[first_offset : first_offset + 4] = (ref.start + total_delta).to_bytes(4, "little")
    return bytes(out)


def replace_tempo_map(data: bytes, events: "list[tuple[float, int]]") -> bytes:
    """Replace the tempo (conductor) map of an ARBITRARY existing session with `events` =
    `[(bpm, tick), ...]` (tick in PT conductor ticks; the first event is the session start,
    tick 0), leaving everything else byte-intact. Rewrites the top-level 0x2028 "Tempo" block
    and, when present, the 0x2718 tempo ruler, then repairs the master index for the size
    delta (offset-shift). An identity replace (`replace_tempo_map(d, tempo_map(d))`) is
    byte-identical; a modify reads back exactly via `tempo_map`. Corpus-validated across all
    26 sessions (rc=0, readback exact, index resolves, other blocks byte-intact); PT display
    confirmation pending. Returns full unxored bytes."""
    n = len(events)
    if n < 1:
        raise ValueError("events must be non-empty (the first event is the start tempo)")
    ref = _FI.final_index_ref(data)
    if ref is None:
        raise ValueError("data has no trailing 0x0002 master index")
    bl = sorted(_raw_block_bounds(data, ref.start))
    lanes = [(z, e) for z, e, c in bl if c == 0x2718]
    top = None
    for z, e, c in bl:
        if c == 0x2028 and data[z + 9 : z + 14] == b"Tempo" and not any(
                lz < z and le >= e for lz, le in lanes):
            top = (z, e)
            break
    if top is None:
        raise ValueError("no top-level 0x2028 tempo block")
    tz, te = top
    replacements = [(tz, te, _rewrite_tempo_records(_resize_tempo_lane(data[tz:te], n), events))]
    if lanes:
        lz, le = lanes[0]
        replacements.append(
            (lz, le, _rewrite_tempo_records(_resize_tempo_lane(data[lz:le], n), events)))
    return _splice_and_reindex(data, replacements)


def replace_meter_map(data: bytes, events: "list[tuple[int, int, int]]") -> bytes:
    """Replace the meter (conductor) map of an ARBITRARY existing session with `events` =
    `[(numerator, denominator, tick), ...]` (tick in PT conductor ticks; the first event is
    the session start, tick 0), leaving everything else byte-intact. Rewrites the top-level
    0x2029 "Meter" block and, when present, the 0x2719 meter ruler, then repairs the master
    index. Preserves the session's displayed first bar (event 0's +8, which PT may renumber,
    e.g. -2; §5c). An empty (count 0) map is grown from the canonical template. Identity
    replace is byte-identical; a modify reads back exactly via `meter_map`. Corpus-validated
    across all 26 sessions; PT display confirmation pending. Returns full unxored bytes."""
    n = len(events)
    if n < 1:
        raise ValueError("events must be non-empty (the first event is the start meter)")
    ref = _FI.final_index_ref(data)
    if ref is None:
        raise ValueError("data has no trailing 0x0002 master index")
    bl = sorted(_raw_block_bounds(data, ref.start))
    lanes = [(z, e) for z, e, c in bl if c == 0x2719]
    top = None
    for z, e, c in bl:
        if c == 0x2029 and data[z + 9 : z + 14] == b"Meter" and not any(
                lz < z and le >= e for lz, le in lanes):
            top = (z, e)
            break
    if top is None:
        raise ValueError("no top-level 0x2029 meter block")
    tz, te = top
    old_count = int.from_bytes(data[tz + 20 : tz + 24], "little")
    start_bar = (int.from_bytes(data[tz + 32 : tz + 36], "little", signed=True)
                 if old_count >= 1 else 1)
    replacements = [
        (tz, te, _rewrite_meter_records(_resize_meter_safe(data[tz:te], n), events, start_bar))]
    if lanes:
        lz, le = lanes[0]
        replacements.append(
            (lz, le, _rewrite_meter_records(_resize_meter_safe(data[lz:le], n), events, start_bar)))
    return _splice_and_reindex(data, replacements)


_MARKER_CT = 0x2077  # a marker record block inside the 0x2030 marker list


def _build_marker_record(template: bytes, ordinal: int, name: str, position: int, guid16: bytes) -> bytes:
    """Build one 0x2077 marker record from a template: ordinal @+9, name (length-prefixed:
    len @+15, name @+19), `position` as a u64 at name_end AND name_end+8 (the encoded value --
    tick-locked `0x4000000000000000 + ZERO_TICKS + tick`, or a plain sample count), and a unique
    GUID (16B at name_end+166). Everything else is kept from the
    template; the record size = base + namelen."""
    import struct
    t = bytearray(template)
    tnl = struct.unpack_from("<I", t, 15)[0]
    tend = 19 + tnl
    t[9] = ordinal & 0xFF
    # The FULL u64, not the low 5 bytes. `markers()` decodes this field by its TOP byte -- 0x40
    # tick-locked, 0x00 sample-locked (§10) -- and writing only 5 bytes left that byte coming
    # from the template, so every marker written was forced to the template's timebase. A
    # sample-locked marker could not be expressed at all, which is why `set_markers` had to
    # refuse 25 of 88 corpus sessions. Callers pass the encoded position; `set_markers` builds it.
    pos = int(position).to_bytes(8, "little")
    t[tend:tend + 8] = pos
    t[tend + 8:tend + 16] = pos
    t[tend + 166:tend + 182] = guid16
    nb = name.encode()
    out = bytearray(t[:15]) + len(nb).to_bytes(4, "little") + nb + t[19 + tnl:]
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def _resize_marker_block(block: bytes, markers) -> bytes:
    """Rebuild a 0x2030 marker list: count (u32 @ content[0]) + one 0x2077 record per
    (name, ENCODED position) in `markers`, then any trailer (preserved). Records are built from the
    block's first 0x2077 template; each gets ordinal i+1 and a deterministic unique GUID."""
    import struct, hashlib
    i = 13  # 0x2030 header (9) + count (4)
    recs = []
    while i < len(block) and block[i] == 0x5A and struct.unpack_from("<H", block, i + 7)[0] == _MARKER_CT:
        sz = struct.unpack_from("<I", block, i + 3)[0]
        recs.append((i, 7 + sz))
        i += 7 + sz
    if not recs:
        return block
    template = block[recs[0][0]:recs[0][0] + recs[0][1]]
    trailer = block[i:]
    records = bytearray()
    for k, (name, position) in enumerate(markers):
        guid = hashlib.sha256(f"ptformat-marker-{k}".encode()).digest()[:16]
        records += _build_marker_record(template, k + 1, name, position, guid)
    content = bytearray(b"\x00\x00\x00\x00") + records + trailer
    struct.pack_into("<I", content, 0, len(markers))
    out = bytearray(block[:9]) + content
    struct.pack_into("<I", out, 3, len(out) - 7)
    return bytes(out)


def _marker_rows_per_record(blocks) -> int:
    """`0x2506` rows per `0x2077` marker record -- i.e. the session's playlist count, via markers.

    The settled relation is `n(0x2506) == n(0x2077) x n_playlists` (§5c), so this is the playlist
    count derived from the marker matrix itself rather than from `playlist_names()`. Returns 1 when
    there are no markers, so a markerless session is never refused.

    Extracted so it can be TESTED. `control_files` contains no multi-playlist session carrying
    markers, so a test written against the local corpus cannot make `set_markers` destroy anything
    and therefore cannot verify the guard -- removing the guard entirely left the corpus test
    passing. That is the fourth time in this session that the local fixture pool could not express
    a bug found on the wider corpus.
    """
    n77 = sum(1 for _z, _e, c in blocks if c == 0x2077)
    n06 = sum(1 for _z, _e, c in blocks if c == 0x2506)
    if not n77:
        return 1
    return max(1, n06 // n77)


def set_markers(data: bytes, markers, markers_ref: "bytes | None" = None) -> bytes:
    """Write session markers onto `data`. `markers` = list of (name, position_in_ticks) in
    time order, OR the dicts `markers()` returns (its "name"/"tick" keys are used), so
    `set_markers(d, markers(d))` composes. Markers live SOLELY in the 0x2030 marker list: a count + one 0x2077 record
    per marker (ordinal, length-prefixed name, position = ZERO_TICKS + ticks, a unique GUID).

    The 0x2030 template is INLINED (`_templates.MARKER_BLOCK_TEMPLATE`, a minimal 1-record
    block resized to N markers — byte-identical to a full donor for any marker list), so no
    donor is needed. Pass `markers_ref` (any session with a 0x2030) only to override the
    template (e.g. to regenerate the inlined copy). A marker adds no new top-level type, so
    it's the easy reindex case. Returns unxored bytes."""
    if not markers:
        return data
    # Accept what `markers()` RETURNS, not only the tuple form. The reader hands back dicts
    # ({"name", "tick", "sample"}) and this writer took (name, ticks) pairs, so the obvious
    # composition `set_markers(d, markers(d))` raised "too many values to unpack" on every one of
    # 88 corpus sessions that has a marker. A read layer whose output its own write layer cannot
    # consume is a gap in the API, not just an inconvenience.
    # A marker is EITHER tick-locked or SAMPLE-locked (§10: the u64 position's TOP byte, 0x40 vs
    # 0x00), and `markers()` reports a sample-locked one as tick=None with the value in "sample".
    # Encode whichever the caller gave, so both kinds round-trip; 25 of 88 corpus sessions carry
    # at least one sample-locked marker and used to be refused outright.
    def _encode(m):
        if isinstance(m, dict):
            if m.get("tick") is not None:
                return m["name"], 0x4000000000000000 + _ZERO_TICKS + int(m["tick"])
            if m.get("sample") is not None:
                return m["name"], int(m["sample"])
            raise ValueError(f"marker {m.get('name')!r} has neither a tick nor a sample position")
        name, tick = tuple(m)[:2]
        return name, 0x4000000000000000 + _ZERO_TICKS + int(tick)
    markers = [_encode(m) for m in markers]
    # REFUSE RATHER THAN DESTROY. The inlined 0x2030 template carries ONE 0x2506 row per 0x2077
    # record, i.e. it assumes a ONE-PLAYLIST session. The settled relation is
    # `n(0x2506) == n(0x2077) x n_playlists`, so splicing that template into a session with P
    # playlists silently drops `n(0x2077) x (P-1)` rows -- and nothing notices: `markers()` still
    # returns the right markers and `validate()` reports no new issue.
    #
    # Measured before this guard: `set_markers(d, markers(d))` -- the composition this docstring
    # advertises -- was byte-identical on 0 of 58 PT-authored sessions carrying markers, and 56 of
    # them LOST blocks: median -2,530 bytes, worst -227,176 bytes and -12,950 0x2506 records.
    # M12 MAGIC CARPET RIDE_MIX: 189 playlists, 0x2506 756 -> 380, which is exactly the 376 rows
    # one of its two 0x2030 blocks should have kept.
    #
    # Syncing the rows back afterwards is NOT a fix: `sync_marker_playlist_rows` adds to every
    # marker in EVERY 0x2030, including the block that was never replaced, giving 1132 where 378
    # is correct. The real fix is to rewrite the existing 0x2030 in place instead of splicing a
    # template over it; until that exists, refusing is the only honest behaviour for a write that
    # would otherwise destroy data it cannot restore.
    _rows = _marker_rows_per_record(_size_driven_blocks(data))
    if _rows > 1:
        raise ValueError(
            f"set_markers would destroy the marker/playlist matrix: this session carries "
            f"{_rows} 0x2506 rows per 0x2077 record ({_rows} playlists), and the inlined 0x2030 "
            f"template holds exactly one. Splicing it would drop every row but the first. "
            f"set_markers currently supports one-playlist sessions only."
        )
    if markers_ref is None:
        from . import _templates as _T
        template_2030 = _T.MARKER_BLOCK_TEMPLATE
    else:
        template_2030 = block_bytes(markers_ref,
                                    [b for b in parse(markers_ref).blocks if b.content_type == 0x2030][0])
    return _grow_block_into(data, 0x2030, _resize_marker_block(template_2030, markers))


def _patch_midi_note(block: bytearray, base: int, *, pitch=None, velocity=None,
                     length_ticks=None) -> None:
    """Patch the FIRST MIDI note in a 0x2000 events block (in place). `base` is the
    block's start within `block`. Layout after the "MdNLB" marker: +11 -> n_events
    (u32) -> the note records; record 0 begins at the n_events+4 boundary, with
    pitch at +8 (u8), length at +9 (5-byte LE ticks), velocity at +17 (u8)."""
    m = block.find(b"MdNLB", base)
    if m < 0:
        return
    rec = m + 11 + 4  # skip marker tail (11) + n_events (4); record 0 starts here
    if pitch is not None:
        block[rec + 8] = int(pitch) & 0x7F
    if length_ticks is not None:
        block[rec + 9 : rec + 14] = int(length_ticks).to_bytes(5, "little")
        # A second, length-derived field at rec+28 (signed i32): empirically
        # length//256 - 2134 for a note at clip start (fits the eighth/quarter/half/
        # whole controls byte-for-byte). Keep it consistent with the primary length.
        block[rec + 28 : rec + 32] = (int(length_ticks) // 256 - 2134).to_bytes(4, "little", signed=True)
    if velocity is not None:
        block[rec + 17] = int(velocity) & 0x7F


def add_midi_note(data: bytes, clean_ref: bytes, midi_ref: bytes, *,
                  pitch: "int | None" = None, velocity: "int | None" = None,
                  length_ticks: "int | None" = None) -> bytes:
    """Add a MIDI clip (one note) to session `data` by transplanting the clip's
    contribution from a matched control PAIR: `clean_ref` (a MIDI-track session
    WITHOUT the clip, e.g. `one midi track no clips v2.ptx`) and `midi_ref` (the SAME
    session WITH a one-note clip). Mirrors `add_audio_clip`.

    A MIDI clip touches a set of top-level blocks (the events block 0x2000, the region
    map 0x2634, the edit playlist 0x2624, the region/track maps 0x1058/0x2107, the name
    table 0x2519, plus display state) — `changed_top_level_types(clean_ref, midi_ref)`
    minus session/hardware-config blocks (`_REF_VOLATILE_TYPES`, kept from `data`).
    Those are transplanted from `midi_ref` and the master index is repaired by
    `_transplant_top_level`. `data` keeps its own name/display. The note's pitch
    (0..127), velocity (0..127) and length (in PT ticks; quarter = 960000) can be set;
    otherwise the ref's note is used.

    MIDI-EDITOR WINDOW: Pro Tools normally pops the MIDI editor window front-and-center
    when it loads a session that has one (encoded as ~10 records in 0x2587). Pass an
    EDITOR-CLOSED `midi_ref` (a one-note session resaved with the editor window closed,
    e.g. `midi note editor closed.ptx`) to suppress it — its 0x2587 lacks those records,
    so the result opens to just the Edit window. PT-CONFIRMED.

    `data` / `clean_ref` must share scaffold (same track layout) so the blocks line up.
    BYTE-EXACT to `midi_ref` (mod session-name) when `data` IS `clean_ref`; the note
    patch reproduces the pitch/velocity/length control variants byte-for-byte. Returns
    full unxored bytes (ready for `writer.encrypt_session_data`)."""
    types = [t for t in changed_top_level_types(clean_ref, midi_ref)
             if t not in _REF_VOLATILE_TYPES]
    out = bytearray(_transplant_top_level(data, midi_ref, types))
    if pitch is None and velocity is None and length_ticks is None:
        return bytes(out)
    for b in [x for x in parse(bytes(out)).blocks if x.content_type == 0x2000]:
        s = b.offset - 7
        _patch_midi_note(out, s, pitch=pitch, velocity=velocity, length_ticks=length_ticks)
    return bytes(out)


# --- click-on-top: move the (bottom) Click track to the TOP of the edit window ---
#
# Pro Tools stores the EDIT-WINDOW track display order NOT in the body block order,
# the overview, the window-config, or the per-track counters (all ruled out by PT
# loads), but as a PLAYLIST-ORDER LIST in the master index: the `0x2624` count==1
# container record's `elements[1:]` and each `0x2624` count==4 instance's
# `child_refs[1]` point at the playlists IN DISPLAY ORDER. PT sorts the edit window
# by this list. (Decoded 2026-05-31 by diffing a user drag-and-resave against a
# byte-identical synthesized session — only these 6 offsets differed at N=2.)
#
# move_click_to_top reproduces what Pro Tools writes when you drag the click to the
# top: it (1) reorders the body so the click's per-track blocks lead each run, (2)
# rank-refills the index (lanes are one type, so rank == display position), (3)
# rotates the playlist-instances click->ordinal 1 and fixes their childtypes, and
# (4) sets the playlist-order list (container + instance childref[1]) to display
# order, click first. Validated byte-exact vs a real click-on-top control at N=2;
# PT-CONFIRMED at N=2 and N=12.


def _reorder_click_to_front_body(data: bytes) -> bytes:
    """Move the click's block to the FRONT of each per-track run in the body (name-
    table entry, both lane-major 0x251a groups, 0x210b, and the 0x2624 playlist
    subtree), renumbering the embedded 1-based position ordinal (the u16 after each
    GUID) in the name/lane runs. Pure size-neutral rearrangement; returns the body."""
    ref = _FI.final_index_ref(data)
    body = data[: ref.start]
    bptf = parse(body)
    by = lambda ct: _by_type(bptf, ct)
    regions = []  # (slots[(lo,hi)] in old order, click_slot_index, renumber:bool)

    b2519 = by(0x2519)[0]
    first_child = min(c.offset - 7 for c in b2519.child)
    table = body[b2519.offset : first_child]
    name_offs = [m.start() for m in _TRACK_NAME.finditer(table)]
    estart = [b2519.offset + no - 6 for no in name_offs]  # entry = sep(2)+len(4)+name+...
    ebounds = [(estart[i], estart[i + 1] if i + 1 < len(estart) else first_child) for i in range(len(estart))]
    cidx = next(i for i, (a, b) in enumerate(ebounds) if b"Click 1" in body[a:b])
    regions.append((ebounds, cidx, True))

    lanes = sorted([c for c in b2519.child if c.content_type == 0x251A], key=lambda c: c.offset)
    half = len(lanes) // 2  # lane-major: [lane0 of all tracks][lane1 of all tracks]
    for lo, hi in [(0, half), (half, len(lanes))]:
        grp = lanes[lo:hi]
        sl = [(c.offset - 7, c.offset + c.block_size) for c in grp]
        ci = next(i for i, c in enumerate(grp) if _own_track_name(body, c) == "Click 1")
        regions.append((sl, ci, True))

    b2107 = by(0x2107)[0]
    k7 = sorted(b2107.child, key=lambda c: c.offset)
    sl7 = [(c.offset - 7, c.offset + c.block_size) for c in k7]
    regions.append((sl7, next(i for i, c in enumerate(k7) if _own_track_name(body, c) == "Click 1"), False))

    b2624 = by(0x2624)[0]
    k24 = sorted(b2624.child, key=lambda c: c.offset)
    sl24 = [(c.offset - 7, c.offset + c.block_size) for c in k24]
    regions.append((sl24, next(i for i, c in enumerate(k24) if c.content_type == 0x261E), False))

    regions = sorted(regions, key=lambda r: r[0][0][0])
    out = bytearray()
    cursor = 0
    for slots, click, renum in regions:
        out += body[cursor : slots[0][0]]
        order = [click] + [i for i in range(len(slots)) if i != click]
        for new_slot, oi in enumerate(order):
            slo, shi = slots[oi]
            chunk = bytearray(body[slo:shi])
            if renum:
                j = chunk.find(b"\x2a\x00\x00\x00")
                if 0 <= j and j + 14 <= len(chunk):
                    chunk[j + 12 : j + 14] = (new_slot + 1).to_bytes(2, "little")
            out += bytes(chunk)
        cursor = slots[-1][1]
    out += body[cursor:]
    return bytes(out)


def move_click_to_top(data: bytes) -> bytes:
    """Move a bottom-Click session's Click track to the TOP of the edit window.
    `data` is a full unxored session whose last track is a Click (e.g. the output of
    `add_click_anyN`). Returns full unxored bytes (ready for `writer.encrypt_session_data`).
    PT-CONFIRMED at N=2 and N=12 (stereo); byte-exact vs the real click-on-top control at N=2."""
    ref = _FI.final_index_ref(data)
    index = data[ref.start :]
    _r, holes = _FI.offset_holes(data)  # capture (type, rank) from the consistent input
    newbody = _reorder_click_to_front_body(data)

    # rank-refill the index for the reordered body (lanes & same-type per-track holes
    # get display order automatically; the playlist list is fixed up explicitly below).
    buf = bytearray(newbody + index)
    new_index_start = len(newbody)
    _z, by_type0 = _FI.block_layout(bytes(buf))
    for abs_pos, _val, ttype, rank, _kind in holes:
        npos = new_index_start + (abs_pos - ref.start)
        buf[npos : npos + 4] = int(by_type0[ttype][rank]).to_bytes(4, "little")

    zt, btmap = _FI.block_layout(bytes(buf))
    recs = _FI.parse_records(bytes(buf[new_index_start:]), set(btmap), set(zt))

    # The playlist-instances (0x2624 count==4) stay in stream order with ordinals
    # 1..N+1; rank-refill already pointed ordinal-1's rank-based child_refs at the
    # click's (now physically-first = rank-0) blocks. Relabel by ORDINAL: ordinal 1
    # is the click. Set the playlist-kind childtype accordingly.
    for r in recs:
        if r.content_type == 0x2624 and r.count == 4 and r.child_refs:
            r.child_refs[0].child_type = 0x261E if r.ordinal == 1 else 0x261C

    # THE display-order field: the playlist-order list, click first. The 0x2624
    # count==1 container's elements[1:] and each count==4 instance's child_refs[1]
    # point at the playlists in edit-window display order (click first).
    fb = flat_blocks(parse(newbody))
    click_pl = [b.offset - 7 for b in fb if b.content_type == 0x261E][0]
    audio_pls = sorted(b.offset - 7 for b in fb if b.content_type == 0x261C)
    display = [click_pl] + audio_pls
    for r in recs:
        if r.content_type == 0x2624 and r.count == 1 and r.flag == 1 and len(r.elements) >= 1 + len(display):
            for i, off in enumerate(display):
                r.elements[1 + i].offsets = [off]
        if r.content_type == 0x2624 and r.count == 4 and len(r.child_refs) >= 2:
            r.child_refs[1].offset = display[r.ordinal - 1]

    return _set_index_offset(newbody + _FI.serialize_final_block(recs))


# --- general track reordering (edit-window display order) ---------------------
#
# The edit-window track order is the index PLAYLIST-ORDER LIST and NOTHING ELSE —
# PT-CONFIRMED: setting just this list (body untouched) reorders the display
# (Audio 4 to top, click to middle, etc. all confirmed). So arbitrary reordering
# is a pure index edit: permute the list. (move_click_to_top additionally reorders
# the body to match what PT re-writes on save, but that is cosmetic — not required
# for the display.)


def track_playlist_order(data: bytes) -> list[tuple[int, int]]:
    """The track playlists in body/creation order as (zmark_offset, content_type):
    0x261c audio, 0x261e click/aux, 0x261d master, 0x261f VCA, 0x2620/0x2621 MIDI, 0x2623
    (see `_DISPLAY_ORDER_TYPES` -- the set is MEASURED from what real display lists point at,
    and the earlier three-kind reading left the enumeration short on 54 sessions).
    This is the list `reorder_tracks`'s
    `new_order` indexes (index 0 = the first-created track, etc.). All track kinds share
    one display-order list, so MIDI is included — its playlist is 0x2620."""
    # SIZE-DRIVEN, not the 0x5A-scan parser. The scan parser silently UNDERCOUNTS real
    # sessions -- 37 playlists where there are 119, 29 where there are 104 -- on 31 of 140
    # corpus sessions (22%). It does not raise; it just returns a short list, and
    # `reorder_tracks` indexes this list, so a caller would have been reordering a third of the
    # tracks and writing an index element list that omits the rest. The size-driven walk is the
    # authoritative enumeration everywhere else in this module for exactly this reason.
    return sorted(((z, c) for z, _e, c in _size_driven_blocks(data)
                   if c in _DISPLAY_ORDER_TYPES), key=lambda x: x[0])


def _container_display_offsets(data: bytes) -> list[int]:
    """The track-playlist offsets in CURRENT display order — the 0x2624 count==1 container's
    elements[1:] (what Pro Tools reads to order the edit window). For a freshly synthesized
    session this equals build/spec order, which is NOT the same as `track_playlist_order`'s
    body-offset order once MIDI is present (MIDI 0x2620 playlists cluster in the body away from
    the interleaved audio 0x261c). Used to translate a spec/display-relative permutation into
    `reorder_tracks`' body-offset index space."""
    # SIZE-DRIVEN block sets, not `block_layout`'s scan-derived ones. `parse_records` frames a
    # record's elements by "is this offset a live zmark", so a short zmark set makes a real
    # element look like garbage: this raised `bad element tag ... record type 0x2624` on 33 of
    # 140 corpus sessions, which is why the display order could not be read on any of them.
    # Given the size-driven sets the same index parses cleanly on all 140.
    ref = _FI.final_index_ref(data)
    _bl = sorted(_raw_block_bounds(data, ref.start))
    for r in _FI.parse_records(ref.data, {c for _z, _e, c in _bl}, {z for z, _e, _c in _bl}):
        if r.content_type == 0x2624 and r.count == 1 and r.flag == 1:
            return [e.offsets[0] for e in r.elements[1:] if e.offsets]
    return []


def reorder_tracks(data: bytes, new_order: list[int]) -> bytes:
    """Set the edit-window track display order to `new_order` — a permutation of
    0..N-1 over the track playlists in body/creation order (see track_playlist_order;
    e.g. [3,0,1,2] moves the 4th track to the top). Returns full unxored bytes
    (ready for `writer.encrypt_session_data`).

    INDEX-ONLY: the edit-window order is the entry order of the master track-list — the
    0x2624 count==1 container's elements[1:] (one offset per track playlist, ALL kinds
    interleaved). Pro Tools displays the tracks in that list order (document order, no sort).
    This sets the list to `new_order`; the body is left untouched.
    Each count==4 per-track playlist instance (audio/click only — MIDI has none) is keyed to
    ITS OWN track: its ordinal = that track's display position; its back-pointer/childtype
    stay its own playlist (a MIDI track interleaved between audio leaves a gap in the audio
    instances' ordinals, e.g. 1,3 — exactly as a real PT save). Works for any mix of audio,
    click, and MIDI at any position. PT-CONFIRMED (audio move-to-top/reverse, click-to-middle,
    MIDI-anywhere structurally byte-matches the multiple-track-types controls)."""
    pls = track_playlist_order(data)
    if sorted(new_order) != list(range(len(pls))):
        raise ValueError(f"new_order must be a permutation of 0..{len(pls) - 1}; got {new_order}")
    target = [pls[i] for i in new_order]
    toff = [o for o, _t in target]
    pos_of = {o: i + 1 for i, (o, _t) in enumerate(target)}  # playlist offset -> 1-based display pos
    ref = _FI.final_index_ref(data)
    # SIZE-DRIVEN sets, for the same reason as `_container_display_offsets`: `parse_records`
    # frames a record's elements by "is this offset a live zmark", so `block_layout`'s
    # scan-derived sets made real elements look like garbage and raised
    # `bad element tag ... record type 0x2624` on 33 of 139 corpus sessions -- every one of them
    # a session this function could not touch at all.
    _bl = sorted(_raw_block_bounds(data, ref.start))
    recs = _FI.parse_records(ref.data, {c for _z, _e, c in _bl}, {z for z, _e, _c in _bl})
    for r in recs:
        if r.content_type == 0x2624 and r.count == 1 and r.flag == 1 and len(r.elements) >= 1 + len(toff):
            for i, off in enumerate(toff):
                r.elements[1 + i].offsets = [off]
        elif r.content_type == 0x2624 and r.count == 4 and len(r.child_refs) >= 2:
            # Per-track playlist instance, keyed to its own track: set ordinal = that track's
            # display position; keep its own back-pointer + childtype. Repointing by ordinal
            # position instead (the old audio-only logic) would assign an audio instance a MIDI
            # (0x2620) childtype where MIDI interleaves. Display order comes from the list above.
            own = r.child_refs[1].offset
            if own in pos_of:
                r.ordinal = pos_of[own]
    return _set_index_offset(data[: ref.start] + _FI.serialize_final_block(recs))


# --- track edit-window VIEW MODE (waveform vs volume) -------------------------
#
# A track's edit-window view is a small block whose CONTENT_TYPE *is* the view mode:
# 0x203b = VOLUME (22 B), 0x2038 = WAVEFORM (19 B), 0x203a = MIDI (no waveform/volume). The
# 0x2037 child is that view block's payload, and the volume form carries an extra trailer
# `01 00 00 00` vs waveform's `00`. The catch: a track's view block appears MANY times — in
# its overview/display chains AND, crucially, EMBEDDED inside its own playlist block (0x261c
# audio / 0x261e click), nested 0x200d->0x200a->0x2015->0x203b. The BLOCK PARSER does NOT
# descend into the playlist payload, so the parser-tree never even sees those embedded view
# blocks — which is exactly where the click splice leaves VOLUME view.
#
# The click splice (`add_click_anyN`) replays a click control that was saved in volume view,
# so the click's 0x261e playlist embeds 4 VOLUME view blocks (its embedded view sequence is
# VVVVVWVW vs a normal audio playlist's WWVVWWWW). Pro Tools renders the click track —
# wherever it lands in the display order — in volume view. (The edit window is ordered by the
# master index's display list, so the click sits at display position 3 in a 'bottom' build and
# that audible row shows volume; in 'top' a MIDI track sits there and has no waveform/volume
# view, hiding the bug — but the same 4 volume bytes are present.)
#
# THE TWO V's THAT MUST STAY: every audio playlist (incl. the fine no-click session) also
# embeds 2 VOLUME blocks that belong to real 0x2580 volume-AUTOMATION lanes — those render
# fine and MUST be left alone. The discriminator: a genuine automation VOLUME block's
# immediate parent is its 0x2580 automation-lane block; a view-volume block's immediate parent
# is its display chain (0x2015 / 0x200a / 0x2589). So we convert every 0x203b whose immediate
# parent is NOT 0x2580.
#
# Because the targets live in playlist payload the parser can't reach, set_waveform_view
# works by RAW byte scan + a raw recursive block-bounds walk (not the parser tree), then
# shrinks every enclosing block (-3 B each) and repairs the master index. No-op on an
# already-waveform session; idempotent. Generated sessions run this LAST (after the splice).
_VIEW_VOLUME = bytes.fromhex("5a01000f0000003b205a010002000000372001000000")    # 0x203b, 22 B
_VIEW_WAVEFORM = bytes.fromhex("5a01000c00000038205a010002000000372000")        # 0x2038, 19 B


def _raw_block_bounds(data: bytes, end0: int) -> "list[tuple[int, int, int]]":
    """Every block in `data[:end0]` as (zmark, end, content_type), found by a RAW
    recursive descent over the 0x5A framing (header = 5A <type:2> <size:4> <ct:2>;
    block spans [zmark, zmark+7+size)). Unlike the parser, this descends into EVERY
    block — including playlist payloads (0x261c/0x261e) the parser treats as opaque —
    so the deep embedded view chain (0x200d->0x200a->0x2015->0x203b) is reachable."""
    out: list[tuple[int, int, int]] = []

    def descend(lo: int, hi: int) -> None:
        pos = lo
        while pos < hi:
            nz = data.find(b"\x5a", pos, hi)   # C-speed jump to the next 0x5A
            if nz < 0:
                return
            pos = nz
            block_type = int.from_bytes(data[pos + 1 : pos + 3], "little")
            if not (block_type & 0xFF00):
                size = int.from_bytes(data[pos + 3 : pos + 7], "little")
                ct = int.from_bytes(data[pos + 7 : pos + 9], "little")
                end = (pos + 7) + size
                if size > 0 and end <= hi:
                    if size == 4 and int.from_bytes(data[pos + 7 : pos + 11], "little") == end0:
                        # The index-POINTER block (the file's first, 4-byte payload). Its payload is
                        # the absolute offset of the trailing 0x0002 index, so the generic framing
                        # reads the low half of that offset as a content_type -- a value that varies
                        # per session and is not a type at all. Reported as -1 so it can never be
                        # mistaken for one: it collided with 0x261C on a generated session and was
                        # counted as a fifth track record, which made the name-table guard refuse a
                        # perfectly good file ("4 entries parsed for 5 track records").
                        ct = -1
                    out.append((pos, end, ct))
                    descend(pos + 9, end)   # children begin after the ct word
                    pos = end
                    continue
            pos += 1

    descend(0, end0)
    return out


def _view_volume_offsets(data: bytes, end0: int,
                         bounds: "list[tuple[int, int, int]] | None" = None) -> "list[int]":
    """Offsets of every _VIEW_VOLUME (0x203b, 22 B) signature in `data[:end0]` that is a
    track-VIEW block, NOT a real 0x2580 volume-AUTOMATION lane. Discriminator (exact, parent-
    based): the IMMEDIATE enclosing block of an automation VOLUME is its 0x2580 automation lane;
    a view VOLUME's immediate parent is its display chain (0x2015 / 0x200a / 0x2589).
    `bounds` = `_raw_block_bounds(data, end0)` (computed if not supplied)."""
    if bounds is None:
        bounds = _raw_block_bounds(data, end0)
    out: list[int] = []
    pos = 0
    while True:
        j = data.find(_VIEW_VOLUME, pos)
        if j < 0 or j >= end0:
            break
        pos = j + 1
        # immediate parent = the strict container with the largest zmark (excludes the 0x203b itself)
        parent_ct = None
        best_z = -1
        for z, end, ct in bounds:
            if z < j and end >= j + len(_VIEW_VOLUME) and z > best_z:
                best_z, parent_ct = z, ct
        if parent_ct != 0x2580:   # 0x2580 = volume-automation lane (leave alone); else a view block
            out.append(j)
    return out


def set_waveform_view(data: bytes) -> bytes:
    """Force every track's edit-window VIEW to WAVEFORM (leaving real 0x2580 volume
    automation alone), then repair block sizes and the master index.

    A track's view is the content_type of its view block: 0x203b = VOLUME (22 B),
    0x2038 = WAVEFORM (19 B). These are embedded inside the track's playlist block
    (0x261c/0x261e), nested too deep for the block parser to reach, so we scan RAW bytes
    for the 0x203b view signature, convert each to the 0x2038 waveform signature (-3 B),
    shrink every enclosing block by 3 (via a raw recursive bounds walk), and recompute the
    index offsets. The 2 VOLUME blocks on every audio playlist that belong to real 0x2580
    automation lanes are detected by their preceding 0x2580 header and left untouched.
    Byte-for-byte the same edit PT writes when you switch a track to waveform view. No-op
    on an already-waveform session; idempotent. The build runs this LAST, after the click
    splice (which is what introduces the volume view). Returns full unxored bytes."""
    ref = _FI.final_index_ref(data)
    if ref is None:
        return data
    idx_start = ref.start
    bounds = _raw_block_bounds(data, idx_start)        # raw framing; reused for discriminate + fixup
    targets = _view_volume_offsets(data, idx_start, bounds)
    if not targets:
        return data
    delta = len(_VIEW_WAVEFORM) - len(_VIEW_VOLUME)   # -3 per converted block
    _r, holes = _FI.offset_holes(data)
    body = bytearray(data[:idx_start])
    index = data[idx_start:]
    # shrink every block that STRICTLY contains a target (raw bounds — the ancestors include
    # the hidden 0x261e->0x200d->0x200a->0x2015 chain the parser never descends into).
    size_delta: dict[int, int] = {}
    for off in targets:
        for z, end, _ct in bounds:
            if z < off and end >= off + len(_VIEW_VOLUME):   # strict container (z != off)
                size_delta[z] = size_delta.get(z, 0) + delta
    for z, dz in size_delta.items():
        sp = z + 3
        sz = int.from_bytes(body[sp : sp + 4], "little")
        body[sp : sp + 4] = (sz + dz).to_bytes(4, "little")
    # splice high->low so lower offsets stay valid
    for off in sorted(targets, reverse=True):
        body[off : off + len(_VIEW_VOLUME)] = _VIEW_WAVEFORM
    out = bytearray(bytes(body) + index)
    new_index_start = len(body)
    _z2t, by_type = _FI.block_layout(bytes(out))
    for abs_pos, _value, ttype, rank, _kind in holes:
        npos = new_index_start + (abs_pos - idx_start)
        out[npos : npos + 4] = int(by_type[ttype][rank]).to_bytes(4, "little")
    return _set_index_offset(bytes(out))


# Header count fields at stable content-relative offsets (block.offset + rel),
# value = N tracks. 0x1054 (= total audio CHANNELS, 2N stereo / N mono) is patched
# separately from `n_channels`. Occurrence picks the right block instance.
_HEADER_COUNTS = [
    (0x1015, 0, 2, 1), (0x2107, 0, 11, 1), (0x2624, 0, 2, 1),
    (0x2519, 0, 16, 1), (0x202a, 0, 18, 1), (0x202a, 1, 18, 1),
    # NOTE: 0x202b's two counts are just the two 0x202a children's counts viewed
    # from 0x202b's frame (content+31 == first 0x202a count; the "second" one sits
    # AFTER the first 0x202a child, so its offset shifts with N). Both are already
    # patched per-0x202a above — patching 0x202b at a fixed offset corrupts the
    # first 0x202a once it has grown (multi-step high-N bug).
    (0x2551, 0, 1088, 1), (0x2587, 0, 1097, 1), (0x258a, 1, 316, 1), (0x258b, 1, 1009, 1),
]


def _patch_counts(body: bytearray, n: int, n_channels: int) -> None:
    """Patch the N-dependent header count fields. `n` = track count (drives most
    fields); `n_channels` = total audio channels (0x1054 = 2N stereo / N mono)."""
    ptf = parse(bytes(body))
    occ: dict[int, int] = {}
    by_occ: dict[tuple[int, int], Block] = {}
    for b in flat_blocks(ptf):
        i = occ.get(b.content_type, 0); occ[b.content_type] = i + 1
        by_occ[(b.content_type, i)] = b
    for ct, o, rel, mult in _HEADER_COUNTS:
        b = by_occ.get((ct, o))
        if b is not None:
            _patch_u32(body, b.offset + rel, n * mult)
    # 0x1054 = total audio channels (decouples from track count under mono/mix).
    b1054 = by_occ.get((0x1054, 0))
    if b1054 is not None:
        _patch_u32(body, b1054.offset + 2, n_channels)
    # 0x2519 deep second count. Anchor: 01 00 00 00 fe ff <u32 count> 5a 0a.
    # The `5a 0a` (next 0x251a ZMARK) after the u32 distinguishes the real count
    # from the end of every 0x251a block (which is `...01 00 00 00 fe ff` followed
    # IMMEDIATELY by `5a 0a`, with no u32 in between).
    b2519 = by_occ.get((0x2519, 0))
    if b2519 is not None:
        s, e = b2519.offset - 7, b2519.offset + b2519.block_size
        scan = s
        while True:
            anchor = body.find(b"\x01\x00\x00\x00\xfe\xff", scan, e)
            if anchor < 0:
                break
            if body[anchor + 10 : anchor + 12] == b"\x5a\x0a":
                _patch_u32(body, anchor + 6, n)
                break
            scan = anchor + 1

    # NOTE: the overview display-order permutation is NOT patched here. It is a
    # whole-session permutation of 0..N-1 (hash-table iteration order) that
    # reshuffles unpredictably as tracks are added, so it cannot be derived by a
    # per-step insertion. `synthesize_stereo_session` rewrites the full sequence
    # in one shot from the target control via `_set_overview_order`.


def _patch_u16(buf: bytearray, pos: int, val: int) -> None:
    buf[pos : pos + 2] = int(val).to_bytes(2, "little")


# --- overview display-order permutation -------------------------------------
# Each track contributes one entry `89 25 <order:u16> 01 00 61 00 5a 01 ...`
# inside the second 0x258a block (89 25 = the 0x2589 content_type marker, order
# is the first content word). Across an N-track session the N order values form a
# permutation of 0..N-1 whose layout is a hash-table iteration order: it shuffles
# wholesale for small N and stabilises into pure insertions for N>=8. There is no
# cheap closed form, so we copy the exact sequence from a real N-track control.
_OVW_ANCHOR = b"\x01\x00\x61\x00\x5a\x01"


def _overview_order_offsets(data: bytes) -> list[int]:
    """Byte offsets of each overview order u16 (the `<order>` word that sits two
    bytes before every `01 00 61 00 5a 01` anchor), in file order."""
    offs: list[int] = []
    pos = 0
    while True:
        j = data.find(_OVW_ANCHOR, pos)
        if j < 0:
            break
        offs.append(j - 2)
        pos = j + 1
    return offs


def overview_order(data: bytes) -> list[int]:
    """The N overview display-order values of a session, in file order."""
    return [int.from_bytes(data[o : o + 2], "little") for o in _overview_order_offsets(data)]


# --- overview scroll-extent field -------------------------------------------
# A session-level value that is 0 for N<=9 but non-zero once the tracks overflow
# the edit window (n10-12=311, n13-16=602 for the controls' window size). It is
# replicated in the fixed-size overview blocks 0x2016 (rel 34/924/1217) and
# 0x2581 (rel 6/299, per block), which otherwise match the grow exactly. The
# grow leaves it 0 -- correct for <=9 tracks but a hard load failure ("magic ID")
# for >=10. There's no closed form (it's the saved window scroll), so copy it
# from the target control like the overview order / name table.
_OVW_EXTENT_FIELDS: dict[int, tuple[int, ...]] = {0x2016: (34, 924, 1217), 0x2581: (6, 299)}


def overview_extent(data: bytes) -> int:
    """Read the overview scroll-extent value (u32 at the first 0x2581 + 6)."""
    ptf = parse(data)
    for b in flat_blocks(ptf):
        if b.content_type == 0x2581:
            return int.from_bytes(data[b.offset + 6 : b.offset + 10], "little")
    return 0


def _set_overview_extent(body: bytearray, value: int) -> None:
    """Write the overview scroll-extent value into every 0x2016/0x2581 slot."""
    ptf = parse(bytes(body))
    for b in flat_blocks(ptf):
        rels = _OVW_EXTENT_FIELDS.get(b.content_type)
        if rels:
            for rel in rels:
                _patch_u32(body, b.offset + rel, value)


# --- window state: visible-track markers + session-info size -----------------
# Pro Tools stores which tracks are in the scrolled edit-window view via a marker
# run (`ef ff df bf ...`) in each visible track's 0x261c/0x200a/0x200b/0x2015/
# 0x2104. The donor's active track carries a stray marker that, in the target
# session, lies outside the visible range -> inconsistent window state. (Tolerated
# while N<=9 because there's no scroll, but a load failure once tracks overflow.)
_WINDOW_MARK_TYPES = (0x261c, 0x200a, 0x200b, 0x2015, 0x2104)
_WMARK_SET = {0xEF, 0xFF, 0xDF, 0xBF, 0x02}
_WMARK_START = bytes.fromhex("efffdfbf")


def _clear_donor_window_markers(body: bytes, base_n: int) -> bytes:
    """Zero the stray visible-track marker on the donor's tracks (1..base_n).
    Those tracks sit below the target's visible range (which is the last tracks),
    so they must carry no marker -- matching the real control. No-op for donors
    with no marker (N<=4). Library-sourced tracks keep their correct markers."""
    out = bytearray(body)
    ptf = parse(bytes(out))
    for ct in _WINDOW_MARK_TYPES:
        blks = sorted((b for b in flat_blocks(ptf) if b.content_type == ct), key=lambda b: b.offset)
        for i, b in enumerate(blks):
            if i >= base_n:
                continue
            s, end = b.offset, b.offset + b.block_size
            while True:
                j = bytes(out[s:end]).find(_WMARK_START)
                if j < 0:
                    break
                p = s + j
                q = p
                while q < end and out[q] in _WMARK_SET:
                    q += 1
                for k in range(p, q):
                    out[k] = 0
                s = q
    return bytes(out)


def _session_info_block(ptf: PTFFormat) -> Block:
    return [b for b in flat_blocks(ptf) if b.content_type == 0x2067][0]


def _match_session_info_size(body: bytes, library_data: bytes) -> bytes:
    """Transplant the library's 0x2067 (session-info) block when its size differs
    from the donor's. The 0x2067 embeds the session name; a different name length
    makes the block a different size, which SHIFTS every following block by that
    delta. The recomposed index tolerates the shift but Pro Tools validates a
    block position from another source that does not -> "magic ID does not match"
    at N>=10 (where the control's name "10 stereo tracks" is one char longer than
    a single-digit donor's). Only transplanted on a size mismatch, so same-length
    cases (e.g. 2->3) are untouched."""
    bptf = parse(body)
    b = _session_info_block(bptf)
    lb = _session_info_block(parse(library_data))
    if b.block_size == lb.block_size:
        return body
    new = library_data[lb.offset - 7 : lb.offset + lb.block_size]
    delta = len(new) - (b.block_size + 7)
    bz = b.offset - 7
    out = bytearray(body)
    for zmark in _ancestor_chain(bptf, bz)[1:]:  # containers only; 0x2067's own size is in `new`
        sp = zmark + 3
        old = int.from_bytes(out[sp : sp + 4], "little")
        out[sp : sp + 4] = (old + delta).to_bytes(4, "little")
    out[bz : b.offset + b.block_size] = new
    return bytes(out)


# --- per-track 'selected' state ---------------------------------------------
# A track's 0x261c playlist carries two 1-byte "selected" booleans (01=selected,
# 00=not). They sit at fixed offsets from a distinctive 9-byte view-state run
# (`ef ff df bf ef ff df bf 02`): flag1 = run-40, flag2 = run+186. Anchoring on
# the run (rather than an absolute offset) is robust to BOTH the track-name
# length (which shifts the tail) and the session path length (era A vs B). NOTE
# the run itself is NOT the selection state — many unselected tracks also have it
# (e.g. n16 tracks 8-15) with the flags at 00; only the two flag bytes encode
# selection, so we touch only those.
_SEL_ANCHOR = bytes.fromhex("efffdfbfefffdfbf02")
_SEL_FLAG1_REL = -40
_SEL_FLAG2_REL = 186


def _sel_flag_positions(block_bytes: bytes) -> tuple[int, int] | None:
    p = block_bytes.find(_SEL_ANCHOR)
    if p < 0:
        return None
    f1, f2 = p + _SEL_FLAG1_REL, p + _SEL_FLAG2_REL
    if f1 < 0 or f2 >= len(block_bytes):
        return None
    return f1, f2


def selected_tracks(data: bytes) -> list[int]:
    """1-based indices of tracks whose 0x261c selection flag is set."""
    # SIZE-DRIVEN. The scan parser undercounts 0x261C in 9 of 312 corpus sessions, and the value
    # returned here is `i + 1` -- a POSITIONAL index into this very list. A missing 0x261C shifts
    # every later index, so the caller is handed the wrong TRACK NUMBERS with nothing to show it.
    # Same defect class as clip_positions/set_clip_position and the 0x251A lane count.
    out: list[int] = []
    for i, (z, e, _c) in enumerate(
            [b for b in _size_driven_blocks(data) if b[2] == 0x261C and b[0] >= 31]):
        blk = data[z + 7 : e]
        fp = _sel_flag_positions(blk)
        if fp and blk[fp[0]] == 0x01:
            out.append(i + 1)
    return out


def _set_track_selection(body: bytearray, selected_track_1based: int | None) -> None:
    """Set track selection. `selected_track_1based=None` deselects every track
    (the neutral default — real controls show both none-selected and
    last-selected, so it's arbitrary saved UI state). Growing from a donor leaves
    the donor's last track AND the appended last track both selected; this clears
    the stray selections by flipping only the two flag bytes (no other content)."""
    ptf = parse(bytes(body))
    a1c = sorted((b for b in flat_blocks(ptf) if b.content_type == 0x261c), key=lambda b: b.offset)
    for i, b in enumerate(a1c):
        blk = bytes(body[b.offset : b.offset + b.block_size])
        fp = _sel_flag_positions(blk)
        if fp is None:
            continue
        want = 0x01 if (i + 1) == selected_track_1based else 0x00
        body[b.offset + fp[0]] = want
        body[b.offset + fp[1]] = want


def _set_overview_order(body: bytearray, values: list[int]) -> None:
    """Overwrite every overview order u16 (in file order) with `values`. The
    count must match — both the grown body and the source control hold exactly N
    entries, so this is a positional copy of the target permutation."""
    offs = _overview_order_offsets(bytes(body))
    if len(offs) != len(values):
        raise ValueError(f"overview order count mismatch: body has {len(offs)}, want {len(values)}")
    for o, v in zip(offs, values):
        _patch_u16(body, o, v)


def first_block_counter_value(n: int) -> int:
    """The per-N value stored in the first top-level block's type field."""
    return 2530 * n + 1886


def _first_block_type(data: bytes) -> int:
    ptf = parse(data)
    first = min(ptf.blocks, key=lambda b: b.offset)
    return first.content_type


def _set_counter(body: bytes, value: int) -> bytes:
    out = bytearray(body)
    ptf = parse(body)
    first = min(ptf.blocks, key=lambda b: b.offset)
    out[first.offset : first.offset + 2] = int(value).to_bytes(2, "little")
    return bytes(out)


def _synthesize_session(
    donor_data: bytes,
    base_n: int,
    target_n: int,
    library_data: bytes,
    library_total: int,
    channels: int,
) -> bytes:
    """Grow `donor_data` (a uniform `base_n`-track session of `channels`-channel
    audio tracks) to `target_n` tracks; return full unxored session bytes (ready
    for `writer.encrypt_session_data`). `channels`=2 stereo, 1 mono. Per-track
    blocks for base_n+1..target_n come from `library_data` (a uniform control with
    >= target_n tracks; deterministic per index)."""
    body = donor_data[: _FI.final_index_ref(donor_data).start]
    for k in range(base_n + 1, target_n + 1):
        unit = extract_track(library_data, k, library_total, channels=channels)
        body = grow_one_track(body, k - 1, unit)
    body = bytearray(body)
    # The first-block "counter" is a per-session value that is only linear in N
    # for N<=4; beyond that it depends on session history. Source it from the
    # library if the library has exactly target_n tracks, else fall back to the
    # (approximate) formula.
    counter = _first_block_type(library_data) if library_total == target_n \
        else first_block_counter_value(target_n)
    body = bytearray(_set_counter(bytes(body), counter))
    # Overwrite the overview display-order permutation in one shot. Growing the
    # body track-by-track leaves the donor's original tracks holding their low-N
    # order values while appended tracks bring the library's values -> an invalid
    # sequence with duplicates. The correct N-track permutation is taken from the
    # target control (library when it has exactly target_n tracks).
    if library_total == target_n:
        _set_overview_order(body, overview_order(library_data))
        # Transplant the exact name table (variable-length multi-digit entries +
        # last-entry boundary quirk make a per-step rebuild fragile).
        body = bytearray(_set_name_table(bytes(body), library_data))
        # Copy the overview scroll-extent (0 for <=9 tracks; non-zero once tracks
        # overflow the window -- the grow leaves it 0, which fails to load at >=10).
        _set_overview_extent(body, overview_extent(library_data))
        # Deselect every track. Growing inherits the donor's last-track selection
        # AND the appended last track's selection. This MUST run before the window
        # markers are cleared, because deselection anchors on the same `ef ff df bf`
        # run that the marker clear zeros.
        _set_track_selection(body, None)
        # Clear the donor's stray visible-track window markers (its active track is
        # outside the target's visible range).
        body = bytearray(_clear_donor_window_markers(bytes(body), base_n))
        # Match the session-info (0x2067) size to the target so the body isn't
        # shifted by a session-name-length difference (the >=10 "magic ID" cause).
        body = bytearray(_match_session_info_size(bytes(body), library_data))
    else:
        raise NotImplementedError(
            "overview order requires a target_n-track library (library_total == "
            f"target_n); got library_total={library_total}, target_n={target_n}. "
            "The permutation is a hash-table iteration order with no closed form; "
            "supply the matching control as the library."
        )
    body = bytes(body)
    donor_index = donor_data[_FI.final_index_ref(donor_data).start :]
    index = _FI.compose_index(donor_data, body + donor_index, base_n, target_n, channels=channels)
    # Rewrite the first-block index-offset pointer to the real index start. For
    # N<=16 this equals what `_set_counter` produced (index < 0x20000, high bits
    # 0x0001); for >16 tracks the full 4-byte offset is required to load.
    return _set_index_offset(body + index)


def synthesize_stereo_session(
    donor_data: bytes,
    base_n: int,
    target_n: int,
    library_data: bytes,
    library_total: int,
) -> bytes:
    """Grow a `base_n`-track empty-stereo session to `target_n` tracks.
    PT-confirmed for 1..16. See `_synthesize_session`."""
    return _synthesize_session(donor_data, base_n, target_n, library_data, library_total, channels=2)


def synthesize_mono_session(
    donor_data: bytes,
    base_n: int,
    target_n: int,
    library_data: bytes,
    library_total: int,
) -> bytes:
    """Grow a `base_n`-track empty-MONO session to `target_n` tracks. Same pipeline
    as stereo with one 0x1052 audio lane per track and 0x1054 = N channels (not 2N).
    Donor + library must be uniform mono controls (e.g. `N mono tracks.ptx`)."""
    return _synthesize_session(donor_data, base_n, target_n, library_data, library_total, channels=1)


def _folder_leaf(data: bytes) -> str | None:
    """The session-folder name (last component of the embedded session path), read
    from the first 0x261c. Used to path-normalize tracks sourced from different
    control folders to one consistent leaf (else the body is a path 'chimera' that
    Pro Tools rejects).

    The session path is a run of consecutive length-prefixed ASCII components
    (`<u32 len><name>`...), e.g. `Dropbox SSD/Dropbox/.../control_files/mixed tracks`
    or `Macintosh HD/Users/.../mixed tracks` — volume-dependent, so we don't anchor
    on a fixed volume name. Pick the consecutive chain with the most total
    characters (the filesystem path beats coincidental short runs like the `0x2626`
    `&&` markers) and return its last component."""
    ptf = parse(data)
    blk = next((b for b in flat_blocks(ptf) if b.content_type == 0x261C), None)
    if blk is None:
        return None
    seg = data[blk.offset : blk.offset + blk.block_size]
    best_chain: list[str] = []
    best_chars = 0
    i = 0
    while i + 4 <= len(seg):
        chain: list[str] = []
        pos = i
        while pos + 4 <= len(seg):
            ln = int.from_bytes(seg[pos : pos + 4], "little")
            if not (1 <= ln <= 64) or pos + 4 + ln > len(seg):
                break
            comp = seg[pos + 4 : pos + 4 + ln]
            if not all(32 <= c < 127 for c in comp):
                break
            chain.append(comp.decode("latin-1"))
            pos += 4 + ln
        chars = sum(len(c) for c in chain)
        if len(chain) >= 2 and chars > best_chars:
            best_chain, best_chars = chain, chars
        i += 1
    return best_chain[-1] if best_chain else None


def _internal_channel_order(data: bytes) -> list[int]:
    """Channel count of each track in INTERNAL (lane) order: 1=mono, 2=stereo, 0=MIDI.
    Internal order is NOT display order when MIDI is present (a `multiple track types`
    control is internal [mono, MIDI, stereo] = [1, 0, 2] but displays [mono, stereo,
    MIDI]). Derived from the lane-0 0x251a blocks (one per track, in lane order) keyed
    to the 0x1052 audio-lane count per track name; a MIDI track has no 0x1052 -> 0."""
    ptf = parse(data)
    a51 = _by_type(ptf, 0x251a)
    base_n = len(a51) // 2
    ch_by_name: dict[str, int] = {}
    for b in _by_type(ptf, 0x1052):
        blk = data[b.offset - 7 : b.offset + b.block_size]
        m = _NAME_RE.search(blk)
        if m:
            ch_by_name[m.group().decode()] = ch_by_name.get(m.group().decode(), 0) + 1
    order: list[int] = []
    for b in a51[:base_n]:
        blk = data[b.offset - 7 : b.offset + b.block_size]
        m = _NAME_RE.search(blk)
        order.append(ch_by_name.get(m.group().decode(), 0) if m else 0)
    return order


def _lane_order_track_names(body: bytes, n: int) -> list[str]:
    """Per-track names in LANE order — the order `_fill_offsets` assigns track k
    (it counts lane-major 0x251a instances 1..N). Read each track's name from the
    first `n` 0x251a lane blocks' own bytes rather than assuming a naming scheme:
    a `multiple track types` donor names per-type ("Audio 1", "MIDI 1", "Audio 2"
    in lane order), so the index must resolve "MIDI 1" at lane position 2, not a
    global "MIDI 3". Lane-major layout is [N lane-0 blocks][N lane-1 blocks], so the
    first N carry one name each (track k's name)."""
    ptf = parse(body)
    lanes = _by_type(ptf, 0x251a)[:n]
    names: list[str] = []
    for b in lanes:
        blk = body[b.offset - 7 : b.offset + b.block_size]
        m = _NAME_RE.search(blk)
        names.append(m.group().decode() if m else f"Audio {len(names) + 1}")
    return names


def synthesize_mixed_session(
    specs: list[int],
    donor_data: bytes,
    mono_lib: tuple[bytes, int],
    stereo_lib: tuple[bytes, int],
    overview_order: list[int] | None = None,
    target_leaf: str | None = None,
    click_ref: tuple[bytes, bytes] | None = None,
    stereo_unit_fn: "callable | None" = None,
    mono_unit_fn: "callable | None" = None,
    midi_unit_fn: "callable | None" = None,
) -> bytes:
    """Synthesize a session with an arbitrary mix of mono (1ch), stereo (2ch), and
    MIDI (0ch) tracks, in the given INTERNAL (grow/lane) order — optionally with a
    Click track appended last.

    `specs`         per-track channel counts (1=mono, 2=stereo, 0=MIDI), in INTERNAL
                    (lane/grow) order. len == N. NOTE: internal order is NOT the same
                    as display order when MIDI is present (Pro Tools stores a separate
                    overview permutation); pass `overview_order` for a specific display.
    `donor_data`    a session whose first `base_n` tracks already match `specs` in
                    internal order (base_n = its track count, must be >= 2 — the 1->2
                    grow has no 0x2589 anchor). Audio-only: the four 2-track controls
                    (`2 mono`, `2 stereo`, `mono stereo`, `stereo mono`). Audio+MIDI:
                    a `multiple track types` control (internal order [mono, MIDI,
                    stereo] = [1, 0, 2]); MIDI cannot be grown into an audio-only body
                    (`grow_one_midi_track` needs the MIDI containers to already exist).
    `mono_lib`/`stereo_lib`  (data, total) uniform controls (>= N tracks) to source
                    each added track's unit from, by track index.
    `midi_unit_fn`  generates a MIDI track unit for internal position k (e.g.
                    `lambda k: synth_midi_unit(k, template)`). Required iff `specs`
                    contains a 0 beyond `base_n`; MIDI tracks are gated single-digit.
    `overview_order`  the N-track display-order permutation. The order is COSMETIC
                    (Pro Tools accepts any valid permutation of 0..N-1 — confirmed),
                    so it defaults to identity `[0..N-1]`; no matching control is
                    needed. Pass an explicit permutation only to mirror a specific
                    session's display order.
    `target_leaf`   session-folder name to normalize every track's embedded path to
                    (default: the donor's own leaf). Required-in-spirit when mixing
                    types, since mono/stereo units come from different folders.
    `click_ref`     (clean_audio_ctrl, click_audio_ctrl) — a control PAIR whose audio
                    layout matches `specs` (the 2nd = the 1st + a Click track). When
                    given, a Click track is appended LAST via `add_click` (the
                    structural diff-replay in `click_clone`). One click per session;
                    the click contributes 0 audio channels. None = no click.
                    The pair is needed for the same reason the audio synth needs a
                    library: the click's exact bytes are sourced from a real control.
                    PT-confirmed for the all-stereo case (1 audio + click); a mixed
                    (mono+stereo) click still needs a matching mixed click control to
                    validate (TODO).

    Returns full unxored bytes (ready for `writer.encrypt_session_data`). Channel
    indices are rewritten to the cumulative allocation; the index is composed with
    per-track channel counts; the path is normalized; the index-offset pointer is
    fixed. (Overview scroll-extent is set to 0 — correct for N <= 9.)"""
    n = len(specs)
    mono_data, mono_total = mono_lib
    stereo_data, stereo_total = stereo_lib
    if overview_order is None:
        overview_order = list(range(n))  # cosmetic: any valid permutation loads
    if sorted(overview_order) != list(range(n)):
        raise ValueError("overview_order must be a permutation of 0..N-1")
    base_n = len(track_types(donor_data))
    if base_n < 2:
        raise ValueError("donor must have >= 2 tracks (the 1->2 grow has no 0x2589 anchor)")

    body = donor_data[: _FI.final_index_ref(donor_data).start]
    for k in range(base_n + 1, n + 1):
        ch = specs[k - 1]
        if ch == 0:
            # MIDI: its own block set, 0 channels. Must be FIRST + `continue` — the audio
            # `else` below would otherwise grow a 0-channel spec as a stereo track. No
            # 0x1014 channel-map fixup (MIDI has no 0x1014); grow_one_midi_track does the
            # lane/ordinal/name bookkeeping. base_n=k-1 is the CURRENT track count so the
            # lane-major 0x251a insert lands at the right boundary (see grow_one_midi_track).
            if midi_unit_fn is None:
                raise ValueError(f"specs has a MIDI track (0) at internal position {k} "
                                 f"but no midi_unit_fn was given")
            body = grow_one_midi_track(body, k - 1, midi_unit_fn(k))
            continue
        if ch == 2 and stereo_unit_fn is not None:
            unit = stereo_unit_fn(k)  # library-free: generate the unit (see synth_stereo_unit)
        elif ch == 1 and mono_unit_fn is not None:
            unit = mono_unit_fn(k)    # library-free mono (see synth_mono_unit)
        else:
            lib, total = (mono_data, mono_total) if ch == 1 else (stereo_data, stereo_total)
            unit = extract_track(lib, k, total, channels=ch)
        body = grow_one_track(body, k - 1, unit)
        # Rewrite the just-added track's channel map to the cumulative allocation
        # (its unit carries the source control's channel numbers). MIDI's 0 contributes
        # nothing to `base`, so audio channel allocation stays contiguous across MIDI.
        base = sum(specs[: k - 1])
        new_1014 = _by_type(parse(body), 0x1014)[-1]
        body = set_track_channels(body, new_1014.offset, list(range(base, base + ch)))

    # Transplant the 0x2519 name table from a uniform source that has exactly N
    # tracks (names are "Audio 1..N" regardless of type). The grown table's last
    # entry has a first-child-boundary quirk (reads 2B short); transplanting a real
    # N-entry table reproduces it exactly, as the uniform synthesizer does.
    transplanted = False
    for src_data, src_total in ((stereo_data, stereo_total), (mono_data, mono_total)):
        if src_total == n:
            body = _set_name_table(body, src_data)
            transplanted = True
            break
    if not transplanted:
        # No matching n-track control to transplant from: the grown 0x2519 table strands the
        # donor's SPECIAL last entry mid-table (the rest stay NORMAL), which Pro Tools reads
        # past -> "end of stream encountered". Rebuild it as [header][n-1 normal][1 special].
        body = _fix_name_table_special_last(body, n)

    body = bytearray(body)
    # The overview display-order anchor (0x2587 cache) exists once per AUDIO track only —
    # MIDI tracks carry no anchor. So with MIDI present the anchor count is #audio, not N,
    # and `_set_overview_order(body, [0..N-1])` would mismatch. The order is cosmetic (PT
    # rebuilds the 0x2587 cache), so set an identity permutation over exactly the anchors
    # that exist; audio-only keeps the caller's `overview_order` unchanged.
    n_anchors = len(_overview_order_offsets(bytes(body)))
    _set_overview_order(body, overview_order if n_anchors == n else list(range(n_anchors)))
    _set_overview_extent(body, 0)  # TODO: non-zero threshold once tracks overflow the window (N>9)
    _set_track_selection(body, None)
    body = bytearray(_clear_donor_window_markers(bytes(body), base_n))
    body = bytes(body)

    donor_index = donor_data[_FI.final_index_ref(donor_data).start :]
    midi_positions = {k for k in range(1, n + 1) if specs[k - 1] == 0}
    if midi_positions:
        # MIDI tracks need name-keyed index resolution (the 0x2519 table/parent reference
        # each track's 0x251a lanes by name). Derive the per-track names in lane order
        # straight from the body so they match exactly — the donor uses PER-TYPE naming
        # (e.g. "MIDI 1" at internal position 2), which assuming a global scheme would miss.
        track_names = _lane_order_track_names(body, n)
        index = _FI.compose_index(donor_data, body + donor_index, base_n, n,
                                  channels=specs, track_names=track_names,
                                  midi_tracks=midi_positions)
    else:
        index = _FI.compose_index(donor_data, body + donor_index, base_n, n, channels=specs)
    out = _set_index_offset(body + index)

    # Path-normalize the ADDED tracks' source-folder leaf -> the donor's leaf, so
    # mono+stereo tracks (sourced from different control folders) share one path and
    # don't form a chimera. Rename ONLY the leaves of the source folders actually
    # used for added tracks -- NOT every leaf in the body: the donor can carry stale
    # template-remnant path refs (e.g. in 0x2067) whose own path-wrapper length
    # fields rename_track doesn't fix, and those refs are harmless if left alone
    # (the donor loads with them). `target` defaults to the donor's own leaf; a
    # different `target_leaf` would also need the donor's tracks renamed (TODO: the
    # 0x2067 wrapper handling for arbitrary user-chosen session folders).
    target = target_leaf or _folder_leaf(donor_data)
    added = specs[base_n:]
    source_leaves: set[str | None] = set()
    if any(c == 1 for c in added):
        source_leaves.add(_folder_leaf(mono_data))
    if any(c == 2 for c in added):
        source_leaves.add(_folder_leaf(stereo_data))
    for leaf in source_leaves:
        if leaf and target and leaf != target and track_name_occurrences(out, leaf):
            out = rename_track(out, leaf, target)

    # Append the click track LAST (one per session) via the structural diff-replay.
    # Done after the audio session is fully assembled + path-normalized so the click
    # splices onto the final audio body. The click adds 0 channels (0x1015/0x1054 stay).
    if click_ref is not None:
        out = add_click(out, click_ref[0], click_ref[1])
        # Re-set the overview display-order over ALL N+1 entries (audio + click). The
        # audio overview was set to `overview_order` BEFORE the click existed; the click
        # diff (sourced from its control pair) then overwrites some audio entries and
        # appends the click's, which can leave a duplicate (an invalid permutation, e.g.
        # [0,0,2]). Overwrite the full sequence with `overview_order + [n]` (click shown
        # last) -- a valid permutation; order is COSMETIC (any valid permutation loads).
        # Positional u16 overwrite (no size change), so the index is untouched; operate
        # on the body slice only in case the anchor coincidentally appears in the index.
        cref = _FI.final_index_ref(out)
        cb, cidx = bytearray(out[: cref.start]), out[cref.start :]
        _set_overview_order(cb, list(overview_order) + [n])
        out = bytes(cb) + cidx
    return out


# Upper bound for the incremental-grow inline synthesizer. At 419 tracks the track
# number / element-id fields legitimately equal 0x...5A (the block magic), and the
# construction toolchain locates blocks by scanning for that magic, so the re-parse
# mis-reads phantom blocks. PT itself is fine (it uses the TOC's absolute offsets);
# fixing >418 needs a TOC-driven construction (build the index from tracked positions,
# never scan-parse the large body) -- deferred. 418 covers every realistic session.
_STEREO_INLINE_MAX = 418


def synthesize_stereo_inline(n: int, *, scaffold: "bytes | None" = None,
                             unique: bool = True) -> bytes:
    """Build a clean `n`-stereo session with NO external control files.

    Uses the inlined 8-track scaffold + the three inlined 512-control unit templates
    (`_stereo_data`, for 1/2/3-digit names), generating tracks `base_n+1..n` via
    `synth_stereo_unit`. This eliminates BOTH the per-N stereo library (formerly a
    1.4 MB 512-track control) and the donor file. Valid range: `base_n` (8) <= n <=
    `_STEREO_INLINE_MAX` (418); for n < 8 use a bundled small control, and n > 418
    needs the TOC-driven construction (see `_STEREO_INLINE_MAX`). Returns full unxored
    bytes (ready for `writer.encrypt_session_data`). PT-confirmed for n=12 and n=30;
    n up to 418 is structurally validated (parses to N) pending PT confirmation."""
    from . import _stereo_data as _SD
    if scaffold is None:
        scaffold = _SD.stereo_scaffold()
    base_n = len(track_types(scaffold))
    if n < base_n:
        raise ValueError(
            f"synthesize_stereo_inline needs n >= {base_n} (the inlined scaffold's track "
            f"count); got n={n}. The 1->{base_n} direction has no grow path; use a bundled "
            f"<n> stereo tracks.ptx control for n < {base_n}.")
    if n > _STEREO_INLINE_MAX:
        raise ValueError(
            f"synthesize_stereo_inline supports n <= {_STEREO_INLINE_MAX}; got n={n}. Beyond "
            f"that the construction's block scan mis-reads 0x5A-in-data phantoms (PT itself "
            f"is fine); a TOC-driven builder is needed. {_STEREO_INLINE_MAX} covers every "
            f"realistic session.")
    t1, t2, t3 = _SD.stereo_templates()
    # stereo_lib here serves ONLY as the path-leaf source + a non-matching total (so the
    # name table is rebuilt, not transplanted); the units come from `stereo_unit_fn`.
    out = synthesize_mixed_session(
        [2] * n, scaffold, (b"", 0), (scaffold, n + 1),
        stereo_unit_fn=lambda k: synth_stereo_unit(k, t1, t2, t3, unique=unique),
    )
    return set_waveform_view(out)  # force every track's edit-window view to waveform


def synthesize_mono_inline(n: int, *, scaffold: "bytes | None" = None,
                           unique: bool = True) -> bytes:
    """Build a clean `n`-mono session with NO external control files — the mono counterpart
    of `synthesize_stereo_inline`. Inlines an 8-track mono scaffold + the 512-mono unit
    templates (`_mono_data`) and generates tracks via `synth_mono_unit`. Range: 8 <= n <=
    `_STEREO_INLINE_MAX`. Because the scaffold and templates come from different folders, the
    generated units' embedded folder leaf is normalized to the scaffold's (no path chimera)."""
    from . import _mono_data as _MD
    if scaffold is None:
        scaffold = _MD.mono_scaffold()
    base_n = len(track_types(scaffold))
    if n < base_n:
        raise ValueError(f"synthesize_mono_inline needs n >= {base_n} (the inlined scaffold's "
                         f"track count); got n={n}. Use a bundled control for n < {base_n}.")
    if n > _STEREO_INLINE_MAX:
        raise ValueError(f"synthesize_mono_inline supports n <= {_STEREO_INLINE_MAX} (the "
                         f"0x5A-in-data scan limit); got n={n}.")
    t1, t2, t3 = _MD.mono_templates()
    out = synthesize_mixed_session(
        [1] * n, scaffold, (scaffold, n + 1), (b"", 0),
        mono_unit_fn=lambda k: synth_mono_unit(k, t1, t2, t3, unique=unique),
    )
    # The generated units carry the 512-mono templates' folder leaf; normalize it to the
    # scaffold's leaf so the session is not a path chimera Pro Tools would reject.
    tl, sl = _MD.TEMPLATE_LEAF, _folder_leaf(scaffold)
    if tl and sl and tl != sl and track_name_occurrences(out, tl):
        out = rename_track(out, tl, sl)
    return set_waveform_view(out)


def synthesize_mixed_inline(spec: "list[int]", *, unique: bool = True) -> bytes:
    """Build a clean mono+stereo session in ARBITRARY ORDER with NO external control files.

    `spec` = per-track channel counts in display order (1 = mono, 2 = stereo). Picks a 2-track
    start-pair donor matching `spec[:2]` (the grow anchor) and generates every remaining track
    from the inlined stereo/mono unit templates via `synth_stereo_unit`/`synth_mono_unit`. The
    cumulative channel allocation + index are handled by `synthesize_mixed_session`; both
    template folder leaves are normalized to the donor's. Range: 2 <= len(spec) <=
    `_STEREO_INLINE_MAX`. (Click + MIDI are added by the higher-level `synthesize`.)"""
    from . import _stereo_data as _SD, _mono_data as _MD, _mixed_data as _XD
    n = len(spec)
    if n < 2:
        raise ValueError(f"synthesize_mixed_inline needs >= 2 tracks (the grow anchor); got {n}.")
    if any(c not in (1, 2) for c in spec):
        raise ValueError("spec entries must be 1 (mono) or 2 (stereo).")
    if n > _STEREO_INLINE_MAX:
        raise ValueError(f"synthesize_mixed_inline supports <= {_STEREO_INLINE_MAX} tracks "
                         f"(the 0x5A-in-data scan limit); got {n}.")
    pair = (spec[0], spec[1])
    if pair not in _XD.start_pairs():
        raise ValueError(f"no inlined donor for start pair {pair}; need one of {_XD.start_pairs()}.")
    donor = _XD.mixed_donor(*pair)
    st = _SD.stereo_templates()
    mt = _MD.mono_templates()
    out = synthesize_mixed_session(
        spec, donor, (donor, n + 1), (donor, n + 1),
        mono_unit_fn=lambda k: synth_mono_unit(k, *mt, unique=unique),
        stereo_unit_fn=lambda k: synth_stereo_unit(k, *st, unique=unique),
    )
    dl = _folder_leaf(donor)  # normalize BOTH template leaves to the donor's
    for tl in (_XD.STEREO_TEMPLATE_LEAF, _XD.MONO_TEMPLATE_LEAF):
        if tl and dl and tl != dl and track_name_occurrences(out, tl):
            out = rename_track(out, tl, dl)
    return set_waveform_view(out)


_TYPE_NAME_PREFIX = {"mono": "Audio", "stereo": "Audio", "midi": "MIDI", "click": "Click"}


def _renumber_tracks_per_type(data: bytes) -> bytes:
    """Renumber tracks to clean per-type counters (Audio 1..A, MIDI 1..M) so grown tracks don't
    show internal-position gaps (e.g. the global-position naming leaves Audio 1,2,4,6 / MIDI 1,5).
    Numbers each type 1..k in `track_types` order (audio-grouped, internal order within a type),
    which renders as gap-free top-to-bottom numbering. Collision-free in that order: the i-th
    track of a type targets "<T> i", and any track currently holding that name has a lower rank
    and was already renamed, so `set_track_names`' sequential `rename_track` never duplicates."""
    tts = track_types(data)
    counts: dict[str, int] = {}
    targets: list[str | None] = []
    for t in tts:
        pre = _TYPE_NAME_PREFIX.get(t.kind)
        if pre is None:
            targets.append(None)
            continue
        counts[pre] = counts.get(pre, 0) + 1
        targets.append(f"{pre} {counts[pre]}")
    return set_track_names(data, targets)


def _sync_kind_track_counts(data: bytes) -> bytes:
    """Set `0x1015` to the session's AUDIO track count and `0x1058` to its MIDI count, reading
    each from the children the container actually frames. Size-neutral and idempotent.

    Safe to RECOMPUTE (unlike the editors, which adjust by delta) because both conventions are
    now pinned: `0x1015` == the number of `0x1014` entries and `0x1058` == the number of `0x1057`
    entries, with ZERO mismatches across 162 PT-authored sessions. Bug #17 forbade recomputing
    only while the convention was unknown; the mixed-MIDI SYNTH has been writing `0x1015` as the
    TOTAL track count and `0x1058` off by one ever since, which is what the gate's
    `container-count` note localised on 13 of our own files.

    Pro Tools tolerates the stale values -- `mixedmidi_click_*` opened with them (2026-06-20) --
    so this is conformance, not a crash fix."""
    bl = _size_driven_blocks(data)
    out = bytearray(data)
    for cont, child in ((0x1015, 0x1014), (0x1058, 0x1057)):
        for z, e, c in bl:
            if c != cont or z < 31:
                continue
            n = sum(1 for zz, _ee, cc in bl if z < zz < e and cc == child)
            if n:                                   # only the instance that frames children
                out[z + 9 : z + 13] = n.to_bytes(4, "little")
                break
    return bytes(out)


def _repair_view_chain_ordinals(data: bytes) -> bytes:
    """Make the `0x2589` display-row ordinals a permutation of `range(name entries)`.

    Every PT-authored session satisfies that (one chain per display row); the mixed-MIDI synth
    emits a COLLISION -- `[1, 0, 1]` for three entries, `[1, 0, 1, 2, 3, 4]` for six -- because
    `grow_one_midi_track` appends a COPY of an existing chain without restamping its ordinal.

    Minimal repair, the same shape that worked for the 0x104F clip-slot ids: reserve every ordinal
    already present, then move only the SECOND and later occurrences of a repeat to the lowest
    value still missing from the range. Assignments that are already correct are never touched, so
    a session whose chains are fine is returned unchanged. Size-neutral.

    Chain->track attribution is deliberately NOT inferred here: file order is not display order,
    so nothing in the synth output says which track a colliding chain belongs to. This only
    guarantees the invariant PT files satisfy; Pro Tools recomputes view state on load, and it
    ACCEPTED these files with the collision (2026-06-20), so this is conformance."""
    bl = _size_driven_blocks(data)
    nt = [(z, e) for z, e, c in bl if c == 0x2519]
    chains = [z for z, _e, c in bl if c == 0x2589]
    if not nt or not chains:
        return data
    n = len(_inline_name_entries(data, nt[0][0], nt[0][1], bl))
    vals = [int.from_bytes(data[z + 9 : z + 11], "little") for z in chains]
    if sorted(vals) == list(range(n)):
        return data                                   # already a clean permutation
    out = bytearray(data)
    used, seen = set(vals), set()
    free = [v for v in range(n) if v not in used]
    for z, v in zip(chains, vals):
        if v in seen or v >= n:
            if not free:
                break
            nv = free.pop(0)
            used.add(nv)
            out[z + 9 : z + 11] = nv.to_bytes(2, "little")
        else:
            seen.add(v)
    return bytes(out)


def synthesize_mixed_midi(
    spec: "list[int]",
    donor_data: bytes,
    midi_template: dict,
    *,
    overview_order: "list[int] | None" = None,
    unique: bool = True,
) -> bytes:
    """Build a mono+stereo+MIDI session in INTERNAL (grow/lane) order from a multi-type donor.

    `spec`          per-track channel counts in INTERNAL order (1=mono, 2=stereo, 0=MIDI).
                    `spec[:base_n]` MUST match the donor's internal track types (MIDI can't be
                    grown into an audio-only body — `grow_one_midi_track` needs the donor's MIDI
                    containers to already exist). Internal order differs from display order when
                    MIDI is present; the display permutation is cosmetic (PT rebuilds it).
    `donor_data`    a `multiple track types` control (internal order [mono, MIDI, stereo] =
                    [1, 0, 2], base_n=3) carrying both audio AND MIDI infrastructure.
    `midi_template` an `extract_midi_unit(...)` dict from a uniform MIDI control (e.g.
                    `8 MIDI tracks.ptx`); each grown MIDI track is re-keyed via `synth_midi_unit`
                    (single-digit gated, so a grown MIDI track must sit at internal position <=9).

    Audio units come from the inlined `_stereo_data`/`_mono_data` templates; their folder leaves
    are normalized to the donor's (MIDI units carry no path, so no MIDI leaf to fix). Returns full
    unxored bytes (ready for `writer.encrypt_session_data`)."""
    from . import _stereo_data as _SD, _mono_data as _MD, _mixed_data as _XD
    n = len(spec)
    base_n = len(track_types(donor_data))
    donor_internal = _internal_channel_order(donor_data)
    if spec[:base_n] != donor_internal:
        raise ValueError(f"spec[:{base_n}] must match the donor's internal order "
                         f"{donor_internal} (MIDI can't be grown from scratch); got {spec[:base_n]}")
    midi_beyond = [k for k in range(base_n + 1, n + 1) if spec[k - 1] == 0]
    if any(k > 9 for k in midi_beyond):
        raise ValueError(f"grown MIDI tracks must sit at internal position <= 9 (single-digit "
                         f"gate); got positions {midi_beyond}")
    st = _SD.stereo_templates()
    mt = _MD.mono_templates()
    out = synthesize_mixed_session(
        spec, donor_data, (donor_data, n + 1), (donor_data, n + 1),
        overview_order=overview_order,
        stereo_unit_fn=lambda k: synth_stereo_unit(k, *st, unique=unique),
        mono_unit_fn=lambda k: synth_mono_unit(k, *mt, unique=unique),
        midi_unit_fn=lambda k: synth_midi_unit(k, midi_template, unique=unique),
    )
    dl = _folder_leaf(donor_data)  # normalize the audio template leaves to the donor's
    for tl in (_XD.STEREO_TEMPLATE_LEAF, _XD.MONO_TEMPLATE_LEAF):
        if tl and dl and tl != dl and track_name_occurrences(out, tl):
            out = rename_track(out, tl, dl)
    # Grown tracks are named by internal position (gaps: Audio 1,2,4,6 / MIDI 1,5); renumber to
    # clean per-type counters (Audio 1..A / MIDI 1..M) as a hand-built PT session would read.
    out = _renumber_tracks_per_type(out)
    out = _sync_kind_track_counts(out)   # 0x1015 = audio count, 0x1058 = MIDI count
    out = _repair_view_chain_ordinals(out)   # one 0x2589 chain per display row, no collisions
    # 0x261C "C4" is the AUDIO-ONLY display index (43/43 discriminating PT sessions). The mixed
    # synth left gaps ([0,1,3,4,5]) by numbering against the full track list.
    _b = bytearray(out)
    for _s, (_z, _e) in enumerate(_blocks_by_type(out).get(0x261C, [])):
        _b[_e - _B261C_C4_FROM_END : _e - _B261C_C4_FROM_END + 4] = _s.to_bytes(4, "little")
    out = bytes(_b)
    # Every GROWN MIDI track left its predecessor's 0x2519 `next_kind` still tagged audio, so a
    # spec like [1,0,2,0] emitted `Audio 2 -> MIDI 2` as 0x0000. Repaired here, in the builder both
    # the inline and control-file donor paths share, so all three routes stay byte-identical.
    out = _reallocate_duplicate_track_ids(_repair_next_kind_chain(out))
    # ...and put the playlist ordinals in BLOCK order. They were assigned in CREATION order, so a
    # spec like [1,0,2,0] emitted [1,2,4,3] -- the grown MIDI took 4 while the stereo kept 3. Pro
    # Tools never writes that: the value is the index of the track's FIRST PLAYLIST, so the column
    # is a running total and strictly increasing by construction. No-op when already correct.
    return _normalize_one_playlist_ordinals(set_waveform_view(out))


def synthesize_mixed_midi_inline(spec: "list[int]", *, unique: bool = True) -> bytes:
    """Build a mono+stereo+MIDI session with NO external control files — the MIDI counterpart of
    `synthesize_mixed_inline`. Inlines the `multiple track types` donor + the MIDI unit template
    (`_mixed_midi_data`) and delegates to `synthesize_mixed_midi`. `spec` is INTERNAL order and
    `spec[:3]` must be `[1, 0, 2]` (the donor's internal [mono, MIDI, stereo]); grown MIDI tracks
    must sit at internal position <= 9 (single-digit gate). Display order is cosmetic."""
    from . import _mixed_midi_data as _MM
    return synthesize_mixed_midi(spec, _MM.mixed_midi_donor(), _MM.midi_template(), unique=unique)


_NAME_ENTRY_KIND = {0x261C: 0, 0x2620: 1, 0x261E: 2, 0x263B: 5, 0x2621: 7, 0x261D: 11}


def _repair_next_kind_chain(data: bytes) -> bytes:
    """Rewrite every `0x2519` entry's `next_kind` from the ACTUAL kind of the entry after it.

    `next_kind` names the NEXT entry's kind `<< 8` (verified 3379/3379 consecutive pairs over 40
    real sessions). The synthesizer got this right only for tracks it took from the donor template:
    each GROWN MIDI track left its predecessor still tagged `0x0000` (audio), so `[1,0,2,0]` emitted
    `Audio 2 -> MIDI 2` as audio. The defect count equalled the number of MIDI tracks beyond the
    first, which is exactly what the ten broken control files show (1, 2 and 3 violations for the
    sessions with 2, 3 and 4 MIDI tracks).

    Kinds are read from each entry's GUID -> owning track record, so this is derived from the
    session that was actually built rather than from the spec, and it is a no-op on a session that
    is already correct -- including every genuine Pro Tools file.

    The last entry has the SHORT 21-byte trailer and simply has no `next_kind`; so does the odd
    non-final short entry seen in at least one PT session. Both are detected by measuring the stride
    to the next entry and skipped, because writing at +20 there would land on the following entry's
    length prefix.
    """
    bl = _size_driven_blocks(data)
    nt = [(z, e) for z, e, c in bl if c == 0x2519]
    if not nt:
        return data
    z, e = nt[0]
    end = min((cz for cz, _ce, _cc in bl if z < cz < e), default=e)
    ents = _inline_name_entries(data, z, e, bl)
    slots = _name_entry_ordinal_slots(data, z, end)
    if len(slots) < 2 or len(ents) != len(slots):
        return data
    recs = [(zz, ee, _NAME_ENTRY_KIND[cc]) for zz, ee, cc in bl if cc in _NAME_ENTRY_KIND]
    out = bytearray(data)
    for i in range(len(slots) - 1):
        if (ents[i + 1][1] - 4) - slots[i] != _NAME_ENTRY_SUFFIX:   # short trailer: no next_kind
            continue
        g = data[slots[i + 1] + 10 : slots[i + 1] + 18]     # the NEXT entry's identity GUID
        kind = next((k for zz, ee, k in recs if g in data[zz:ee]), None)
        if kind is None:
            continue
        out[slots[i] + 20 : slots[i] + 22] = (kind << 8).to_bytes(2, "little")
    return bytes(out)


def synthesize(spec: "list[int]", *, click: "str | None" = None,
               display_order: "list[int] | None" = None, unique: bool = True) -> bytes:
    """Build a Pro Tools session from an ordered track spec with NO external control files.

    `spec` = per-track channel counts (1 = mono, 2 = stereo, 0 = MIDI). A spec containing MIDI (0)
    is built in INTERNAL order and must start `[1, 0, 2]` (the inlined multi-type donor's [mono,
    MIDI, stereo]) with grown MIDI at internal position <= 9.
    `display_order` sets the edit-window track order independently of build order: a permutation of
    0..N-1 over the built tracks in spec/creation order (e.g. `synthesize([1, 0, 2], display_order=
    [2, 0, 1])` builds mono/MIDI/stereo then DISPLAYS stereo, mono, MIDI). Index-only — Pro Tools'
    loader orders the edit window by the master track-list, not body order, so this is how MIDI can
    sit anywhere in the display. None = display in build order.
    `click` adds a single Click track: None, 'top', or 'bottom' — works with audio AND MIDI. For a
    click at an arbitrary position, add it then pass `display_order` over the N+1 resulting tracks
    (the click is the last build index). Range 2..418 tracks. Returns full unxored bytes."""
    n = len(spec)
    has_midi = any(c == 0 for c in spec)
    if all(c == 2 for c in spec) and n >= 8:
        body = synthesize_stereo_inline(n, unique=unique)
    elif all(c == 1 for c in spec) and n >= 8:
        body = synthesize_mono_inline(n, unique=unique)
    elif has_midi:
        body = synthesize_mixed_midi_inline(spec, unique=unique)
    else:
        body = synthesize_mixed_inline(spec, unique=unique)
    if click is not None:
        if click not in ("top", "bottom"):
            raise ValueError("click must be None, 'top', or 'bottom'.")
        from . import _click_data as _CD
        clean, clk = _CD.click_pair()
        # The click splice can leave a track in volume view; force waveform LAST (the docstring's
        # "run this last" rule). body was already waveform from the inline synthesizer.
        body = set_waveform_view(add_click_anyN(body, clean, clk, at_top=(click == "top")))
    if display_order is not None:
        # Index-only edit-window reorder (no body change). `display_order` indexes the build/spec
        # order; translate to reorder_tracks' body-offset index space (they differ once MIDI is
        # present, since 0x2620 playlists cluster in the body away from spec order).
        cur = _container_display_offsets(body)               # playlist offsets in build/spec order
        tpo = {o: i for i, (o, _t) in enumerate(track_playlist_order(body))}
        body = reorder_tracks(body, [tpo[cur[d]] for d in display_order])
    # Two identity repairs, both of which the donor-cloning synthesis violates:
    #   * grown MIDI left its predecessor's next_kind mis-tagged;
    #   * a cloned track (notably the CLICK, cloned from a donor) inherited the donor's 10
    #     session-unique ids, which is what made Pro Tools treat two tracks as one object.
    # ...and put the playlist ordinals in BLOCK order. The CLICK path clones a donor click track
    # and inherits its ordinal, so `synthesize([2]*4, click='top')` emitted [3,1,2,3,4] -- a
    # REPEAT, which is what Pro Tools crashes on (PT-confirmed 2026-07-27). Every click-bearing
    # session this writer produced carried one. No-op where the column is already correct.
    return _normalize_one_playlist_ordinals(
        _reallocate_duplicate_track_ids(_repair_next_kind_chain(body)))


# --- index-offset pointer ----------------------------------------------------

def _set_index_offset(data: bytes) -> bytes:
    """The first top-level block (a 4-byte payload) stores the ABSOLUTE offset of
    the trailing 0x0002 master index. Any edit that changes the body size moves
    the index, so this pointer must be rewritten or Pro Tools seeks to the wrong
    place and reports "magic ID does not match". (The value I previously treated
    as a per-N 'counter content_type' is just the low 16 bits of this offset.)"""
    ref = _FI.final_index_ref(data)
    if ref is None:
        return data
    # The first top-level block is the lowest-offset block; find it via the fast
    # size-driven walk instead of the (much slower) scan parser. `Block.offset`
    # equals `zmark + 7`, so the first block's offset is `min(zmark) + 7`.
    bounds = _raw_block_bounds(data, ref.start)
    first_offset = min(z for z, _e, _c in bounds) + 7
    out = bytearray(data)
    out[first_offset : first_offset + 4] = int(ref.start).to_bytes(4, "little")
    return bytes(out)


# --- in-place clip editing on arbitrary existing sessions --------------------
#
# Third-party sessions defeat the 0x5A-scan block parser (`parse`): it both
# undercounts (misses real blocks whose payload never produced a scannable magic)
# and invents phantoms (data bytes that look like 0x5A | small-type). The master
# index then fails to frame. The fix is to enumerate blocks by their DECLARED
# SIZE -- walk each block to `zmark + 7 + size` and recurse -- since the block
# sizes make the stream self-describing. With size-driven zmarks every 0x0002 hole
# resolves on real sessions, so we can edit + reindex them in place.

def _size_driven_blocks(data: bytes) -> "list[tuple[int, int, int]]":
    """Authoritative block enumeration by declared size. Returns sorted
    `[(zmark, end, content_type)]` covering the body (everything before the
    trailing 0x0002 index). Use this -- not `flat_blocks` -- on arbitrary
    third-party sessions: it recovers the blocks the scan parser misses."""
    ref = _FI.final_index_ref(data)
    return sorted(_raw_block_bounds(data, ref.start))


def _index_offset_holes(data: bytes) -> "tuple[list[tuple[int, int]], set[int]]":
    """Every offset hole in the 0x0002 master index as `(abs_file_pos, target)`,
    plus the authoritative zmark set. Frames the index with size-driven zmarks so
    it parses real sessions (scan-parser zmarks leave child-refs unresolved)."""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    zmarks = {z for z, _, _ in bl}
    recs = _FI.parse_records(ref.data, {c for _, _, c in bl}, zmarks)
    holes: "list[tuple[int, int]]" = []
    for r in recs:
        for c in r.child_refs:                       # offset at record-rel rel+2
            holes.append((ref.start + r.start + c.rel + 2, c.offset))
        for e in r.elements:                         # offsets at record-rel rel+5+4i
            for i, o in enumerate(e.offsets):
                holes.append((ref.start + r.start + e.rel + 5 + 4 * i, o))
    return holes, zmarks


def playlist_names(data: bytes) -> "list[str]":
    """Every playlist in the session, in `0x210B` file order -- the order the per-track `0x2015`
    ordinal indexes into.

    The playlists live in ONE flat `0x2107` container (126/126 on the reference session), NOT nested
    per track, so a playlist's owner is established by POSITION, not containment. Each `0x210B`
    holds its name as the first length-prefixed ASCII string in the block; the name is located by
    scanning for that shape rather than at an offset, because `0x210B` spans vary (54-68 bytes on
    the same session, with the length driven by the name).
    """
    out: "list[str]" = []
    for z, e, c in _size_driven_blocks(data):
        if c != 0x210B:
            continue
        blk = data[z:e]
        name = ""
        for k in range(len(blk) - 4):
            n = int.from_bytes(blk[k : k + 4], "little")
            if 1 <= n <= 64 and k + 4 + n <= len(blk):
                s = blk[k + 4 : k + 4 + n]
                if all(32 <= b < 127 for b in s):
                    name = s.decode("latin1")
                    break
        out.append(name)
    return out


def track_playlists(data: bytes) -> "list[list[str]]":
    """The playlists owned by each track, in track order -- `[[name, ...], ...]`.

    HOW IT IS DERIVED. Each track's `0x2015` carries a `u32` (at the `_ORD2015_ANCHOR` + 18) that is
    the 1-BASED INDEX OF THAT TRACK'S FIRST PLAYLIST in the flat `0x210B` list, so track `i` owns
    the half-open slice `[ord[i], ord[i+1])` and the last track owns the rest.

    PROVEN BY NAME, not just by arithmetic. On `SANTA CLAUS IS COMIN.bak.011.ptx` (36 tracks, 126
    playlists) the ordinals are `[1, 2, 12, 22, ...]`, so track 1 -- displayed as `BD in.04`, Pro
    Tools' `trackname.NN` convention for "playlist NN is selected" -- claims indices 2..11, and those
    ten `0x210B` entries read:

        BD in.04, BD in.07, BD in.06, BD in.05, BD in.08, BD in.01, BD in, BD in.02, BD in.03,
        gutter1

    i.e. every playlist of `BD in` and nothing else. The arithmetic check agrees independently:
    `sum(gaps) + 1 == 126 ==` the `0x210B` count exactly.

    On a session where every track owns one playlist -- which is EVERY session in `control_files`
    and `/Volumes/Samples` -- this degenerates to one name per track, which is why the field read as
    a plain positional counter for so long.
    """
    names = playlist_names(data)
    if not names:
        return []
    ords: "list[int]" = []
    for z, e, c in _size_driven_blocks(data):
        if c != 0x2015:
            continue
        a = data.find(_ORD2015_ANCHOR, z, e)
        if a >= 0 and a + 22 <= e:
            ords.append(int.from_bytes(data[a + 18 : a + 22], "little"))
    if not ords or ords[0] != 1 or any(b <= a for a, b in zip(ords, ords[1:])):
        return []                       # not the convention -- say nothing rather than guess
    bounds = ords + [len(names) + 1]
    return [names[bounds[i] - 1 : bounds[i + 1] - 1] for i in range(len(ords))]


def session_self_name(data: bytes) -> "str | None":
    """The `.ptx` filename the session records for ITSELF, from its `0x2067` save-lineage block.

    Read as a length-prefixed string (`u32 len` immediately before the bytes) so it does not depend
    on an offset -- `0x2067` has over a hundred distinct sizes across the corpus.

    WHY THIS MATTERS: a donor-transplanted session keeps the DONOR's name here, so comparing this
    against the name on disk separates files Pro Tools wrote from files THIS PROJECT wrote. That
    partition was badly needed: of the 282 sessions in `control_files`, which the whole project has
    treated as a Pro Tools oracle, **94 disagree** -- 85 of them naming a donor that is still in the
    corpus, plus 5 more naming a donor that is not. Roughly a third of the "PT-authored" control set
    is our own output, and any statistic quoted as "n/n on PT-authored controls" that did not
    exclude them is partly circular.

    It is NOT a pure provenance test on its own: a genuine Pro Tools session renamed on disk, or
    produced by Save As, also disagrees. Four of the 94 are exactly that (`2 stereo plus click on
    top.ptx` recording `2 stereo click on top.ptx`). Use `session_is_probably_ours` for the sharper
    test, which additionally requires the recorded donor to exist in the same directory tree.
    """
    for z, e, c in _size_driven_blocks(data):
        if c != 0x2067:
            continue
        blk = data[z:e]
        k = 0
        while True:
            k = blk.find(b".ptx", k)
            if k < 0:
                return None
            for ln in range(4, 120):
                st = k + 4 - ln
                if st < 4:
                    continue
                if int.from_bytes(blk[st - 4 : st], "little") != ln:
                    continue
                s = blk[st : st + ln]
                if s.endswith(b".ptx") and all(32 <= b < 127 for b in s):
                    return s.decode("latin1")
            k += 1
    return None


def session_is_probably_ours(data: bytes, path, siblings=None) -> bool:
    """True when this file looks like OUR donor-transplant output rather than a Pro Tools save.

    The test: the name in `0x2067` differs from the file's own name AND names a file that exists
    alongside it -- i.e. the donor is right there. Requiring the donor to be present is what keeps a
    merely-renamed Pro Tools session from being misread as ours; on `control_files` that separates
    85 transplants from 4 renames, where the name check alone flags all 89 together.

    `siblings` is the set of filenames to consider (defaults to every `.ptx` beside `path`).
    """
    import pathlib
    p = pathlib.Path(path)
    recorded = session_self_name(data)
    if recorded is None or recorded == p.name:
        return False
    if siblings is None:
        root = p.parent
        while root.parent != root and root.name not in ("control_files",):
            root = root.parent
        siblings = {q.name for q in root.rglob("*.ptx")}
    return recorded in siblings


def region_names(data: bytes) -> "list[tuple[int, str]]":
    """Every audio REGION's name, as ``(region_zmark, name)`` in file order. An audio
    region record (content_type 0x2628) frames as
    ``5A | blocktype | size | 0x2628 | <u32 name-len @+9> | <name @+13> | geometry...``;
    the length-prefixed name is the region's source/clip name (what shows in the clips
    list and on placed clips, e.g. ``VERSE.dup3_67-09``, ``VPS kit 1.grp.L``). The
    layout is present on 100% of region records across the corpus."""
    out: "list[tuple[int, str]]" = []
    for z, _e, c in _size_driven_blocks(data):
        if c != 0x2628:
            continue
        n = int.from_bytes(data[z + 9 : z + 13], "little")
        out.append((z, _decode_name(data[z + 13 : z + 13 + n]) if n else ""))
    return out


def _region_list_names(data: bytes,
                       bl: "list[tuple[int, int, int]] | None" = None) -> "list[str]":
    """Region names indexed by the **0x2629 region-instance list** in file order — the
    index space a placement's +11 ref uses. `names[ref]` is the name of the 0x2628
    record nested inside the ref-th 0x2629 (single forward pass)."""
    if bl is None:
        bl = _size_driven_blocks(data)
    out: "list[str]" = []
    cur = None  # (z, end, slot) of the open 0x2629
    for z, e, c in bl:                                   # bl is sorted by zmark
        if c == 0x2629:
            out.append("")
            cur = (z, e, len(out) - 1)
        elif c == 0x2628 and cur and cur[0] < z < cur[1] and out[cur[2]] == "":
            n = int.from_bytes(data[z + 9 : z + 13], "little")
            out[cur[2]] = _decode_name(data[z + 13 : z + 13 + n]) if n else ""
    return out


def clip_names(data: bytes, lane_zmark: int) -> "list[str]":
    """The region/source name of each clip in the lane at `lane_zmark`, in clip order
    (parallel to `remove_clip`'s `clip_index`). Each placement's `0x104F` carries a u32
    at +11 that is a 0-based index into the 0x2629 region list; this resolves it to the
    region name. PT-corpus-confirmed (the bak.075->076 added clips resolve to the added
    regions; a 'Wolf Wet' track's clips resolve to 'Wolf Wet.L')."""
    bl = _size_driven_blocks(data)
    rnames = _region_list_names(data, bl)
    lane_end = next((e for z, e, c in bl if z == lane_zmark and c == 0x1052), None)
    if lane_end is None:
        raise ValueError(f"no 0x1052 lane at zmark {lane_zmark}")
    placements = sorted(
        (pz, pe) for pz, pe, c in bl if c == 0x1050 and lane_zmark < pz < lane_end
    )
    out: "list[str]" = []
    for pz, pe in placements:
        z104f = next((z for z, _e, c in bl if c == 0x104F and pz < z < pe), None)
        ref = int.from_bytes(data[z104f + 11 : z104f + 15], "little") if z104f else -1
        out.append(rnames[ref] if 0 <= ref < len(rnames) else "")
    return out


def session_start_bar(data: bytes) -> int:
    """The session's starting bar NUMBER. Pro Tools can renumber a session to begin at
    an arbitrary bar (e.g. -2, 0, 2) instead of 1; the value lives in the first
    populated meter-map block (`0x2029`) as a signed i32 at the first meter event's +8
    (block payload +23). Returns 1 for a default session. PT-corpus-confirmed: every
    default session reads 1, and the renumbered 'THE WIND' session reads -2."""
    for z, _e, c in _size_driven_blocks(data):
        if c != 0x2029 or data[z + 9 : z + 14] != b"Meter":
            continue
        count = int.from_bytes(data[z + 20 : z + 24], "little")
        if count >= 1:
            return int.from_bytes(data[z + 32 : z + 36], "little", signed=True)
    return 1


def _first_prefixed_string(data: bytes, z: int, e: int, limit: int = 96) -> "str | None":
    """The first length-prefixed printable-ASCII run in `[z+9, z+9+limit)`, or None.

    The workhorse behind the readers below. A `u32` length followed by that many printable bytes
    is how Pro Tools writes every name in this format, and a name it wrote is the one kind of
    field the corpus can decode without the application (§5c)."""
    q = z + 9
    while q + 4 < min(e, z + 9 + limit):
        n = int.from_bytes(data[q : q + 4], "little")
        if 3 <= n <= 64 and q + 4 + n <= e:
            b = data[q + 4 : q + 4 + n]
            if all(0x20 <= x < 0x7F for x in b):
                return _decode_name(b)
        q += 1
    return None


def session_product(data: bytes) -> "str | None":
    """The Pro Tools EDITION that wrote this session, e.g. `Pro Tools Studio`, `Pro Tools HD`,
    `Pro Tools Ultimate`.

    Read from the length-prefixed string in the top-level `0x0003` header — present in 90/90
    sessions measured. This is the block `core.parse_version` reads its version number from, and
    the edition is why that number's meaning varies: a version field is only interpretable
    alongside the product that wrote it.

    It also predicts structure. Nine block types (`0x4808`, `0x4820`, `0x4824`, `0x4826`,
    `0x4828`, `0x4829`, `0x4834`, `0x4501`, `0x2567`) appear only in `Pro Tools Studio` sessions,
    so this is the field to branch on when synthesizing for a specific edition."""
    for z, e, c in _size_driven_blocks(data):
        if c == 0x0003:
            return _first_prefixed_string(data, z, e)
    return None


def volumes(data: bytes) -> "list[str]":
    """Every disk/VOLUME name the session references — `Macintosh HD`, an external SSD, etc.

    From the `0x0F3C` records (34/34 instances carried a name when measured). Nothing else in
    this format names a volume, which is also why these blocks differ more between machines than
    almost anything else in a session."""
    out: "list[str]" = []
    for z, e, c in _size_driven_blocks(data):
        if c != 0x0F3C:
            continue
        nm = _first_prefixed_string(data, z, e)
        if nm and nm not in out:
            out.append(nm)
    return out


#: Four-character audio-format tags in the file table, stored BYTE-REVERSED (`EVAW` = `WAVE`) --
#: the same convention the plug-in catalog uses for its manufacturer/type codes. Measured over 12
#: sessions: WAVE 1878, AIFF 8.
_FILE_TAGS = (b"EVAW", b"FFIA", b"CIAA", b"46FA", b"1LPM", b"tflA")


def audio_files(data: bytes) -> "list[tuple[str, str]]":
    """Every audio file the session references, as `(filename, format)`.

    Example: `("BS EBASS.L.wav", "WAVE")`. This is the question most often asked of a session file
    -- what does it need in order to open -- and nothing in this library could answer it: the
    exports covered WRITING wavs (`add_audio_clip`, `set_clip_wav`, `wrap_raw_wav`) and none of
    them reading the table.

    Layout. The file table is `0x1004`, whose payload opens with a **u32 file count** (the same
    field whose low byte produced the `0x2757` phantom), followed by children that tile the payload
    exactly. One of those children is a `0x103A` name list:

        u32 len | name | 4-char format tag, byte-reversed

    **The reader checks itself.** `0x1004` declares how many files there are, so a parse that
    disagrees is wrong by the file's own account. Measured: the name count equals the declared
    count in every `0x103A` that sits under a `0x1004`, including 76/76 and 158/158.

    Only the `0x103A` INSIDE the file table is read. The type is used elsewhere for other lists,
    and those carry no declared count to check against, so they are left alone rather than guessed
    at.
    """
    bl = _size_driven_blocks(data)
    tables = [(z, e) for z, e, c in bl if c == 0x1004 and z >= 31]
    if not tables:
        return []
    tz, te = tables[0]
    out: "list[tuple[str, str]]" = []
    for z, e, c in bl:
        if c != 0x103A or not (tz < z and e <= te):
            continue
        q = z + 9
        while q + 4 <= e:
            n = int.from_bytes(data[q : q + 4], "little")
            if 1 <= n <= 255 and q + 8 + n <= e:
                seg = data[q + 4 : q + 4 + n]
                tag = data[q + 4 + n : q + 8 + n]
                # NOT `all(32 <= b < 127)`. Filenames are UTF-8 -- macOS stores accents DECOMPOSED
                # -- and an ASCII-only test silently DROPS them. Measured before the fix: 20 of 462
                # sessions parsed fewer files than their own table declares, all in one project
                # family, all undercounts, and every missing name was accented: `Butterbeans Café`,
                # `Thalía with Dora the Explorer`. Third time an ASCII assumption has bitten this
                # project, after the 0x2519 name-table walk and the validator's ordinal offsets.
                #
                # The 4-byte format tag is the validator, and a strong one: a length-prefixed run
                # followed by one of six fixed tags. Control bytes are still rejected, since no
                # filename contains them and they are what a mis-framed run looks like.
                if tag in _FILE_TAGS and not any(b < 0x20 for b in seg):
                    out.append((_decode_name(seg), tag[::-1].decode("ascii")))
                    q += 8 + n
                    continue
            q += 1
    return out


def audio_file_count(data: bytes) -> "int | None":
    """The file count the `0x1004` table DECLARES, or None if there is no table.

    Exposed separately because it is the oracle `audio_files()` checks itself against: if the two
    disagree on a session, the parse is wrong by the file's own account rather than by ours.
    """
    for z, _e, c in _size_driven_blocks(data):
        if c == 0x1004 and z >= 31:
            return int.from_bytes(data[z + 9 : z + 13], "little")
    return None


def plugins(data: bytes) -> "list[tuple[str, str]]":
    """Every plug-in in the session's catalog, as `(display_name, identifier)`.

    Example: `("FabFilter Pro-Q 3", "com.fabfilter.Pro-Q.3.AAX")`. **The identifier may be empty**
    -- many records carry only a display name -- so callers must not assume it is present.

    `0x1017` is in the parser's LEAF_PAYLOADS: the block WALK must not descend into it, because
    plug-in names contain `Z` and manufacture phantoms (the catalogue's `0x0c00` is the `Z` in
    `iZotope`). "Do not descend" is not "cannot read"; the record is regular:

        +9   u8   record kind -- 3, 4, 8 carry a plug-in; **0xFF is an EMPTY STUB**
        +10  u32  length of the display name
        +14  ..   display name
        ..   12   three four-character codes (manufacturer / type / subtype, byte-reversed)
        ..   7    fixed bytes
        ..   u32  length of the identifier   ) OPTIONAL -- absent on many kind-3 records,
        ..   ..   identifier                 ) which is why it must not be required
        ..   ..   trailer

    > **CORRECTED 2026-07-28, the day after it shipped.** The first version REQUIRED the identifier
    > and silently dropped every record without one, so a session whose plug-ins are all
    > identifier-less returned an empty list: **`plugins()` was `[]` on 128 of 407 PT-authored
    > sessions (31.4%)**, 32 of them provably carrying plug-in names in their own `0x1017` records.
    >
    > It shipped with a number that looked fine — "1534 of 1585 blocks parse, 96.8%" — measured on
    > **25 sessions from one drive**, and quoted per BLOCK when what a caller experiences is per
    > SESSION. Both errors are ones this project has written about repeatedly: a narrow sample, and
    > a denominator that flatters.

    Self-checkable: see `plugin_count()`.
    """
    bl = _size_driven_blocks(data)
    # SCOPED TO THE CATALOG. Every session carries exactly one 0x1018 and, measured on 40 of 40,
    # exactly TWO 0x1017 blocks that sit OUTSIDE it -- a different use of the type. Reading those
    # made `len(plugins()) > plugin_count()` on 24 of 407 sessions, i.e. broke the very invariant
    # the oracle exists to provide. Falls back to every 0x1017 when a session has no 0x1018.
    cats = [(z, e) for z, e, c in bl if c == 0x1018 and z >= 31]
    out: "list[tuple[str, str]]" = []
    seen: "set[tuple[str, str]]" = set()
    for z, e, c in bl:
        if c != 0x1017 or z < 31 or e - z < 14:
            continue
        if cats and not any(cz < z and e <= ce for cz, ce in cats):
            continue
        if data[z + 9] == 0xFF:                     # empty stub: a slot, not a plug-in
            continue
        n = int.from_bytes(data[z + 10 : z + 14], "little")
        if not (0 < n <= 200) or z + 14 + n > e:
            continue
        name = data[z + 14 : z + 14 + n]
        if any(b < 0x20 for b in name):
            continue
        ident = b""
        q = z + 14 + n + 12 + 7                     # past the codes and the fixed bytes
        if q + 4 <= e:
            m = int.from_bytes(data[q : q + 4], "little")
            if 0 < m <= 250 and q + 4 + m <= e:
                cand = data[q + 4 : q + 4 + m]
                if not any(b < 0x20 for b in cand):
                    ident = cand
        try:
            pair = (_decode_name(name), _decode_name(ident) if ident else "")
        except Exception:
            continue
        if pair not in seen:
            seen.add(pair); out.append(pair)
    return out


def plugin_count(data: bytes) -> "int | None":
    """The plug-in count the `0x1018` catalog container DECLARES, or None if there is none.

    `0x1018`'s payload opens with a `u32` that equals its direct `0x1017` child count -- verified
    on 45 of 45 PT-authored sessions, and reported by an independent audit at 357/357. It is the
    oracle `plugins()` can be checked against, exactly as `audio_file_count()` serves
    `audio_files()`.

    Note the two count DIFFERENT things: this counts catalog SLOTS including the `0xFF` empty
    stubs, while `plugins()` returns only populated records and de-duplicates. So
    `len(plugins(d)) <= plugin_count(d)` is the invariant, not equality.
    """
    for z, _e, c in _size_driven_blocks(data):
        if c == 0x1018 and z >= 31:
            return int.from_bytes(data[z + 9 : z + 13], "little")
    return None


def midi_devices(data: bytes) -> "list[tuple[str, str]]":
    """The session's MIDI devices as `(name, kind)`, kind being `"input"` or `"port"`.

    `0x208C` is a NAMED-DEVICE record, and it is used by **two different lists** — a distinction
    worth keeping, because conflating them mixes hardware you play with destinations you send to:

    | parent | kind | measured | examples |
    |---|---|---|---|
    | `0x206F` | `input` | 11 | `Alesis Vi61`, `OXYGEN 8` — physical controllers |
    | `0x2086` | `port` | 407 | `Stylus RMX 1`, `Blue Cat's PatchWork Synth 1`, `Network, M1 Mac` |

    The `input` list is the one that reads as a constant in most corpora: it is EMPTY in all but
    26 of 1,532 sessions, which is why `0x206F`'s payload was long recorded as an invariant
    sentinel (`u16` form, `u16` device count, then one `0x208C` per device). `0x2086` itself hangs
    off `0x2050` or `0x2006`.

    Names are returned in file order, duplicates included — a session legitimately lists the same
    port more than once."""
    kinds = {0x206F: "input", 0x2086: "port"}
    bl = sorted(_size_driven_blocks(data))
    out: "list[tuple[str, str]]" = []
    stack: "list[tuple[int, int, int]]" = []
    for z, e, c in bl:
        while stack and stack[-1][1] <= z:
            stack.pop()
        if c == 0x208C:
            kind = kinds.get(stack[-1][2]) if stack else None
            if kind:
                nm = _first_prefixed_string(data, z, e)
                if nm:
                    out.append((nm, kind))
        stack.append((z, e, c))
    return out


def session_sample_rate(data: bytes) -> int:
    """The session sample rate in Hz, from the `0x1028` INFO block (`u32` at block +11).
    PT-corpus-confirmed across all 26 sessions (44100 / 48000 / 96000)."""
    for z, _e, c in _size_driven_blocks(data):
        if c == 0x1028:
            return int.from_bytes(data[z + 11 : z + 15], "little")
    raise ValueError("no 0x1028 sample-rate block")


def session_bit_depth(data: bytes) -> int:
    """The session recording bit depth, from the `0x1028` INFO block: a `u8` 5 bytes
    before the payload end (block +(size+2)). 0x18 -> 24-bit, 0x20 -> 32-bit float;
    other values pass through. PT-corpus-confirmed across all 26 sessions."""
    for z, _e, c in _size_driven_blocks(data):
        if c == 0x1028:
            size = int.from_bytes(data[z + 3 : z + 7], "little")
            return {0x18: 24, 0x20: 32}.get(data[z + size + 2], data[z + size + 2])
    raise ValueError("no 0x1028 sample-rate block")


def base_tempo(data: bytes) -> "float | None":
    """The session's base (starting) tempo in BPM -- the first event of the tempo map.

    The `0x2028` block opens with a "Tempo" tag, the event count as a `u32` at payload +11,
    then per-event "Const"/"TMS" records. The event-0 BPM is the first plausible IEEE-754
    `f64` in [20, 300] within the block. Returns None if the session has no tempo map.
    Returns the first event of `tempo_map`. PT-corpus-confirmed sensible across all 26
    corpus sessions."""
    tm = tempo_map(data)
    return round(tm[0][0], 4) if tm else None


def base_meter(data: bytes) -> "tuple[int, int]":
    """The session's base (starting) time signature as `(numerator, denominator)`.

    The `0x2029` "Meter" block holds the event count as a `u32` at payload +11 and events
    at payload +15; event 0 carries the start bar (`i32` at +8), numerator (`u32` at +12)
    and denominator (`u32` at +16). An empty meter map (count 0) or a session with no meter
    block defaults to 4/4. Returns the `(numerator, denominator)` of the first event of
    `meter_map`. PT-corpus-confirmed sensible across all 26 corpus sessions."""
    mm = meter_map(data)
    return (mm[0][0], mm[0][1]) if mm else (4, 4)


def tempo_map(data: bytes) -> "list[tuple[float, int]]":
    """The full tempo map as `[(bpm, tick), ...]` in conductor-tick order (the same shape
    `set_tempo_map` consumes). Decoded from the `0x2028` "Tempo" block: `count` (`u32` at
    payload +11) fixed 61-byte records starting at `zmark+28` (each `Const`/`TMS`-framed);
    per record the musical position is a 5-byte LE tick at +30 (minus `ZERO_TICKS`) and the
    BPM is an IEEE-754 `f64` at +40. Returns [] if the session has no tempo map. Recovers
    PT-authored controls exactly (120->140 @bar2, 90, 121) and is sane/monotonic across the
    corpus (up to 111 events)."""
    import struct
    for z, e, c in _size_driven_blocks(data):
        if c != 0x2028 or data[z + 9 : z + 14] != b"Tempo":
            continue
        count = int.from_bytes(data[z + 20 : z + 24], "little")
        out: "list[tuple[float, int]]" = []
        for i in range(count):
            p = z + 28 + 61 * i
            if p + 61 > len(data):        # `count` is authoritative; bound only by the buffer
                break                     # (a resized 0x2028's declared size can lag its records)
            tick = int.from_bytes(data[p + 30 : p + 35], "little") - _ZERO_TICKS
            out.append((struct.unpack_from("<d", data, p + 40)[0], tick))
        return out
    return []


def meter_map(data: bytes) -> "list[tuple[int, int, int]]":
    """The full meter map as `[(numerator, denominator, tick), ...]` in conductor-tick order
    (the shape `set_meter_map` consumes). Decoded from the `0x2029` "Meter" block: `count`
    (`u32` at payload +11) fixed 36-byte records starting at `zmark+24`; per record the
    position is a 5-byte LE tick at +0 (minus `ZERO_TICKS`), the numerator a `u32` at +12,
    the denominator a `u32` at +16 (a `u32` "ordinal"/start-bar sits at +8, see §5c). Note
    the enclosing block's `payload_len` counts `12 + count*52` -- 36 for the record plus a
    16-byte entry in the `0x2719` lane. Returns [] if the session has no meter map. Recovers
    PT-authored controls exactly (4/4->3/4 @bar2) and is sane across the corpus (up to 18
    events)."""
    for z, e, c in _size_driven_blocks(data):
        if c != 0x2029 or data[z + 9 : z + 14] != b"Meter":
            continue
        count = int.from_bytes(data[z + 20 : z + 24], "little")
        out: "list[tuple[int, int, int]]" = []
        for i in range(count):
            p = z + 24 + 36 * i
            if p + 36 > len(data):        # `count` is authoritative; bound only by the buffer
                break
            tick = int.from_bytes(data[p : p + 5], "little") - _ZERO_TICKS
            out.append((
                int.from_bytes(data[p + 12 : p + 16], "little"),
                int.from_bytes(data[p + 16 : p + 20], "little"),
                tick,
            ))
        return out
    return []


def markers(data: bytes) -> "list[dict]":
    """Every session marker as `{"name": str, "tick": int|None, "sample": int|None}`.

    Each marker is a `0x2077` record (nested in a `0x2030` list): ordinal `u8` @+9, name
    length-prefixed (`u32` len @+15, bytes @+19), and a `u64` position at `name_end`
    (= 19 + namelen) with the SAME timebase encoding as clips (§5d) -- top byte `0x40` =
    tick-locked (`tick = pos - 0x4000000000000000 - ZERO_TICKS`), `0x00` = sample-locked
    (`sample = pos`, e.g. a marker dropped at an audio hit). A session can carry a duplicate
    `0x2030` list (PT's memory-locations copy), so records are de-duplicated by `(name,
    position)`; results are in file (list) order. Recovers PT-authored controls exactly and
    is sane across the corpus (marker counts 0..123, mixed tick/sample on some sessions)."""
    out: "list[dict]" = []
    seen: "set[tuple[str, int]]" = set()
    for z, _e, c in _size_driven_blocks(data):
        if c != 0x2077:
            continue
        nl = int.from_bytes(data[z + 15 : z + 19], "little")
        ne = z + 19 + nl
        if nl > 256 or ne + 8 > len(data):
            continue
        name = _decode_name(data[z + 19 : ne])
        pos = int.from_bytes(data[ne : ne + 8], "little")
        if (name, pos) in seen:
            continue
        seen.add((name, pos))
        if pos >> 56 == 0x40:
            out.append({"name": name, "tick": pos - 0x4000000000000000 - _ZERO_TICKS,
                        "sample": None})
        else:
            out.append({"name": name, "tick": None, "sample": pos})
    return out


def _lane_track_name(data: bytes, lane_zmark: int, lane_end: int) -> str:
    """Track/lane name: the length-prefixed string in the lane head (`<u32 len><len
    bytes>`). Read the exact length -- a greedy printable scan would swallow the
    adjacent u32 placement-count byte (e.g. 'BRUSH 2' + count 104='h' -> 'BRUSH 2h')."""
    p = lane_zmark + 9
    end = min(lane_end, lane_zmark + 256)
    while p + 4 <= end:
        n = int.from_bytes(data[p : p + 4], "little")
        if 1 <= n <= 64 and p + 4 + n <= len(data) and all(
            32 <= b < 127 for b in data[p + 4 : p + 4 + n]
        ):
            return data[p + 4 : p + 4 + n].decode("latin1")
        p += 1
    return ""


def clip_lanes(data: bytes) -> "list[tuple[int, str, int]]":
    """Enumerate every audio-playlist lane (0x1052) holding clips, as
    `(lane_zmark, track_name, n_clips)`. A clip is a 0x1050 placement nested in
    the lane's byte range (lane -> 0x1050 placement -> 0x104f position). A track
    can expose more than one lane with the same name (alternate playlists). Pass a
    lane's zmark to `remove_clip`."""
    bl = _size_driven_blocks(data)
    placements = sorted((z, e) for z, e, c in bl if c == 0x1050)
    out: "list[tuple[int, str, int]]" = []
    for z, e, c in bl:
        if c != 0x1052:
            continue
        n = sum(1 for pz, _pe in placements if z < pz < e)
        if n:
            out.append((z, _lane_track_name(data, z, e), n))
    return out


# The three parallel placement chains, measured over the corpus (§5c). Each is
# `holder -> wrapper -> 0x104F placement`, and only the two OUTER types differ; the placement
# record itself (position, region ref, timebase) is shared by all three.
_PLACEMENT_CHAINS = (
    (0x1052, 0x1050, "audio"),
    (0x1057, 0x1056, "midi"),
    (0x1053, 0x1051, "video"),
)


def placement_lanes(data: bytes, kinds=("audio", "midi", "video")) -> "list[tuple[int, str, int, str]]":
    """Every placement holder of the requested KINDS, as `(zmark, name, n_placements, kind)`.

    `clip_lanes` covers audio only -- it enumerates `0x1052` -- which is correct for the edit
    functions it feeds, but means neither it nor `clips()` can see a MIDI or video clip. Measured:
    on a session with 185 MIDI placements `clips()` returns 511 of the 696 `0x104F` records
    present. This is the enumerator that sees all three:

        audio   0x1052 -> 0x1050 -> 0x104F
        MIDI    0x1057 -> 0x1056 -> 0x104F
        video   0x1053 -> 0x1051 -> 0x104F

    Each chain is holder -> wrapper -> placement, differing only in the two outer content types.
    Read-only and additive: `clip_lanes` is unchanged, so nothing that edits by lane zmark is
    affected. The `kind` field is returned so a caller can tell which chain a zmark came from.

    A zmark from here can be passed straight to `remove_clip`, which selects the chain from the
    holder's own content type. (Until 2026-07-27 that was not true and this docstring said so;
    `remove_clip` was hardwired to the audio pair.)"""
    want = {k for k in kinds}
    bl = _size_driven_blocks(data)
    by_type: "dict[int, list[tuple[int, int]]]" = {}
    for z, e, c in bl:
        by_type.setdefault(c, []).append((z, e))
    out: "list[tuple[int, str, int, str]]" = []
    for holder_ct, wrapper_ct, kind in _PLACEMENT_CHAINS:
        if kind not in want:
            continue
        wrappers = sorted(by_type.get(wrapper_ct, ()))
        for z, e in sorted(by_type.get(holder_ct, ())):
            n = sum(1 for wz, _we in wrappers if z < wz < e)
            if n:
                out.append((z, _lane_track_name(data, z, e), n, kind))
    out.sort()
    return out


def _lane_count_pos(data: bytes, lane_zmark: int, first_placement: int,
                    n_placements: int) -> "int | None":
    """Offset of the lane's u32 placement-count field. It sits just before the
    first placement (a u32 immediately after the lane name);
    fall back to the last u32 == n in the lane head if the layout varies."""
    if int.from_bytes(data[first_placement - 4 : first_placement], "little") == n_placements:
        return first_placement - 4
    for p in range(first_placement - 4, lane_zmark + 8, -1):
        if int.from_bytes(data[p : p + 4], "little") == n_placements:
            return p
    return None


def remove_clip(data: bytes, lane_zmark: int, clip_index: int) -> bytes:
    """Remove one clip from the lane at `lane_zmark` in an ARBITRARY existing
    session, leaving every other track/clip/plugin/automation byte-exact.

    Works on ALL THREE placement chains — audio (`0x1052`→`0x1050`), MIDI
    (`0x1057`→`0x1056`) and video (`0x1053`→`0x1051`). The chain is chosen from the content
    type of the block at `lane_zmark`, so a caller passes a zmark from `clip_lanes` (audio) or
    `placement_lanes` (any kind) and does not have to say which it is.

    `clip_index` is the placement's rank in file order within the lane. Splices
    out the placement (its wrapper + the `0x104F` position child), decrements
    the lane's u32 placement count, shrinks every enclosing block size, and slides
    every master-index offset that sits past the cut. The clip's region/audio-file
    records are intentionally left untouched (they linger in the clips bin, which
    is harmless and matches deleting a clip from the timeline).

    None of the splice arithmetic was ever audio-specific: the three chains differ only in the two
    OUTER content types, and the `0x104F` placement record itself is shared. Only the two constants
    were audio-specific, and they are now looked up.

    **Confirmed in Pro Tools for AUDIO only.** MIDI and video pass the same corpus battery the
    audio path passed before its PT run, but a reader returning `rc=0` is not the oracle here — PT
    is. Treat the MIDI/video paths as unconfirmed until a session edited this way has been opened.
    """
    # One `final_index_ref` (the slow scan parser lives there); everything else is
    # derived from the fast size-driven walk + the index it points at.
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    lane = next((b for b in bl if b[0] == lane_zmark), None)
    if lane is None:
        raise ValueError(f"no block at zmark {lane_zmark}")
    chain = next((ch for ch in _PLACEMENT_CHAINS if ch[0] == lane[2]), None)
    if chain is None:
        holders = ", ".join("0x%04x (%s)" % (h, k) for h, _w, k in _PLACEMENT_CHAINS)
        raise ValueError(
            f"block at zmark {lane_zmark} is 0x{lane[2]:04x}, not a placement holder; "
            f"expected one of {holders}"
        )
    wrapper_ct = chain[1]
    lane_end = lane[1]
    placements = sorted(
        (pz, pe) for pz, pe, c in bl if c == wrapper_ct and lane_zmark < pz < lane_end
    )
    if not 0 <= clip_index < len(placements):
        raise ValueError(
            f"clip_index {clip_index} out of range (lane has {len(placements)} clips)"
        )
    pz, pe = placements[clip_index]
    cut = pe - pz
    count_pos = _lane_count_pos(data, lane_zmark, placements[0][0], len(placements))
    recs = _FI.parse_records(ref.data, {c for _z, _e, c in bl}, {z for z, _e, _c in bl})
    out = bytearray(data[:pz] + data[pe:])
    for z, e, _c in bl:                              # shrink enclosing block sizes
        if z < pz and e >= pe:
            out[z + 3 : z + 7] = (
                int.from_bytes(out[z + 3 : z + 7], "little") - cut
            ).to_bytes(4, "little")
    if count_pos is not None:                        # decrement lane placement count
        out[count_pos : count_pos + 4] = (
            int.from_bytes(out[count_pos : count_pos + 4], "little") - 1
        ).to_bytes(4, "little")
    # The body shrank by `cut` at `pz`; every index offset that pointed past the cut
    # shifts down by `cut`. Offsets before the cut are already correct in the copied
    # tail, so only the shifted ones need rewriting (at their new position abs-cut).
    def shift(abs_pos: int, val: int) -> None:
        if val > pz:
            out[abs_pos - cut : abs_pos - cut + 4] = (val - cut).to_bytes(4, "little")
    for r in recs:
        for c in r.child_refs:
            shift(ref.start + r.start + c.rel + 2, c.offset)
        for e in r.elements:
            for i, o in enumerate(e.offsets):
                shift(ref.start + r.start + e.rel + 5 + 4 * i, o)
    # repoint the first block at the moved index (first block is before the cut)
    first_offset = min(z for z, _e, _c in bl) + 7
    out[first_offset : first_offset + 4] = (ref.start - cut).to_bytes(4, "little")
    return bytes(out)


def region_length(data: bytes, region_index: int) -> "int | None":
    """The region's length in samples: the FIRST present field of its `0x2628` variable section.

    Proven against the source WAVs -- for every corpus region whose file is on disk, this field
    equals `wav_clip_identity(wav)[0]` exactly (40/40). The field's width comes from the
    descriptor nibbles (2 bytes in ~94% of records, sometimes 3 or 4), so it is read at the width
    the descriptor declares, not a fixed one. Returns None if the region declares no field."""
    z28 = _region_2628(data, region_index)
    if z28 is None:
        return None
    z, nl = z28
    desc = data[z + 13 + nl : z + 13 + nl + 5]
    widths = [desc[1] >> 4, desc[1] & 15, desc[2] >> 4, desc[2] & 15, desc[3] >> 4, desc[3] & 15]
    w = next((x for x in widths if x), 0)
    if not w:
        return None
    at = z + 13 + nl + 5
    return int.from_bytes(data[at : at + w], "little")


def _region_2628(data: bytes, region_index: int) -> "tuple[int, int] | None":
    """`(zmark_of_0x2628, namelen)` for region `region_index`, or None."""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    lists = [(z, e) for z, e, c in bl if c == 0x262A]
    if not lists:
        return None
    lz, le = lists[0]
    entries = sorted((z, e) for z, e, c in bl if c == 0x2629 and lz < z < le)
    if not 0 <= region_index < len(entries):
        return None
    sz, se = entries[region_index]
    for z, e, c in bl:
        if c == 0x2628 and sz < z < se:
            nl = int.from_bytes(data[z + 9 : z + 13], "little")
            return (z, nl) if 0 < nl <= 128 else None
    return None


def set_region_length(data: bytes, region_index: int, sample_count: int) -> bytes:
    """Set region `region_index`'s length (samples) -- the first field of its `0x2628` variable
    section. Size-neutral; raises rather than guessing when the value will not fit.

    This is the field `retarget_audio_file` deliberately leaves alone: retargeting a registration
    at a WAV of a DIFFERENT length leaves the region's geometry describing the old one. Call this
    with `wav_clip_identity(wav)[0]` to finish the job.

    WIDTH LIMIT, stated rather than worked around: the field's byte width is declared by the
    descriptor nibbles and is 2 bytes in ~94% of corpus records, so counts above 65535 do not fit.
    Widening it means rewriting the descriptor nibble AND resizing the block, which changes every
    enclosing size and index offset -- that is the genuinely unfinished part of the `0x2628`
    model, so this raises instead of silently truncating."""
    z28 = _region_2628(data, region_index)
    if z28 is None:
        raise ValueError(f"no 0x2628 record for region {region_index}")
    z, nl = z28
    desc = data[z + 13 + nl : z + 13 + nl + 5]
    widths = [desc[1] >> 4, desc[1] & 15, desc[2] >> 4, desc[2] & 15, desc[3] >> 4, desc[3] & 15]
    w = next((x for x in widths if x), 0)
    if not w:
        raise ValueError(f"region {region_index} declares no length field (descriptor {desc.hex()})")
    if sample_count < 0:
        raise ValueError(f"sample_count must be non-negative, got {sample_count}")
    need = max(1, (sample_count.bit_length() + 7) // 8)
    at = z + 13 + nl + 5
    if need <= w:
        out = bytearray(data)                       # fits: size-neutral in-place write
        out[at : at + w] = int(sample_count).to_bytes(w, "little")
        return bytes(out)

    # WIDEN: the value needs more bytes than the descriptor declares. Grow the field in place by
    # splicing the extra bytes in and bumping the descriptor nibble that declares this slot. The
    # slot is the first NON-ZERO width, and zero-width slots consume no bytes, so the field always
    # starts at the variable section's origin. `_splice_in` handles every enclosing block size and
    # every master-index offset (0x2628/0x2629/0x262A are not index targets, so no new entries).
    if need > 15:
        raise ValueError(f"sample_count {sample_count} needs {need} bytes; a nibble caps a slot at 15")
    slot = next(i for i, x in enumerate(widths) if x)
    out = bytearray(_splice_in(data, at + w, b"\x00" * (need - w)))
    dz = z + 13 + nl                                # descriptor start (before `at`, so unmoved)
    bi, hi = dz + 1 + slot // 2, (slot % 2 == 0)
    cur = out[bi]
    out[bi] = ((need << 4) | (cur & 0x0F)) if hi else ((cur & 0xF0) | need)
    out[at : at + need] = int(sample_count).to_bytes(need, "little")
    return bytes(out)


def retarget_audio_file(data: bytes, file_index: int, wav_path) -> bytes:
    """Point ONE registered audio file (the `file_index`-th `0x1003` record) at `wav_path`,
    leaving every other file, region, clip and track byte-identical.

    This is the piece that turns `duplicate_audio_file` (which copies a record verbatim, so the
    copy describes the SAME audio) into genuine NEW-audio registration. The identity fields Pro
    Tools matches on are the ones `set_clip_wav` already writes for the synthesis path, applied
    here to a single record instead of all of them:

      * `+15`  u32  sample count (data-chunk bytes / block align)
      * `+44`  8 B  the WAV's `umid` material, `2a`-marked
      * `+292` 8 B  the same material with a `00` prefix instead of the `2a` marker
      * `+301` 2 B  `id2`, from the bext SMPTE-UMID marker

    Size-neutral -- every field is fixed-width, so no block sizes or index offsets move.

    Only PT/BWF WAVs carry the `umid`/`bext` chunks this needs; `wav_clip_identity` raises on a
    raw WAV. The region's own length field (`0x2628`, first variable slot) is NOT touched here --
    see `set_clip_wav`, which writes it as part of a whole-clip retarget.
    """
    sample_count, umid_material, id2 = wav_clip_identity(wav_path)
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    recs = sorted((z, e) for z, e, c in bl if c == 0x1003)
    if not 0 <= file_index < len(recs):
        raise ValueError(f"file_index {file_index} out of range ({len(recs)} audio files)")
    z, e = recs[file_index]
    out = bytearray(data)
    out[z + 15 : z + 19] = int(sample_count).to_bytes(4, "little")
    out[z + 44 : z + 52] = umid_material
    out[z + 292 : z + 300] = b"\x00" + umid_material[1:]
    out[z + 301 : z + 303] = id2
    return bytes(out)


def _splice_in(data: bytes, at: int, blob: bytes) -> bytes:
    """Insert `blob` at body offset `at`, growing every container that spans the point and sliding
    every master-index offset that sits at or past it. Size-neutral bookkeeping only -- the caller
    is responsible for any COUNT field the new element belongs to.

    Extracted from `duplicate_region` / `add_clip` / `remove_clip`, which had each grown their own
    copy of this. It is only valid for elements that are NOT master-index targets (verified for
    0x2629/0x2628 regions and 0x1003/0x103A audio-file records); an indexed element additionally
    needs its own index entry, which `duplicate_track` handles separately."""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    grow = len(blob)
    out = bytearray(data[:at] + blob + data[at:])
    for z, e, _c in bl:
        if z < at <= e:
            out[z + 3 : z + 7] = (
                int.from_bytes(out[z + 3 : z + 7], "little") + grow
            ).to_bytes(4, "little")
    recs = _FI.parse_records(ref.data, {c for _z, _e, c in bl}, {z for z, _e, _c in bl})
    def bump(abs_pos: int, val: int) -> None:
        if val >= at:
            out[abs_pos + grow : abs_pos + grow + 4] = (val + grow).to_bytes(4, "little")
    for r in recs:
        for c in r.child_refs:
            bump(ref.start + r.start + c.rel + 2, c.offset)
        for el in r.elements:
            for i, o in enumerate(el.offsets):
                bump(ref.start + r.start + el.rel + 5 + 4 * i, o)
    first_offset = min(z for z, _e, _c in bl) + 7
    out[first_offset : first_offset + 4] = (ref.start + grow).to_bytes(4, "little")
    return bytes(out)


def _audio_file_list(data: bytes) -> "tuple[int, int, list[tuple[int, str]]]":
    """`(list_zmark, entries_end, [(entry_offset, filename)])` for the `0x103A` audio-file list.

    Entries pack back-to-back after the `"Audio Files"` anchor + a `u32`, each
    `02 00 00 00 00 <u32 namelen> <name> "EVAW"` -- stride `13 + namelen`. A session carries
    several 0x103A blocks; the real list is the one holding the anchor."""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    for z, e, c in bl:
        if c != 0x103A or e - z <= 60:
            continue
        # the media-folder name is length-prefixed at +18, NOT necessarily "Audio Files"
        nl = int.from_bytes(data[z + 18 : z + 22], "little")
        if not (0 < nl <= 64 and z + 22 + nl <= e):
            continue
        i = z + 22 + nl + 4
        out = []
        while i + 9 <= e and data[i : i + 4] == b"\x02\x00\x00\x00":
            nlen = int.from_bytes(data[i + 5 : i + 9], "little")
            if nlen <= 0 or i + 13 + nlen > e:
                break
            out.append((i, data[i + 9 : i + 9 + nlen].decode("latin1")))
            i += 13 + nlen
        return z, i, out
    raise ValueError("session has no 0x103A audio-file list")


def duplicate_audio_file(data: bytes, file_index: int, new_filename: str) -> bytes:
    """Register another audio file by copying entry `file_index` under `new_filename`.

    Adds BOTH halves of a wav's registration: the `0x103A` list entry (the filename Pro Tools
    matches on) and a copy of the corresponding per-wav `0x1003` record -- the corpus shows these
    are 1:1 (4 wavs -> 4 records; 1300 -> 1300). Neither is a master-index target, so no index
    entries are needed, only the offset slide `_splice_in` performs.

    The `0x1003` body is copied VERBATIM, so the new registration describes the SAME audio as the
    source. That makes this "register the same audio under a second name", which is what an
    in-place editor needs to attach a duplicated region to a filename. Pointing the record at
    genuinely DIFFERENT audio additionally requires rewriting the identity fields inside `0x1003`
    (length/rate/GUID) -- see `wav_clip_identity`.
    """
    lz, entries_end, entries = _audio_file_list(data)
    if not 0 <= file_index < len(entries):
        raise ValueError(f"file_index {file_index} out of range ({len(entries)} files)")
    nb = new_filename.encode("latin1")
    entry = b"\x02\x00\x00\x00\x00" + len(nb).to_bytes(4, "little") + nb + b"EVAW"
    out = _splice_in(data, entries_end, entry)

    ref = _FI.final_index_ref(out)
    bl = sorted(_raw_block_bounds(out, ref.start))
    recs = sorted((z, e) for z, e, c in bl if c == 0x1003)
    if recs:
        sz, se = recs[min(file_index, len(recs) - 1)]
        out = _splice_in(out, recs[-1][1], bytes(out[sz:se]))
    return out


def region_list(data: bytes) -> "list[tuple[int, str]]":
    """Every region IN THE REGION LIST as `(zmark_of_0x2629, name)`, in file order -- the same
    order the `0x104F` placement `+11` region INDEX refers to.

    SCOPE: this is the `0x262A` list, NOT every `0x2628` in the session. The region record is
    reachable by four routes (§5c "rare second routes"): `0x2629` inside the list, plus `0x2633`,
    `0x262B` (crossfade) and `0x262D`. Measured over 120 sessions, this function returns fewer
    records than there are `0x2628` blocks in 41 of them, and the shortfall is EXACTLY the
    non-list routes -- 1 to 21 records per session. That is correct for this function's purpose:
    the placement index addresses the list, so including crossfade-internal regions would shift
    every index and break `replace_clip_region`. Use `region_names()` when you want every region
    record regardless of route; it enumerates by content type and sees all of them (120/120).

    HANDLES ARE PER-BODY. The zmarks (and the lane zmarks from `clip_lanes`) are byte offsets into
    THIS body, so any edit that splices earlier in the file invalidates them. Re-derive them from
    the returned body after every edit; chaining `duplicate_audio_file` -> ... -> `add_clip` with a
    lane zmark captured before the chain fails with "no 0x1052 lane at zmark N". The region INDEX
    is stable (entries are appended), so prefer indices for anything you carry across edits."""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    lists = [(z, e) for z, e, c in bl if c == 0x262A]
    if not lists:
        return []
    lz, le = lists[0]
    out = []
    for z, e, c in bl:
        if c != 0x2629 or not (lz < z < le):
            continue
        nm = ""
        for rz, re_, rc in bl:
            if rc == 0x2628 and z < rz < e:
                nl = int.from_bytes(data[rz + 9 : rz + 13], "little")
                if 0 < nl <= 128:
                    nm = _decode_name(data[rz + 13 : rz + 13 + nl])
                break
        out.append((z, nm))
    return sorted(out)


def duplicate_region(data: bytes, region_index: int, new_name: str) -> bytes:
    """Append a copy of region `region_index` under `new_name`, leaving everything else intact.

    A region entry is a self-contained `0x2629` (holding its `0x2628` name+geometry record plus
    `0x4403`/`0x1000` siblings) inside the `0x262A` list, whose `u32` at payload+0 is the entry
    count. Neither `0x2629` nor `0x2628` is a master-index target -- only the `0x262A` list is --
    so adding one needs NO new index entries, just an offset slide for whatever sits past the
    insertion point.

    The copy's geometry is taken VERBATIM, which is exactly right: `0x2628`'s variable section is
    delimited by a constant 12-byte tail with a fixed 27-byte trailer (2774/2774 corpus records),
    so `span == 57 + namelen + varlen` and only the NAME needs rewriting. That is why this works
    without the field semantics that creating a region for genuinely new audio still requires.

    Returns the new body; the new region is the last entry, so its index is `len(before)`.
    """
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    lists = [(z, e) for z, e, c in bl if c == 0x262A]
    if not lists:
        raise ValueError("session has no 0x262A region list")
    lz, le = lists[0]
    entries = sorted((z, e) for z, e, c in bl if c == 0x2629 and lz < z < le)
    if not 0 <= region_index < len(entries):
        raise ValueError(f"region_index {region_index} out of range ({len(entries)} regions)")
    sz, se = entries[region_index]
    blob = bytearray(data[sz:se])

    r28 = [(rz, re_) for rz, re_, rc in bl if rc == 0x2628 and sz < rz < se]
    if not r28:
        raise ValueError("region entry has no 0x2628 record")
    rz, _re = r28[0]
    off = rz - sz                                     # 0x2628 start inside the copied blob
    old_len = int.from_bytes(blob[off + 9 : off + 13], "little")
    nn = new_name.encode("latin1")
    delta = len(nn) - old_len
    blob[off + 13 : off + 13 + old_len] = nn
    blob[off + 9 : off + 13] = len(nn).to_bytes(4, "little")
    for at in (off, 0):                               # 0x2628 then the enclosing 0x2629
        blob[at + 3 : at + 7] = (
            int.from_bytes(blob[at + 3 : at + 7], "little") + delta
        ).to_bytes(4, "little")

    at_ins = entries[-1][1]                           # append after the last entry
    grow = len(blob)
    out = bytearray(data[:at_ins] + bytes(blob) + data[at_ins:])
    for z, e, _c in bl:                               # grow every container spanning the insert
        if z < at_ins <= e:
            out[z + 3 : z + 7] = (
                int.from_bytes(out[z + 3 : z + 7], "little") + grow
            ).to_bytes(4, "little")
    out[lz + 9 : lz + 13] = (
        int.from_bytes(out[lz + 9 : lz + 13], "little") + 1
    ).to_bytes(4, "little")

    recs = _FI.parse_records(ref.data, {c for _z, _e, c in bl}, {z for z, _e, _c in bl})
    def bump(abs_pos: int, val: int) -> None:
        if val >= at_ins:
            out[abs_pos + grow : abs_pos + grow + 4] = (val + grow).to_bytes(4, "little")
    for r in recs:
        for c in r.child_refs:
            bump(ref.start + r.start + c.rel + 2, c.offset)
        for el in r.elements:
            for i, o in enumerate(el.offsets):
                bump(ref.start + r.start + el.rel + 5 + 4 * i, o)
    first_offset = min(z for z, _e, _c in bl) + 7
    out[first_offset : first_offset + 4] = (ref.start + grow).to_bytes(4, "little")
    return bytes(out)


def add_clip(data: bytes, lane_zmark: int, position: int,
             region_index: "int | None" = None, template_clip_index: int = 0) -> bytes:
    """Add a clip to the lane at `lane_zmark`, reusing a region already in the session.
    The exact inverse of `remove_clip` (validated by round-trip = byte-identity).

    A new placement is built by COPYING an existing placement in the lane
    (`template_clip_index`, so its block layout/variant matches), then setting its
    timeline `position` (the `u64` at `0x104F`+16) and, if `region_index` is given, its
    region reference (the `u32` at `0x104F`+11, a 0-based index into the 0x2629 region
    list — see `clip_names`). With `region_index=None` the template's region is kept, so
    the default is "duplicate this clip at a new position". The placement is spliced in
    after the lane's last clip; the lane's u32 count is incremented, enclosing block
    sizes grown, and every master-index offset past the splice slid up.

    `position` is the raw `u64`: plain samples for a sample-based track, or the encoded
    tick value for a tick-based track (copy a sibling's high byte at +23; see spec §5d).
    Reuses an EXISTING region only — it does not add a new region/audio file. (Structural
    + reader-valid; PT display confirmation pending, like all write paths.)"""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    lane = next((b for b in bl if b[0] == lane_zmark and b[2] == 0x1052), None)
    if lane is None:
        raise ValueError(f"no 0x1052 lane at zmark {lane_zmark}")
    lane_end = lane[1]
    placements = sorted(
        (pz, pe) for pz, pe, c in bl if c == 0x1050 and lane_zmark < pz < lane_end
    )
    if not placements:
        raise ValueError("lane has no existing clip to template the new placement from")
    if not 0 <= template_clip_index < len(placements):
        raise ValueError(f"template_clip_index {template_clip_index} out of range")
    tz, te = placements[template_clip_index]
    newp = bytearray(data[tz:te])                    # copy the template placement (0x1050 + 0x104F)
    rel = next((z - tz for z, _e, c in bl if c == 0x104F and tz < z < te), None)
    if rel is None:
        raise ValueError("template placement has no 0x104F position record")
    if region_index is not None:
        newp[rel + 11 : rel + 15] = int(region_index).to_bytes(4, "little")
    newp[rel + 16 : rel + 24] = int(position).to_bytes(8, "little")
    grow = len(newp)
    splice = placements[-1][1]                        # insert after the lane's last clip
    count_pos = _lane_count_pos(data, lane_zmark, placements[0][0], len(placements))
    recs = _FI.parse_records(ref.data, {c for _z, _e, c in bl}, {z for z, _e, _c in bl})
    out = bytearray(data[:splice] + bytes(newp) + data[splice:])
    for z, e, _c in bl:                              # grow the lane + its ancestors
        if z <= lane_zmark and e >= splice:
            out[z + 3 : z + 7] = (
                int.from_bytes(out[z + 3 : z + 7], "little") + grow
            ).to_bytes(4, "little")
    if count_pos is not None:                        # increment the lane placement count
        out[count_pos : count_pos + 4] = (
            int.from_bytes(out[count_pos : count_pos + 4], "little") + 1
        ).to_bytes(4, "little")
    def shift(abs_pos: int, val: int) -> None:       # slide index offsets past the splice up
        if val >= splice:
            out[abs_pos + grow : abs_pos + grow + 4] = (val + grow).to_bytes(4, "little")
    for r in recs:
        for c in r.child_refs:
            shift(ref.start + r.start + c.rel + 2, c.offset)
        for e in r.elements:
            for i, o in enumerate(e.offsets):
                shift(ref.start + r.start + e.rel + 5 + 4 * i, o)
    first_offset = min(z for z, _e, _c in bl) + 7    # repoint the first block at the moved index
    out[first_offset : first_offset + 4] = (ref.start + grow).to_bytes(4, "little")
    return bytes(out)


def _locate_placement_104f(data: bytes, lane_zmark: int, clip_index: int) -> int:
    """The 0x104F zmark of the `clip_index`-th clip in the lane at `lane_zmark`."""
    bl = _size_driven_blocks(data)
    lane = next((b for b in bl if b[0] == lane_zmark), None)
    if lane is None:
        raise ValueError(f"no block at zmark {lane_zmark}")
    # ALL THREE CHAINS, chosen from the holder's own content type -- the same generalisation
    # `remove_clip` needed. Hardwiring 0x1052/0x1050 made `move_clip` and `replace_clip_region`
    # refuse every MIDI and video clip with a message naming the audio type.
    chain = next((ch for ch in _PLACEMENT_CHAINS if ch[0] == lane[2]), None)
    if chain is None:
        holders = ", ".join("0x%04x (%s)" % (h, k) for h, _w, k in _PLACEMENT_CHAINS)
        raise ValueError(
            f"block at zmark {lane_zmark} is 0x{lane[2]:04x}, not a placement holder; "
            f"expected one of {holders}"
        )
    wrapper_ct, lane_end = chain[1], lane[1]
    placements = sorted(
        (pz, pe) for pz, pe, c in bl if c == wrapper_ct and lane_zmark < pz < lane_end
    )
    if not 0 <= clip_index < len(placements):
        raise ValueError(f"clip_index {clip_index} out of range ({len(placements)} clips)")
    pz, pe = placements[clip_index]
    z104f = next((z for z, _e, c in bl if c == 0x104F and pz < z < pe), None)
    if z104f is None:
        raise ValueError("placement has no 0x104F record")
    return z104f


def move_clip(data: bytes, lane_zmark: int, clip_index: int, position: int) -> bytes:
    """Move a clip to a new timeline `position` (the `u64` at `0x104F`+16). Size-neutral
    and index-neutral — an in-place field rewrite, so no reindex. `position` is the raw
    u64 (plain samples for a sample-based track; copy a sibling's tick encoding for a
    tick-based track — see spec §5d). A stereo clip has one lane per channel; move each.
    (PT-confirmed that a clip's timeline start is governed solely by this field.)"""
    z104f = _locate_placement_104f(data, lane_zmark, clip_index)
    out = bytearray(data)
    out[z104f + 16 : z104f + 24] = int(position).to_bytes(8, "little")
    return bytes(out)


def replace_clip_region(data: bytes, lane_zmark: int, clip_index: int,
                        region_index: int) -> bytes:
    """Repoint a clip at a different EXISTING region (the `u32` at `0x104F`+11, a 0-based
    index into the 0x2629 region list — see `clip_names`/`region_names`). Size- and
    index-neutral in-place rewrite. Changes which source audio the clip plays; does not
    add a new region/file. Verify the target with `region_names(data)[region_index]`."""
    z104f = _locate_placement_104f(data, lane_zmark, clip_index)
    out = bytearray(data)
    out[z104f + 11 : z104f + 15] = int(region_index).to_bytes(4, "little")
    return bytes(out)


def session_info(data: bytes) -> dict:
    """A consolidated read-only snapshot of an arbitrary session, from the confirmed read
    primitives: the writing PRODUCT, format (sample rate / bit depth / start bar),
    block/track/clip/region/marker counts, the placement lanes of all three kinds, and the
    machine-level context (volumes, MIDI devices).

    `n_clips` counts `0x1050` — AUDIO placements only, matching `clip_lanes`/`clips`. The
    per-kind breakdown is in `placements`, which covers the MIDI (`0x1056`) and video (`0x1051`)
    chains those two cannot see."""
    from collections import Counter
    bl = _size_driven_blocks(data)
    ctc = Counter(c for _z, _e, c in bl)
    _pl = placement_lanes(data)
    _dev = midi_devices(data)
    return {
        "product": session_product(data),
        "sample_rate": session_sample_rate(data),
        "bit_depth": session_bit_depth(data),
        "start_bar": session_start_bar(data),
        "base_tempo": base_tempo(data),
        "base_meter": base_meter(data),
        "n_blocks": len(bl),
        "n_tracks": ctc.get(0x261B, 0),          # one 0x261B per track of any type
        "n_clips": ctc.get(0x1050, 0),
        "n_regions": ctc.get(0x2628, 0),
        "n_markers": len(markers(data)),         # deduped (a 2nd 0x2030 copy can exist)
        "n_tempo_events": len(tempo_map(data)),
        "n_meter_events": len(meter_map(data)),
        "clip_lanes": [(name, k) for _z, name, k in clip_lanes(data)],
        # all three placement chains -- audio/MIDI/video (see `placement_lanes`)
        "placements": {kind: sum(k for _z, _n, k, kd in _pl if kd == kind)
                       for kind in ("audio", "midi", "video")},
        "volumes": volumes(data),
        "midi_inputs": [n for n, kind in _dev if kind == "input"],
        "midi_ports": [n for n, kind in _dev if kind == "port"],
    }


def clips(data: bytes) -> "list[dict]":
    """Every placed AUDIO clip in the session, as dicts with: `track` (the lane's track name),
    `clip_index` (rank within the lane, as `remove_clip`/`move_clip` take), `name` (the
    region/source name), `position` (the raw `u64` timeline position), and `lane_zmark`
    (to pass to the edit functions). Combines `clip_lanes`, the placement `0x104F` fields, and
    the 0x2629 region list.

    SCOPE — AUDIO ONLY. It reaches placements through `clip_lanes`, which enumerates `0x1052`
    audio lanes. A MIDI clip's placement hangs off `0x1057` through a `0x1056` wrapper instead
    (the MIDI twin of `0x1050`), so none are included. Measured: on a session with 185 `0x1056`
    wrappers this returns 511 of the 696 `0x104F` placements present, and the 185 missing are
    exactly the MIDI ones. That is a scope limit, not a defect — the edit functions this feeds
    (`remove_clip`, `move_clip`) take an audio lane zmark — but it is stated here because the
    docstring previously claimed "every placed clip in the session"."""
    bl = _size_driven_blocks(data)
    rnames = _region_list_names(data, bl)
    p104f = sorted(z for z, _e, c in bl if c == 0x104F)          # placement position records
    out: "list[dict]" = []
    for lz, tname, _k in clip_lanes(data):
        lane_end = next(e for z, e, c in bl if z == lz and c == 0x1052)
        pls = sorted((pz, pe) for pz, pe, c in bl if c == 0x1050 and lz < pz < lane_end)
        for ci, (pz, pe) in enumerate(pls):
            z = next((z for z in p104f if pz < z < pe), None)
            if z is None:
                continue
            ref = int.from_bytes(data[z + 11 : z + 15], "little")
            out.append({
                "track": tname, "clip_index": ci,
                "name": rnames[ref] if 0 <= ref < len(rnames) else "",
                "position": int.from_bytes(data[z + 16 : z + 24], "little"),
                "lane_zmark": lz,
            })
    return out


# --- container child-count invariant (a Pro Tools deserializer requirement) --
#
# Pro Tools reads the body as a nested stream: a "count-prefixed container" holds a
# u32 child-count at payload+0, then that many child blocks (some containers append
# a trailer block of a DIFFERENT type). If the stored count does not match the actual
# children, PT's per-object read walks off the end -> "Could not complete your request
# because end of stream encountered." Our size-driven reader tolerates a stale count
# (it walks by declared size), so an edit can pass rc=0 yet be PT-INVALID. Adding or
# removing a track changes these containers' children but not their declared count,
# so the count must be rewritten. Maps are corpus-derived (count == actual on every
# valid session): most tally leading children of ONE type; 0x2624 tallies ALL its
# (heterogeneous) per-track subtrees.
_COUNT_CONTAINER_CHILD = {0x1054: 0x1052}

# `0x1015` is deliberately NOT auto-recomputed. Its u32 is NOT its 0x1014 child count: MIDI tracks
# carry no 0x1014, so `midi9.ptx` has count=9 with ZERO 0x1014 children and `mixedmidi_5_AMSmM`
# count=5 with 3 -- tallying 0x1014 rewrote the count to the AUDIO-track total and silently
# corrupted every mixed audio+MIDI session a track edit touched. (All-audio sessions hid it: there
# tracks == 0x1014 children.) But "count == len(track_types())" is not universal either -- some
# corpus sessions disagree with BOTH models -- so no recompute is provably safe. A track edit
# instead ADJUSTS the existing value by its own delta (`_adjust_track_count`), which preserves
# whatever the file means and cannot corrupt it. `validate()` likewise does not assert on 0x1015.
# Every per-track record kind that hangs directly off the 0x2624 track list. 0x263B is the
# folder/VCA-style kind: absent from the 282-session control corpus entirely, but present in 27 of
# 40 sampled REAL sessions, indexed like the others, carrying a 0x2619 identity and a 0x4701
# playlist subtree. Walking only the first three made those tracks invisible to both editors on the
# majority of real sessions.
_TRACK_RECORD_TYPES = frozenset({
    0x261C,   # audio (mono/stereo)
    0x2620,   # MIDI
    0x261E,   # click / aux
    0x263B,   # folder / grouping             ("BGVS", "RT MIX TRACKS", "COMS")
    0x2621,   # instrument                    ("Inst 1", "Inst 2", ...)
    0x261D,   # master / bus                  ("DRUMS MSTR", "MUSIC MSTR", ...)
    0x261F,   # VCA-master-shaped             ("vDrums", "vBass", "vLead Vox", "MASTERS ALL")
})
# Enumerated by taking the DIRECT children of the 0x2624 track list across real sessions, rather
# than by collecting the kinds the control corpus happened to contain. The 282-session corpus has
# only three of these; a 22-session sample of real studio work has all six, with 0x2621 in 18
# sessions and 0x261D in 16. All six are master-index targets.
#
# 0x261F ADDED 2026-07-26, by that same criterion, from two drives that were not reachable when the
# list was written. It occurs in ZERO sessions of either corpus used until then, which is why it was
# missed rather than rejected. What is PROVEN: its immediate parent is the 0x2624 track list in
# 290/290 instances; it occupies a row in the 0x2519 name table (553 of its names are entries there,
# and `entries == records` only balances once it is counted); and it carries the full per-track
# apparatus (0x102D -> 0x2619 identity, 0x2015, 0x2434, 0x2580, 0x260A), 527/527.
#
# What is INFERRED, not proven: that it is specifically the VCA master. Its creation names across
# these sessions read `vDrums, vBass, vGtrs, vPerc, vKeys, vStrings, vBrass, vLead Vox, vBGVs, vFX,
# vMASTERS, MASTERS ALL` -- one engineer's `v`-prefix convention over 130-186-track mixes -- and
# 0x263B, already documented as folder/grouping, is ABSENT from all 59 sessions that carry 0x261F.
# A naming convention is not a proof; confirming it needs Pro Tools.
#
# WHY IT MATTERS BEYOND THE COUNT: `_assert_name_table_complete` compares name entries against track
# records, so while 0x261F went uncounted the guard UNDERCOUNTED by 12-13 rows on these sessions and
# would have passed a table missing that many. It is a safety check that was quietly too lenient.

_TRACK_COUNT_CONTAINER = 0x1015


def _adjust_track_count(data: bytes, delta: int) -> bytes:
    """Add `delta` to the `0x1015` AUDIO-track-count `u32` (payload+0), size-neutral.

    0x1015 counts the session's AUDIO tracks (its 0x1014 entries), NOT all tracks -- verified on
    166 PT sessions. Callers pass delta only for an AUDIO edit; a MIDI/click edit passes 0 (its
    count lives in 0x1058, see `_adjust_midi_list_count`). Adjust-by-delta, never recompute, so a
    session whose stored value is off (e.g. our synth's midi9) is preserved rather than 'fixed'
    into a round-trip mismatch."""
    out = bytearray(data)
    for z, _e, c in _size_driven_blocks(data):
        if c == _TRACK_COUNT_CONTAINER:
            cur = int.from_bytes(out[z + 9 : z + 13], "little")
            out[z + 9 : z + 13] = max(0, cur + delta).to_bytes(4, "little")
            break
    return bytes(out)


def _adjust_midi_list_count(data: bytes, delta: int) -> bytes:
    """Add `delta` to the `0x1058` MIDI-track-list count `u32` (payload+0), size-neutral.

    0x1058 is the MIDI analog of 0x1015: its count == the number of 0x1057 MIDI track-list entries
    it holds (PT-verified: 1 MIDI -> 1, 2 MIDI -> 2). A MIDI add/remove must move it, or the dup
    leaves it stale (the count says N while the container holds N+1 0x1057 entries) -- invisible to
    the byte-exact round-trip. Target the 0x1058 that actually holds 0x1057 entries (a session also
    carries a tiny empty 0x1058). Adjust-by-delta for the same reason as 0x1015."""
    out = bytearray(data)
    bl = _size_driven_blocks(data)
    for z, e, c in bl:
        if c == 0x1058 and any(z < zz < e and cc == 0x1057 for zz, _ee, cc in bl):
            cur = int.from_bytes(out[z + 9 : z + 13], "little")
            out[z + 9 : z + 13] = max(0, cur + delta).to_bytes(4, "little")
            break
    return bytes(out)
_COUNT_CONTAINER_TOTAL = frozenset({0x2624})
# Two per-track containers whose child-count `u32` is NOT at payload+0: `0x2107` (child
# `0x210b`) keeps it at payload+9 (z+18); `0x258a` (child `0x2589`) keeps it immediately
# BEFORE the first `0x2589` child (a preamble child precedes the count). `0x258a` appears
# more than once per session; only the instance that actually holds `0x2589` children
# carries the count, so `_count_field_pos` returns None for the empty ones.
_COUNT_CONTAINER_OFFSET = {0x2107: 0x210B, 0x258A: 0x2589}


def _direct_children_of_type(data: bytes, z: int, e: int, child: int, bl) -> "list[int]":
    """Direct (immediate) children of container `[z, e)` whose content_type == `child`, as
    sorted zmarks (a block nested deeper inside another child is not counted)."""
    kids = [(a, b, c) for a, b, c in bl if z < a < e]
    out = []
    for a, b, c in kids:
        if c != child:
            continue
        if any(a2 < a and b2 >= b for a2, b2, _c2 in kids if (a2, b2) != (a, b)):
            continue
        out.append(a)
    return sorted(out)


def _container_actual_count(data: bytes, z: int, e: int, c: int, bl=None) -> int:
    """The child count a count-prefixed container SHOULD carry: for the off-payload
    containers, the number of direct children of its type; for a `_TOTAL` container every
    direct child; else the leading children of its tallied type."""
    if c in _COUNT_CONTAINER_OFFSET:
        if bl is None:
            bl = _size_driven_blocks(data)
        return len(_direct_children_of_type(data, z, e, _COUNT_CONTAINER_OFFSET[c], bl))
    p = z + 13
    if c in _COUNT_CONTAINER_TOTAL:
        n = 0
        while p < e and data[p] == 0x5A:
            sz = int.from_bytes(data[p + 3 : p + 7], "little")
            if p + 7 + sz > e:
                break
            p += 7 + sz
            n += 1
        return n
    child = _COUNT_CONTAINER_CHILD[c]
    n = 0
    while p < e and data[p] == 0x5A and int.from_bytes(data[p + 7 : p + 9], "little") == child:
        p += 7 + int.from_bytes(data[p + 3 : p + 7], "little")
        n += 1
    return n


def _count_field_pos(data: bytes, z: int, e: int, c: int, bl) -> "int | None":
    """Absolute position of the count `u32` for an off-payload container (`0x2107` at
    payload+9; `0x258a` just before its first `0x2589` child), or None if this instance
    carries no such children -- or if the field the convention names lies outside the block.

    An EMPTY `0x2107` is only 10 bytes of payload, so `payload+9` is past its own end. Writing
    there landed 4 zero bytes on the NEXT block's header (`block_type` + half of `size`), which
    broke the top-level chain walk, so `final_index_ref` could no longer find the master index
    and every later pass died on `NoneType.start`. Seen on a real 25-track session
    (`BMG17 Apollo v13AS...`), where BOTH `duplicate_track` and `remove_track` crashed. A count
    field that does not fit inside its own block does not exist: report None rather than write
    outside the block."""
    if _COUNT_CONTAINER_OFFSET[c] == 0x210B:
        return z + 18 if z + 22 <= e else None
    firsts = _direct_children_of_type(data, z, e, 0x2589, bl)
    return firsts[0] - 4 if firsts else None


def _fix_container_counts(data: bytes) -> bytes:
    """Rewrite every count-prefixed container's child-count `u32` to match its actual
    children -- both the payload+0 containers (`0x1015`/`0x1054`/`0x2624`) and the two
    off-payload ones (`0x2107` payload+9, `0x258a` before-first-`0x2589`). Size-neutral (no
    reindex needed). Called at the end of a track add/remove. Idempotent.

    Every write is bounds-checked against the block it belongs to. A count field that would
    extend past its own block's end is not that block's field -- writing it corrupts the NEXT
    block's header and silently breaks the top-level chain (see `_count_field_pos`)."""
    bl = _size_driven_blocks(data)
    out = bytearray(data)
    for z, e, c in bl:
        if c in _COUNT_CONTAINER_OFFSET:
            pos = _count_field_pos(data, z, e, c, bl)
            # The field must lie in the block's PAYLOAD: at or after z+9 (past the 7-byte frame
            # and the content-type word) and ending at or before e. `0x258a` derives its position
            # from its first `0x2589` child (`firsts[0] - 4`), so a container whose first child
            # began at the payload start would aim the write into its own header; measured at
            # +313 minimum across 202 real containers, so this costs nothing and bounds the
            # arithmetic rather than trusting it.
            if pos is not None and z + 9 <= pos and pos + 4 <= e:
                out[pos : pos + 4] = _container_actual_count(data, z, e, c, bl).to_bytes(4, "little")
        elif c in _COUNT_CONTAINER_CHILD or c in _COUNT_CONTAINER_TOTAL:
            if z + 13 <= e:
                out[z + 9 : z + 13] = _container_actual_count(data, z, e, c).to_bytes(4, "little")
    return bytes(out)


def _name_table_shift(data: bytes, z: int) -> int:
    """Extra preamble bytes in a `0x2519` name table, from the `u32` flag at payload+0x0a
    (0 = base, 1 = +2). Both the entry count and the first entry move by this. It is NOT keyed
    by the block's +1 subtype word: sessions with the SAME subtype 9 use both preambles -- a
    synth's entries start at payload+0x14, a real clip-bearing session's at +0x16. Verified on
    436 local sessions (362 base / 74 extended, no exceptions)."""
    return 2 if int.from_bytes(data[z + 9 + 0x0A : z + 9 + 0x0E], "little") else 0


def _name_table_entry_start(data: bytes, z: int, limit: int) -> int:
    """Where the `0x2519` inline entries actually begin, resolved empirically.

    `_name_table_shift` models the preamble as a two-valued flag (base or +2). A third form exists
    in real sessions -- entries at `payload + 0x18` -- so the flag alone leaves the walk reading
    garbage. Stepping forward in 2-byte units to the first offset that parses handles every form
    seen, and the EFFECTIVE shift it implies (`start - (z + 9 + 0x14)`) is what the entry-COUNT
    field's offset must use too: writing the count at the flag's offset put it two bytes early on a
    `+4` session, clobbering a preamble byte.
    """
    q = z + 9 + 0x14 + _name_table_shift(data, z)
    if _name_entry_head(data, q, limit) is None:
        for c in range(q, min(q + 64, max(q, limit - 8)), 2):
            if _name_entry_head(data, c, limit) is not None:
                return c
    return q


def _inline_name_entries(data: bytes, z: int, e: int, bl=None) -> "list[tuple[str, int]]":
    """`(name, name_start)` for each INLINE entry of the `0x2519` name table, in table order.

    Read from the table itself rather than inferred from `track_types`, which does not report
    every entry (a click track has an entry but no reported track), so "is this the last entry"
    must be answered by the table. Entries run `<u32 len><name><23-byte trailer>` from the
    preamble (see `_name_table_shift`) up to the first framed child.
    """
    if bl is None:
        bl = _size_driven_blocks(data)
    end = _name_table_region_end(data, z, e, bl)
    q = z + 9 + 0x14 + _name_table_shift(data, z)
    # `_name_table_shift` models the preamble as a binary flag (base or +2), verified on 436 LOCAL
    # sessions with no exceptions. Real sessions have at least a third form: 105 of 600 start their
    # entries elsewhere -- e.g. `20220503 LAST TIME 2.ptx` begins at payload+0x18, two bytes past
    # the +2 case -- so the head parsed as a garbage length (458752) and the walk returned ZERO
    # entries for a 72-track session. Rather than guess at another flag, resolve the start
    # EMPIRICALLY: if no entry parses where the flag says it should, step forward (in 2-byte units,
    # preserving alignment) to the first offset that does.
    def _run(allow_ctrl: bool) -> "tuple[list[tuple[str, int]], int]":
        p = q
        if _name_entry_head(data, p, end, allow_ctrl) is None:
            for _cand in range(p, min(p + 64, max(p, end - 8)), 2):
                if _name_entry_head(data, _cand, end, allow_ctrl) is not None:
                    p = _cand
                    break
        start = p
        acc: "list[tuple[str, int]]" = []
        while True:
            head = _name_entry_head(data, p, end, allow_ctrl)
            if head is None:
                break
            name, name_end = head
            acc.append((name, p + 4))
            p = _next_name_entry(data, name_end, end, allow_ctrl)
        return acc, start

    out, start = _run(False)
    # The table DECLARES its own entry count, so it can arbitrate its own parse. Two real names
    # carry a raw 0x10 mid-string (`DR PPB \x10OP HAT` -- a `T` in every other copy of the name),
    # and the control-character guard ends the walk there: one session parsed 7 of 143. Simply
    # allowing control bytes is NOT the fix -- measured over 280 sessions it agrees with the
    # declared count on 2 more but on 1 FEWER, and this feeds `_fix_name_table_count`, which
    # WRITES that count. So keep the strict read, and accept the permissive one only when strict
    # disagrees with the declared count and permissive reproduces it exactly. That is 279/280
    # with no session traded away, where strict alone is 277 and permissive alone is 279.
    if _name_table_declared_count(data, z, start) != len(out):
        alt, alt_start = _run(True)
        if alt_start == start and _name_table_declared_count(data, z, alt_start) == len(alt):
            return alt
    return out


def _name_table_declared_count(data: bytes, z: int, start: int) -> int:
    """The `u16` entry count the `0x2519` table states for itself, read at the EFFECTIVE shift
    implied by where the entries actually begin (see `_name_table_entry_start`)."""
    pos = z + 9 + 0x0E + (start - (z + 9 + 0x14))
    return int.from_bytes(data[pos : pos + 2], "little")


def _decode_name(raw: bytes) -> str:
    """Decode a stored name the way Pro Tools wrote it: UTF-8, falling back to latin1.

    Names in a `.ptx` are UTF-8 (see `_name_entry_head`), but almost every reader in this module
    predates that finding and used `latin1`, which turns `135·3BPM` into `135Â·3BPM` -- a string
    whose `.encode()` no longer matches the bytes in the file. That is not cosmetic: the editors
    locate a track by `track_name.encode()`, so a name taken from the public `track_types()` API
    could not be fed back into `duplicate_track`/`remove_track` at all. It failed on 42 of 42
    corpus sessions carrying a non-ASCII name, with "cannot locate track".

    The latin1 fallback keeps every byte sequence decodable, so this is a strict improvement:
    valid UTF-8 (which all-ASCII names trivially are) now round-trips, and anything that is not
    valid UTF-8 behaves exactly as before.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin1", "replace")


def _name_entry_head(data: bytes, q: int, end: int, allow_ctrl: bool = False) -> "tuple[str, int] | None":
    """`(name, name_end)` if a `<len:u32><name>` entry begins at `q`, else None.

    Names are UTF-8, not ASCII. This used to require every byte in `0x20..0x7E`, which rejects
    any accented character -- and macOS stores them DECOMPOSED (NFD), so `Café` is `e` followed
    by U+0301 (`cc 81`). One such clip name ENDED the walk: eight sessions of one project parsed
    29 of 179 entries and were uneditable, because `_assert_name_table_complete` (correctly)
    refuses to edit a table it cannot fully read.

    The test is now "no control characters, and decodes as UTF-8", which is a far stronger
    discriminator against garbage than a printable-ASCII range plus a tight length cap: random
    binary rarely decodes as valid UTF-8. Measured across 288 sessions, this parses 11,937 ->
    14,948 entries, and against each table's OWN declared entry count it agrees on 32 MORE
    sessions while losing NONE. On five sessions it also recovers a first entry the old walk
    skipped (a name with a `·`), so those tables were previously off by one at entry [0]."""
    if q + 4 > end:
        return None
    nlen = int.from_bytes(data[q : q + 4], "little")
    if not (0 < nlen <= _NAME_ENTRY_MAX_BYTES) or q + 4 + nlen > end:
        return None
    name = data[q + 4 : q + 4 + nlen]
    # `allow_ctrl` is the arbitrated fallback (see `_inline_name_entries`), never the default:
    # it still rejects NUL, without which run-of-zero padding parses as a name.
    if any(b == 0x00 for b in name) if allow_ctrl else any(b < 0x20 or b == 0x7F for b in name):
        return None
    try:
        return name.decode("utf-8"), q + 4 + nlen
    except UnicodeDecodeError:
        return None


def _next_name_entry(data: bytes, name_end: int, end: int, allow_ctrl: bool = False) -> int:
    """Where the entry after the one ending at `name_end` starts.

    The suffix is NOT a constant: its ordinal is a `u32` in almost every entry but a `u16` in
    some, so the stride is 23 or 21. Pro Tools emits both -- `robust_audio_mixed12` (an
    unedited PT session) carries a 21-byte gap at entry [1], mid-table, and only 2 of 4362
    corpus entries are short, which is why a hardcoded 23 survived this long. It did real
    damage where it did fire: the walk landed 2 bytes off, failed to parse entry [2], and
    reported a 12-entry table as 2 -- so `_fix_name_table_count` rewrote that session's header
    count 12 -> 2, and PT bounds its read by that count.

    Prefer the long form and fall back to the short one only when long does not land on a valid
    entry; neither landing means there is no next entry.
    """
    q = name_end + _NAME_ENTRY_SUFFIX
    if _name_entry_head(data, q, end, allow_ctrl) is not None:
        return q
    alt = name_end + _NAME_ENTRY_SUFFIX_SHORT
    return alt if _name_entry_head(data, alt, end, allow_ctrl) is not None else q


def _fix_name_table_count(data: bytes) -> bytes:
    """Update the `0x2519` name table's INLINE entry count (`u16` at payload+0x0e, shifted by
    `_name_table_shift`) to match the actual inline name entries, after a track add/remove
    changed them. Pro Tools reads this count to bound the inline entry list; a stale count runs
    the read past the last real entry into the framed children -> end of stream. Size-neutral
    (no reindex). Idempotent on an already-consistent session.

    Walks via `_inline_name_entries` -- this used to keep its own copy of the walk, which is how
    it inherited the fixed-stride bug (see `_next_name_entry`) and wrote a 12-entry table's count
    as 2. One walker, so a stride fix cannot reach the reader and miss the writer."""
    bl = _size_driven_blocks(data)
    out = bytearray(data)
    for z, e, c in bl:
        if c != 0x2519:
            continue
        # EFFECTIVE shift, not the two-valued flag: see `_name_table_entry_start`.
        _end = _name_table_region_end(data, z, e, bl)
        shift = _name_table_entry_start(data, z, _end) - (z + 9 + 0x14)
        n = len(_inline_name_entries(data, z, e, bl))
        cnt = z + 9 + 0x0E + shift
        out[cnt : cnt + 2] = n.to_bytes(2, "little")
    return bytes(out)


def validate(data: bytes) -> "list[dict]":
    """Catch Pro Tools "end of stream" failures BEFORE writing by replicating PT's
    count-driven read of the objects our size-driven reader is too permissive about.
    Returns a list of problem dicts ([] == clean).

    Two gates: (1) `eos_validator.simulate` walks the inline name-entry list (`0x2519`)
    and the track container (`0x2624`) exactly as PT's stream reader does, catching a
    misframed name entry or an over-large container count — the faults that make PT read
    past an object's end; (2) the count-container invariant for the other count-prefixed
    containers (`0x1015` track entries, `0x1054` lanes). `rc == 0` from the reader does
    NOT imply this passes — the reader walks by declared size and tolerates the stale
    inner counts; Pro Tools does not. Sound on every corpus session (no false positives)
    and flags the pre-fix track edits. A clean result is necessary, not proven-sufficient,
    for PT validity."""
    from . import eos_validator as _EOS
    blocks = _size_driven_blocks(data)
    out: "list[dict]" = []
    eos = _EOS.simulate(data, blocks)                     # the ported PT stream walk
    if eos is not None:
        out.append(eos)
    for z, e, c in blocks:                                # count-container invariant (0x1015/0x1054)
        if (c in _COUNT_CONTAINER_CHILD or c in _COUNT_CONTAINER_TOTAL) and z + 13 <= e \
                and data[z + 13] == 0x5A:
            stored = int.from_bytes(data[z + 9 : z + 13], "little")
            actual = _container_actual_count(data, z, e, c)
            if stored != actual:
                out.append({"content_type": c, "zmark": z,
                            "stored_count": stored, "actual_children": actual})
    return out


def _all_ordinal_lists(data: bytes, z: int, e: int) -> "list[dict]":
    """Every `<ALL>` ordinal list inside block `[z, e)`. These drive both 0x202a and 0x202b
    (the two 0x202a blocks are the two size-walk children of the single 0x202b, sharing the SAME
    physical bytes). Layout relative to the ASCII `<ALL>` marker: 5 marker + 7 fixed + 2
    count(u16) + 2 pad + count*2 ordinals(u16) + `fe ff …` trailer. Ordinals are positional
    identity (ord[i] == i). Returns dicts with absolute offsets: count_pos, count, ords_start,
    ords_end."""
    out: "list[dict]" = []
    p = z
    while True:
        a = data.find(b"<ALL>", p, e)
        if a < 0:
            break
        cp = a + 5 + 7
        count = int.from_bytes(data[cp : cp + 2], "little")
        ords_start = cp + 2 + 2
        out.append({"count_pos": cp, "count": count,
                    "ords_start": ords_start, "ords_end": ords_start + 2 * count})
        p = a + 1
    return out


def _fix_all_ordinal_counts(data: bytes) -> bytes:
    """Set each 0x202b `<ALL>` list's u16 count to the name-table entry count after an inline
    splice/insert changed the block; this also fixes the two nested 0x202a lists (same physical
    bytes). Size-neutral, idempotent.

    The count is the NAME-TABLE ENTRY count (tracks incl. a click), NOT the length of the first
    `fe ff`-terminated run. Those coincide only when the `<ALL>` list is a flat `[0..N-1]` (every
    non-click session), which is why "recompute to the first-run length" survived: it gave the
    right value everywhere it was tested. On a click session the list is SEGMENTED -- runs of
    ordinals separated by `fe ff 10 80 <u32> ff ff`, e.g. `[0,1,2] .. [8,9,10,11]` -- so the
    first run is far shorter than the entry count, and recomputing rewrote a PT-authored 6 to 3.
    Across the whole corpus the stored count equals the entry count on ALL 280 sessions (0 equal
    the first-run length but not the entry count), so entry count is the ground truth.

    This also makes duplicate/remove inverse on click sessions: dup inserts the copy's ordinal at
    `ords_end` and remove strips `ords_end-2`, which only line up when the count moves +1/-1 with
    the edit. Pinning the count to the entry count does exactly that (dup: 6->7 entries, remove:
    7->6), where the old recompute pinned it to the first-run length and broke the round-trip."""
    bl = _size_driven_blocks(data)
    z19, e19 = [(z, e) for z, e, c in bl if c == 0x2519][0]
    n_entries = len(_inline_name_entries(data, z19, e19, bl))
    out = bytearray(data)
    for z, e, c in bl:
        if c != 0x202B:
            continue
        p = z
        while True:
            a = out.find(b"<ALL>", p, e)
            if a < 0:
                break
            out[a + 5 + 7 : a + 5 + 9] = n_entries.to_bytes(2, "little")
            p = a + 1
    return bytes(out)


def _fix_name_table_intergroup(data: bytes) -> bytes:
    """Set the 0x2519 framed inter-group `u32` (the count between the two groups of N framed
    0x251A entries) to the actual first-group child count — the unique `fe ff <u32> 5a 0a 00`
    inside 0x2519. Size-neutral, idempotent. (`_fix_name_table_count` fixes only the header
    count u16 at payload+0x0e, a different field.)"""
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    out = bytearray(data)
    z, e = [(z, e) for z, e, c in bl if c == 0x2519][0]
    group1 = len([cz for cz, ce, cc in bl if z < cz < e and cc == 0x251A]) // 2
    seg = bytes(out[z:e])
    m = 0
    while True:
        m = seg.find(b"\xfe\xff", m)
        if m < 0:
            break
        p = m + 2
        if seg[p : p + 3] != b"\x5a\x0a\x00" and seg[p + 4 : p + 7] == b"\x5a\x0a\x00":
            out[z + p : z + p + 4] = group1.to_bytes(4, "little")
            break
        m += 1
    return bytes(out)


# --- BODY positional renumber for a track add/remove -------------------------------
# A duplicate/remove leaves the copy (or the shifted survivors) carrying the WRONG
# per-track position-dependent BODY values: the 0x1014 channel indices, the several per-track
# display ordinals, the 0x261c element-id pool + its k/k-1 counters, the grow-path
# view-scale (0x2104), and the 0x1054 lane order. `remove_track`/`duplicate_track` already
# rebuild the master index canonically; these helpers finish the job in the BODY so the
# result is byte-identical to a fresh synth of the same layout (and the duplicate->remove
# round-trip stays byte-exact). Every field below is a function of the track's PHYSICAL body
# slot AND its CHANNEL COUNT (mono=1, stereo=2): a track at display slot s occupies channels
# [base(s) .. base(s)+kind-1] where base(s) is the cumulative channel sum of the tracks before
# it (all-mono base(s)=s, all-stereo base(s)=2s, mixed=cumulative). Offsets anchor on each
# block's own name_end (which shifts with the name length) and are selected per KIND (the mono
# and stereo 0x1014/0x261c layouts genuinely differ). Gated to synth-shaped sessions
# (`_is_synth_shaped`): a session carrying clips/plugins/automation keeps the no-renumber splice
# path -- its per-track blocks are larger/differently framed, so these fixed offsets don't apply.

# The inner-0x2434 anchor whose +18 u32 holds the 1-based ordinal shared by the 0x200a/0x200b/0x2015
# nested subtree (the same bytes reappear in all three types).
#
# 16 BYTES, NOT 18. The 0x2434 block is `5A 01 00 | 0B 00 00 00 | 34 24` + a 9-byte payload, so the
# two bytes that used to end this literal (`3C 00` = 60) are the LAST u16 OF THE PAYLOAD, not part
# of the frame -- and that value varies: it is 60 in 1958 of Pro Tools' own 0x2015 blocks and 64 in
# the other 622. Matching it made the anchor unfindable in 622/2580 real blocks (24.1%), and every
# call site guards `if a >= 0`, so those tracks were silently skipped -- not read, not audited.
#
# That silence manufactured a false rule. A column with a quarter of its entries missing reads as
# "distinct with GAPS", and that is precisely what the comment further down used to claim (dense in
# only 129 of 200 real sessions, so uniqueness must be the invariant, not density). Measured through
# the 16-byte anchor the column is dense 1..N in 250/250 of those same sessions:
#
#     shape of the ordinal column, 250 real sessions | 18-byte literal | 16-byte anchor
#     dense 1..N                                     |       160       |      250
#     "distinct with gaps"                           |        90       |        0
#
# Where both resolve they agree 4627/4627 and 1958/1958, so this is a strict widening.
_ORD2015_ANCHOR = bytes([0x5A, 0x01, 0x00, 0x0B, 0x00, 0x00, 0x00, 0x34, 0x24, 0x04,
                         0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
# the grow-path 0x2104 edit-window view-scale payload (28 bytes @ block-offset 18); the
# scaffold/donor slots (physical rank < base_n) carry an all-zero payload instead.
_VIEW_SCALE_PAYLOAD = bytes([0xEF, 0xFF, 0xDF, 0xBF, 0xEF, 0xFF, 0xDF, 0xBF, 0x02, 0, 0, 0,
                             0, 0, 0, 0, 0, 0, 0, 0, 0xEF, 0xFF, 0xDF, 0xBF, 0x02, 0, 0, 0])
# per-kind 0x1014 channel-index sites, name_end-relative: (u16 offsets, u32 offsets). The k-th
# channel of a track (k in 0..nchan-1) is written at each listed site + k. Mono is ONE channel
# (SITE2 a u16 at +30); stereo is TWO (SITE2 a u32 at +32/+36) -- the stereo 0x1014 head is 6
# bytes larger for its extra channel record, so the two layouts genuinely differ by kind.
_CH_SITES = {1: ((5,), (30,)), 2: ((5, 7), (32, 36))}
# per-kind 0x261c leaf offsets, name_end-relative: the ten-u32 element-id run, the 160-byte
# present-only free blob, and the C1/C2/C3 k/k-1 counters (C1/C3 == slot..slot+1, C2 == slot).
# The mono 0x261c is tighter, so its fields sit 39 bytes earlier than the stereo ones. C4 is
# NOT here -- it is block-END-anchored (see _B261C_C4_FROM_END).
_B261C = {
    1: {"eid": 979,  "blob": 1064, "c1": 1251, "c2": 1257, "c3": 1435},   # mono
    2: {"eid": 1018, "blob": 1103, "c1": 1290, "c2": 1296, "c3": 1474},   # stereo
}
_B261C_BLOB_LEN = 160
# the fourth 0x261c counter (== slot) sits a FIXED 19 bytes before the block end for BOTH kinds:
# a VARIABLE-LENGTH folder-leaf path string lives between C3 and C4 (its length differs by
# builder and by donor-vs-synth origin within one mixed session), so a name_end offset reads
# garbage -- the block end is the stable anchor.
_B261C_C4_FROM_END = 19


def _no_magic(b: bytes) -> bytes:
    """Keep a free field clear of the 0x5A block magic (the parser scans payloads for it);
    0x5A -> 0x5B is harmless for a present-only field. Same rule the unit synth uses."""
    return bytes(0x5B if x == 0x5A else x for x in b)


def _grow_blob(track_number: int) -> bytes:
    """The 160-byte 0x261c free blob a fresh `synth_stereo_unit`/`synth_mono_unit`
    (`track_number`) emits (a present-only field keyed to the build index). Kind-independent --
    reproduced here so an appended/regenerated track's slot matches a fresh synth byte-for-byte."""
    return _no_magic(bytes([0x00, 0xFF, 0x7F, 0x0A, 0x00])
                     + bytes((track_number * 131 + i * 97) & 0xFF for i in range(155)))


def _gen_eid_base(kind: int, k: int) -> int:
    """The element-id pool base a fresh synth assigns a GENERATED track of channel-count `kind`
    at 1-based build index `k`: stereo pool 10*(k-1)+1, mono pool 10*k+18871 -- the two separate
    per-kind pools `synth_stereo_unit`/`synth_mono_unit` draw from."""
    return 10 * (k - 1) + 1 if kind == 2 else 10 * k + 18871


def _track_kinds(data: bytes) -> "list[int]":
    """Per-track channel count (1=mono, 2=stereo) in display/slot order."""
    return [t.channels for t in track_types(data)]


def _norm_guids(b: bytes) -> bytes:
    """Zero every 8-byte GUID (the bytes after a `2A 00 00 00` tag) so two sessions that differ
    only in their random per-track GUIDs compare equal."""
    out = bytearray(b)
    k = 0
    while True:
        k = out.find(b"\x2a\x00\x00\x00", k)
        if k < 0:
            break
        if k + 12 <= len(out):
            out[k + 4 : k + 12] = b"\x00" * 8
        k += 1
    return bytes(out)


# Content types a pristine `*_inline` synth NEVER emits. Their presence proves the session
# carries real content, so `_is_synth_shaped` can reject it without the expensive rebuild.
# Sound by construction: a true synth has none of these, so it is never wrongly rejected.
_REAL_CONTENT_MARKERS = frozenset({
    0x104F,   # clip placement
    0x2628,   # region
    0x2611,   # insert / plug-in instance
    0x2064,   # plug-in-settings (.tfx) wrapper
})


def _is_synth_shaped(data: bytes) -> bool:
    """True iff `data` is a pristine mono/stereo/mixed `*_inline` synth (mod random GUIDs) -- the
    ONLY shape the fixed-offset positional renumber is derived for. Verified by rebuilding the
    fresh synth of the same audio layout + names and comparing whole-file (GUID-normalized): any
    clips/plugins/automation/click content makes the rebuild differ, so real sessions fall through
    to the no-renumber splice path (which `remove_track` already handles safely). Audio-only
    (mono/stereo); MIDI/aux/etc. are outside the renumber's domain.

    The rebuild is expensive and scales with track count, so a real session is rejected first by
    an O(blocks) scan for content a synth never emits (`_REAL_CONTENT_MARKERS`) -- otherwise every
    edit on a large real session would pay a full synth rebuild just to answer "no".
    """
    tts = track_types(data)
    kinds = [t.channels for t in tts]
    if not tts or any(k not in (1, 2) for k in kinds):
        return False
    try:
        _index_start = _FI.final_index_ref(data).start
    except Exception:
        return False
    for _z, _e, _ct in _raw_block_bounds(data, _index_start):
        if _ct in _REAL_CONTENT_MARKERS:
            return False
    names = [t.name for t in tts]
    try:
        ref = set_track_names(_synth_reference(tuple(kinds)), names)
    except Exception:
        return False
    return _norm_guids(data) == _norm_guids(ref)


@_functools.lru_cache(maxsize=8)
def _synth_reference(kinds: "tuple[int, ...]") -> bytes:
    """The pristine synth for an audio layout, cached: `_is_synth_shaped` rebuilds one per edit,
    and the build dominates its cost. Pure in `kinds` (the random GUIDs differ per build but the
    caller GUID-normalizes before comparing), and `bytes` are immutable, so sharing is safe."""
    if all(k == 1 for k in kinds):
        return synthesize_mono_inline(len(kinds))
    if all(k == 2 for k in kinds):
        return synthesize_stereo_inline(len(kinds))
    return synthesize_mixed_inline(list(kinds))


def _leaf_name_end(blk: bytes) -> "int | None":
    """Block-relative offset just past the first length-prefixed ASCII track name in `blk`
    (a `<u32 len><name>` slot). Every per-track field below anchors on this, so the offsets
    hold for any name length."""
    for k in range(4, len(blk) - 1):
        nlen = int.from_bytes(blk[k - 4 : k], "little")
        if 1 <= nlen <= 32 and all(0x20 <= c < 0x7F for c in blk[k : k + nlen]):
            return k + nlen
    return None


def _blocks_by_type(data: bytes) -> "dict[int, list[tuple[int, int]]]":
    """{content_type: [(zmark, end), ...]} in zmark order, from the size-driven block walk
    (containers physically inline their per-track children, so patching a leaf's bytes also
    fixes every container view of it)."""
    ref = _FI.final_index_ref(data)
    by: "dict[int, list[tuple[int, int]]]" = {}
    for z, e, c in sorted(_raw_block_bounds(data, ref.start)):
        # SKIP THE FILE-HEADER INDEX-POINTER RECORD at body offset 20. It is framed like a block
        # but is not one: the u16 read as its content_type is the low half of the stored index
        # OFFSET, so it takes a different, session-specific value in every file (see the spec).
        #
        # It is not merely noise. When that value happens to equal a type this dict is used to
        # patch, the record is treated as a real block of that type and written to -- and it sits
        # at (20, 31), so an end-anchored field lands in the FILE HEADER. The 0x261C C4 write
        # (`end - 19`) landed on byte 12 and zeroed the four header bytes `0101`, which broke the
        # duplicate->remove inverse on 2 of 213 PT-authored controls. Which sessions are hit is
        # pure luck -- it depends on the low 16 bits of an intermediate index offset -- so this was
        # a latent, data-dependent header corruption for every caller, not a quirk of those files.
        if z < 31:
            continue
        by.setdefault(c, []).append((z, e))
    return by


def _lane_blocks(by: "dict[int, list[tuple[int, int]]]") -> "list[tuple[int, int]]":
    """The real lane-major `0x251A` entries: the children of the name table, and nothing else.

    `_blocks_by_type` is a GLOBAL index, so a `0x251A` framed by a stray `0x5A` inside a leaf
    payload sits in it next to the real lanes. That matters because the caller derives the track
    count by DIVIDING this list (`len // 2`): one phantom shifts the lane-group boundary and
    mis-numbers every ordinal in the second group.

    Measured over 462 corpus sessions: 10 hold a phantom `0x251A` -- all one project family, each a
    290-byte block inside a `0x1038` audio payload. This filter removes **exactly one** entry from
    each of those 10 and nothing at all from the other 434, and the derived count changes in **0 of
    462** -- because in every affected session the phantom count is 1 and the total is odd, so
    `len // 2` happened to absorb it. It is right by luck, and a second stray `0x5A` in audio data
    would end the luck.

    Falls back to the unfiltered list if the containment test finds nothing, so a session whose
    name table is shaped unexpectedly is left exactly as it was rather than re-keyed off an empty
    lane set.
    """
    lanes = by.get(0x251A, [])
    tables = by.get(0x2519)
    if not tables:
        return lanes
    nz, ne = tables[0]
    real = [(lz, le) for lz, le in lanes if nz < lz and le <= ne]
    return real or lanes


def _view_scale_base_n(data: bytes) -> int:
    """The builder's scaffold/donor track count == the number of LEADING 0x2104 blocks whose
    28-byte view-scale payload is all-zero (pure mono/stereo synths carry 8, the mixed donor 2).
    This is the grow-path threshold: slots >= base_n carry the payload, below carry zeros.
    Measured on the pre-edit (canonical) session so an inserted copy can't skew the count."""
    n = 0
    for z, _e in _blocks_by_type(data).get(0x2104, []):
        if bytes(data[z + 18 : z + 18 + 28]) == bytes(28):
            n += 1
        else:
            break
    return n


def _b261c_free_field(body: bytes, z: int, e: int, kind: int) -> "tuple[int, bytes]":
    """(element-id base, 160-byte blob) of the `kind` 0x261c block at `[z, e)`."""
    import struct
    off = _B261C[kind]
    ne = z + _leaf_name_end(body[z:e])
    base = struct.unpack_from("<I", body, ne + off["eid"])[0]
    blob = bytes(body[ne + off["blob"] : ne + off["blob"] + _B261C_BLOB_LEN])
    return base, blob


def _set_b261c_free_field(body: bytearray, z: int, e: int, kind: int,
                          base: int, blob: bytes) -> None:
    """Write a fresh element-id run (base .. base+9) + free blob into the `kind` 0x261c."""
    import struct
    off = _B261C[kind]
    ne = z + _leaf_name_end(bytes(body[z:e]))
    for j in range(10):
        struct.pack_into("<I", body, ne + off["eid"] + 4 * j, (base + j) & 0xFFFFFFFF)
    body[ne + off["blob"] : ne + off["blob"] + _B261C_BLOB_LEN] = blob


def _name_entry_ordinal_slots(body, z: int, region_end: int) -> "list[int]":
    """Byte offset of each `0x2519` inline entry's NAME END, in table order.

    The ordinal for entry i is the `u16` at `slot + 18`. This DELEGATES to the same structural
    walk `_inline_name_entries` uses (`_name_table_shift` for the start, `_next_name_entry` for
    the 23-vs-21-byte stride) instead of scanning for plausible-looking length prefixes.

    The scanning version this replaces capped names at 32 chars and so silently SKIPPED any
    longer one -- a real session whose first entry is the 33-char "Dancing In The Street Macy's
    2017" lost that entry, and every following ordinal was then written one slot early, shifting
    all 38 of them. Two enumerators that disagree about the entry set also made the dense-vs-real
    check answer for a different set than the one being rewritten. One walk now, so they cannot
    drift apart again.
    """
    # Shares `_inline_name_entries`' EMPIRICAL start. The two walks disagreed here for a long time:
    # this one used `_name_table_shift` alone, which does not model a third preamble form, so it
    # returned ZERO slots on 69 of 612 sessions and silently disabled `_repair_next_kind_chain` and
    # the ordinal renumber there -- while this docstring claimed the two could not drift apart.
    #
    # Widening it was tried, REVERTED, and only then landed. On the first attempt the newly-reached
    # sessions made `_rekey_offsynth_positional` write 1257 bytes into 7 untouched Pro Tools files,
    # breaking the no-op invariant this project spent a session establishing. The cause was NOT this
    # walk: `_renumber_name_entry_ordinals` forced the ordinal run to start at 1, and those 7
    # sessions legitimately number from 2 -- a case its "300/300" evidence could not have contained,
    # because those 300 were sessions this very walk could reach. Fixing the renumber to preserve
    # the base is what made widening safe.
    q = _name_table_entry_start(body, z, region_end)
    slots: "list[int]" = []
    while True:
        head = _name_entry_head(body, q, region_end)
        if head is None:
            break
        _name, name_end = head
        if name_end + 20 <= region_end:
            slots.append(name_end)
        q = _next_name_entry(body, name_end, region_end)
    return slots


def _renumber_name_entry_ordinals(body, z: int, region_end: int, su16) -> None:
    """Renumber `0x2519` entry ordinals: the NONZERO ones, in table order, become 1..k.

    Zero is a "not numbered" sentinel and is left alone. Measured over 300 real sessions drawn at
    random from the corpus, the nonzero ordinals in table order are EXACTLY 1..k -- 300/300, no
    exceptions -- while 40.6% of all entries are zero and 40.1% are duplicates of another entry's
    value. Sequences like `[1,2,3,4,0,0,5,0,6,...]` are the normal shape, not corruption.

    This replaces an unconditional 1..n renumber, which treated the zeros as values to overwrite
    and so rewrote PT's own ordinals across 1,608 entries of 25 untouched sessions. Under this
    rule a re-key of an unedited real session is a byte-level no-op, while a synthesized session
    (every ordinal nonzero) renumbers densely exactly as before -- which is why the PT-confirmed
    synth oracles are unaffected. It also needs no pre-edit sampling: an add creates a duplicate
    among the nonzeros and a remove leaves a gap, and renumbering the nonzeros fixes both.

    THE RUN DOES NOT ALWAYS START AT 1. That "300/300" was measured on sessions the SLOT WALK could
    reach, and the walk silently returns nothing on 69 of 612 (see `_name_entry_ordinal_slots`).
    Re-measured across the whole population once the walk was widened::

        reachable before the widening    543/543  nonzero == 1..k
        newly reachable                   27/34   nonzero == 1..k
        newly reachable                    7/34   distinct, but starting at 2: [2,3,4,5,6,...]

    Those 7 are genuine Pro Tools files, and they are exactly the 7 where forcing 1..k rewrote 1257
    bytes of an untouched session. The BASE is preserved now -- the run becomes `base..base+k-1`
    where `base` is the first nonzero already present -- so a session numbering from 2 stays
    numbering from 2. On a session that starts at 1 this is identical to the old behaviour, which is
    every session the rule was originally measured on.

    A first nonzero of 0 is impossible by construction (zeros are skipped), so `base` is >= 1 and a
    synth session, whose ordinals start at 1, renumbers exactly as before.
    """
    slots = _name_entry_ordinal_slots(body, z, region_end)
    base = next((v for v in (int.from_bytes(body[b + 18 : b + 20], "little") for b in slots) if v), 1)
    k = 0
    for b in slots:
        if int.from_bytes(body[b + 18 : b + 20], "little"):
            su16(b + 18, base + k)
            k += 1


def _rekey_positional_by_slot(data: bytes, kinds: "list[int] | None" = None,
                              base_n: "int | None" = None) -> bytes:
    """Re-key every per-track BODY position field to its physical slot's canonical value,
    CHANNEL-COUNT-AWARE: the 0x1014 channel indices (base(s)..base(s)+kind-1 at the per-kind
    sites, base = cumulative channel sum), the 0x200a/0x200b/0x2015 subtree ordinal (s+1), the
    0x2519 name-table ordinals (s+1), the 0x251a lane ordinals ((idx mod N)+1), the 0x2589
    overview index (s), the 0x261c k/k-1 counters (per-kind offsets; C4 block-end-anchored), and
    the 0x2104 grow-path view-scale (payload for slots >= base_n, else zero). Pure function of
    slot -> self-inverse (idempotent on an already-canonical session), so it serves both the add
    and the remove direction. Containers are patched implicitly (they inline the leaves).
    `kinds`/`base_n` default to the session's own (from track_types / the 0x2104 pattern);
    ordinals are kind-independent, so all-stereo stays byte-identical to the prior behaviour."""
    import struct
    by = _blocks_by_type(data)
    body = bytearray(data)
    if kinds is None:
        kinds = _track_kinds(data)
    if base_n is None:
        base_n = _view_scale_base_n(data)
    n = len(by.get(0x261C, []))
    su16 = lambda p, v: struct.pack_into("<H", body, p, v & 0xFFFF)
    su32 = lambda p, v: struct.pack_into("<I", body, p, v & 0xFFFFFFFF)
    chan_base, acc = [], 0                                  # cumulative channel base per slot
    for c in kinds:
        chan_base.append(acc)
        acc += c

    for s, (z, e) in enumerate(by.get(0x1014, [])):        # channel indices (per kind)
        ne = z + _leaf_name_end(body[z:e])
        u16s, u32s = _CH_SITES[kinds[s]]
        for j, o in enumerate(u16s):
            su16(ne + o, chan_base[s] + j)
        for j, o in enumerate(u32s):
            su32(ne + o, chan_base[s] + j)

    for ct in (0x200A, 0x200B, 0x2015):                    # nested subtree ordinal (s+1)
        for s, (z, e) in enumerate(by.get(ct, [])):
            a = body.find(_ORD2015_ANCHOR, z, e)
            if a >= 0:
                su32(a + 18, s + 1)

    if by.get(0x2519):                                     # name-table entry ordinals (s+1)
        z, e = by[0x2519][0]
        inside = [cz for cs in by.values() for cz, _ce in cs if z < cz < e]
        region_end = min(inside) if inside else e
        _renumber_name_entry_ordinals(body, z, region_end, su16)

    for i, (z, e) in enumerate(by.get(0x251A, [])):        # lane ordinals ((idx mod N)+1)
        nlen = struct.unpack_from("<I", body, z + 11)[0]
        ne = z + 15 + nlen
        su16(ne + 18, (i % n) + 1 if n else i + 1)

    for s, (z, e) in enumerate(by.get(0x2589, [])):        # overview index (s, 0-based)
        su16(z + 9, s)

    for s, (z, e) in enumerate(by.get(0x261C, [])):        # 0x261c k/k-1 counters (per kind)
        off = _B261C[kinds[s]]
        ne = z + _leaf_name_end(body[z:e])
        su32(ne + off["c1"], s); su16(ne + off["c2"], s); su32(ne + off["c3"], s + 1)
        su32(e - _B261C_C4_FROM_END, s)                    # C4: block-END anchored (both kinds)

    for s, (z, e) in enumerate(by.get(0x2104, [])):        # grow-path view-scale
        body[z + 18 : z + 18 + 28] = (_VIEW_SCALE_PAYLOAD if s >= base_n else bytes(28))
    return bytes(body)


def _regroup_copy_lanes(data: bytes, source_slot: int, kinds: "list[int]") -> bytes:
    """`duplicate_track` inserts each copy lane right after its source lane, interleaving the
    copy's lanes through the source's (0x1052 in the shared 0x1054). Regroup them so the copy's
    lanes form one consecutive group after the source's last lane -- the order a fresh synth has.
    Lanes are equal-size (v1: copy name == source name length), so this is a size-neutral block
    reorder. `source_slot` is the source track's 0-based display row; `kinds` the after layout's
    per-track channel counts (the source's lanes start at its CUMULATIVE channel base, not
    channels*slot -- the difference that makes it work for mixed sessions)."""
    by = _blocks_by_type(data)
    lanes = by.get(0x1052, [])
    channels = kinds[source_slot]
    start = sum(kinds[:source_slot])                       # cumulative channel base of the source
    grp = lanes[start : start + 2 * channels]
    if len(grp) != 2 * channels:
        return data
    src = [grp[2 * t] for t in range(channels)]            # even positions = source lanes
    cpy = [grp[2 * t + 1] for t in range(channels)]        # odd  positions = copy   lanes
    rs, re = grp[0][0], grp[-1][1]
    return (data[:rs] + b"".join(data[z:e] for z, e in (src + cpy)) + data[re:])


def _capture_donor_free(data: bytes, kinds: "list[int]", base_n: int) -> "list[tuple[int, bytes]]":
    """The scaffold/donor 0x261c free fields (slots 0..base_n-1) of the pre-edit session --
    byte-identical to the donor control and thus to every fresh synth's donor slots. Restored
    verbatim by `_recompute_free_fields` so donor tracks stay byte-exact across an edit."""
    body = bytearray(data)
    b261c = _blocks_by_type(data).get(0x261C, [])
    return [_b261c_free_field(body, z, e, kinds[s])
            for s, (z, e) in enumerate(b261c) if s < base_n]


def _recompute_free_fields(data: bytes, kinds: "list[int]", base_n: int,
                           donor_free: "list[tuple[int, bytes]]") -> bytes:
    """Write every 0x261c block's free field (element-id pool + present-only blob) to its
    fresh-synth value by slot: a scaffold/donor slot (< base_n) keeps the donor's field
    (`donor_free[s]`, captured pre-edit); a generated slot (>= base_n) gets the kind's generated
    pool base `_gen_eid_base(kind, s+1)` + `_grow_blob(s+1)`. This reproduces the two SEPARATE
    per-kind element-id pools a mixed synth assigns (which a positional slide can't), and reduces
    to the pure mono/stereo pattern when every slot is one kind. Both the add and remove direction
    call it; the round-trip is byte-exact because donor slots restore the donor's bytes and
    generated slots restore the by-slot formula (build index k == slot+1 for an identity overview).
    A copy of a donor track (a rare `<base_n` insert) can't be reshaped to a generated block by a
    size-neutral rewrite, so it stays valid (eos-clean, round-trips) but non-canonical."""
    by = _blocks_by_type(data)
    b261c = by.get(0x261C, [])
    body = bytearray(data)
    for s, (z, e) in enumerate(b261c):
        kind = kinds[s]
        if s < base_n and s < len(donor_free):
            base, blob = donor_free[s]
        else:
            base, blob = _gen_eid_base(kind, s + 1), _grow_blob(s + 1)
        _set_b261c_free_field(body, z, e, kind, base, blob)
    return bytes(body)


def _c4_slot_values(data: bytes) -> "list[tuple[int, int]]":
    """`(rank, value)` for every `0x261C` whose "C4" u32 at `end - 19` lands in the record's OWN
    bytes -- the slots `_rekey_offsynth_positional` is willing to write. `rank` is the index into
    ALL `0x261C` records, not into the returned list: records where the field falls inside a child
    block are skipped by the write pass but still consume a rank.
    """
    bl = _size_driven_blocks(data)
    everything = [(z, e) for z, e, _c in bl]
    out: "list[tuple[int, int]]" = []
    for rank, (z, e) in enumerate((z, e) for z, e, c in bl if c == 0x261C):
        t = e - _B261C_C4_FROM_END
        if any(zz > z and ee <= e and zz <= t < ee for zz, ee in everything):
            continue
        out.append((rank, int.from_bytes(data[t : t + 4], "little")))
    return out


def _c4_uses_dense_convention(data: bytes) -> bool:
    """Does this session already store C4 exactly as the re-key writes it -- `value == rank`?

    This is an IDEMPOTENCE test, not a density test, and the difference is not cosmetic. The write
    pass numbers by rank over ALL `0x261C` records but skips those whose field is child-covered, so
    what it emits is `[0, 1, 3]` when rank 2 was skipped. Testing the READ-BACK values for
    `0..k-1` therefore judges the pass's own output non-conforming, which would have made an edit
    stop repairing a session it had itself just written.

    MUST be asked of the PRE-EDIT session. Asking after the splice is what sank every earlier
    attempt to make this conditional: a duplicate leaves a repeated value and a removal a gap, so a
    post-edit test judges a conforming session non-conforming and skips the renumber it needs.
    """
    slots = _c4_slot_values(data)
    return bool(slots) and all(v == rank for rank, v in slots)


def _rekey_offsynth_positional(data: bytes, *, c4_dense: "bool | None" = None) -> bytes:
    """Re-key the per-track POSITIONAL ordinal fields that must stay unique after an off-synth
    track add/remove. Size-neutral and idempotent.

    Pro Tools resolves a track's identity through these ordinals, NOT through the name bytes: an
    off-synth `duplicate_track` produced structurally perfect, distinct names everywhere and PT
    still displayed the copy under the SOURCE's name, then crashed on close (PT-tested
    2026-07-23, 8/8 duplicates rejected). The cause is that `_rekey_positional_by_slot` -- which
    renumbers exactly these fields -- is gated behind `_do_renumber`, so on a real session NOTHING
    was re-keyed and the copy inherited the source's ordinal verbatim.

    Renumbered here (each measured dense in PT-authored sessions, and violated by every duplicate):
      * `0x2519` inline name entries -- `u16` at name_end+18, dense 1..N in TABLE order.
      * `0x251A` lane entries -- `u16` at ne+18 (nlen at z+11, ne = z+15+nlen), lane-major so the
        value is `(i mod N) + 1`; N is read as `len(0x251A)//2` (self-describing: two lanes per
        track, click included) rather than from `track_types`, which omits the click.
    NOT renumbered here, despite being dense in PT-authored sessions:
      * `0x200A` / `0x200B` / `0x2015` -- `u32` at the `_ORD2015_ANCHOR` + 18. See the note at the
        `_ = anchors` line below for why this pass is the wrong place for it.

    Deliberately NOT touched: `0x2589` (attributed by DISPLAY ROW, not file order -- see
    `duplicate_track`), and the `0x261C`/`0x2104` fields, which `_rekey_positional_by_slot`
    rewrites from synth-layout assumptions (`kinds`/`base_n`/`donor_free`) that do not hold on a
    real session."""
    body = bytearray(data)
    by = _blocks_by_type(data)

    def su16(o: int, v: int) -> None:
        body[o : o + 2] = v.to_bytes(2, "little")

    def su32(o: int, v: int) -> None:
        body[o : o + 4] = v.to_bytes(4, "little")

    # 0x200A / 0x200B / 0x2015 are OVERLAPPING VIEWS of ONE per-track ordinal, not three fields:
    # in `multiple track types no click` the 0x200A[1], 0x200B[0] and 0x2015[1] anchors are all the
    # same physical offset (61000). So collect the DISTINCT anchor offsets and number them 1..N in
    # address order. Enumerating each type separately (what `_rekey_positional_by_slot` does) makes
    # the result depend on which loop writes last: 0x200B exists only for AUDIO tracks, so on a
    # mixed session its `s+1` is off by the number of preceding MIDI tracks -- it happens to be
    # overwritten by the 0x2015 pass today, which is luck, not design.
    anchors = set()
    for ct in (0x200A, 0x200B, 0x2015):
        for z, e in by.get(ct, []):
            a = body.find(_ORD2015_ANCHOR, z, e)
            if a >= 0:
                anchors.add(a)
    # PRESERVED, NOT RENUMBERED -- but NOT for the reason this comment used to give.
    #
    # THE VALUE IS A PLAYLIST INDEX: `value[i] = 1 + (number of playlists owned by tracks before i)`,
    # i.e. the 1-based position of this track's FIRST playlist in the session-wide 0x210B list.
    # Proved on `SANTA CLAUS IS COMIN.bak.011.ptx` (36 tracks, 126 0x210B): the column reads
    # [1, 2, 12, 22, ..., 102, 103, ...], the gaps are [1, 10, 10, ..., 10, 1, 1, ...] -- tracks 3-12
    # own ten playlists each -- and `sum(gaps) + 1 == 126 ==` the 0x210B count exactly.
    #
    # TWO EARLIER READINGS OF THIS FIELD WERE WRONG, both from too-narrow corpora:
    #   * "a stable per-track ID, distinct with GAPS where tracks were deleted, so UNIQUENESS is the
    #     invariant" -- an artefact of the 18-byte `_ORD2015_ANCHOR` silently missing 24.1% of real
    #     blocks. A column with a quarter of its entries absent looks exactly like that.
    #   * "dense 1..N, so DENSITY is the invariant" (mine, same day, after fixing the anchor) --
    #     true on 250/250 `/Volumes/Samples` and 255/255 controls, and still wrong: those are all
    #     SINGLE-PLAYLIST sessions, where the playlist rule degenerates to 1..N. Two further drives
    #     show 14 sessions that are not dense, and they are genuine Pro Tools files.
    #
    # NOT RENUMBERED HERE, and deliberately not renumbered at all yet. A duplicate does leave two
    # tracks sharing an ordinal (216/216 control sessions), so this IS violated -- but Pro Tools
    # opened, used and closed such a file cleanly (PT-confirmed 2026-07-26), so it is a fidelity
    # defect, not a crash. Correcting it means writing SURVIVING tracks (every track after the copy
    # shifts by the copy's playlist count), which is the exact class of change reverted repeatedly
    # here, and it cannot be validated locally. The rule to implement, once there is a PT run to
    # check it against: copy gets `value[src] + pc(src)`, every later track gains `pc(src)`, where
    # `pc(src) = value[src+1] - value[src]` (or `n_0x210B + 1 - value[src]` when src is last).
    _ = anchors

    if by.get(0x2519):
        z, e = by[0x2519][0]
        inside = [cz for cs in by.values() for cz, _ce in cs if z < cz < e]
        region_end = min(inside) if inside else e
        _renumber_name_entry_ordinals(body, z, region_end, su16)

    # LANE-MAJOR: the table is `[1..N]` repeated once per lane index, so the run restarts every
    # N = track count. Verified against Pro Tools' own sessions at N = 99, 128 and 512
    # (`512 stereo tracks.ptx` = 1024 ordinals = exactly `[1..512]x2`).
    #
    # ZERO IS A SENTINEL and is preserved, exactly as in the 0x2519 table: only the NONZERO lanes
    # are numbered, 1..k within each lane group. No local control contains a zero here (0 of 213
    # PT-authored files), so this is a byte-level NO-OP on everything available to test against --
    # but 40.6% of external-corpus entries are zero, and renumbering straight through them
    # overwrote Pro Tools' own values. Preserving them cannot be worse than overwriting them, and
    # it matches the measured shape: the nonzeros form ascending runs restarting at 1 (25/25, and
    # 148/149 on the wider sample).
    # PHANTOM-FREE lane list. `by` is a GLOBAL type index, so a `0x251A` framed by a stray 0x5A
    # inside a leaf payload lands in it alongside the real lanes -- and `n` below is derived by
    # DIVIDING that list, so one phantom shifts the lane-group boundary and mis-numbers every
    # ordinal in the second group. The real lanes are children of the name table; nothing else is.
    #
    # Measured over 462 corpus sessions: 10 hold a phantom `0x251A` (all in one project family,
    # each a 290-byte block inside a `0x1038` audio payload). In every one there is EXACTLY ONE
    # phantom and the total is ODD, so `len//2` absorbs it and `n` is right by luck --
    # `n_all == n_clean` in 10 of 10, which is what makes this filter a byte-level no-op on the
    # whole corpus. Two phantoms in one session would not have been survivable, and nothing about
    # a stray 0x5A in audio data promises there will only ever be one.
    #
    # (The spec's note that 4 `0x251A` sit under `0xE196` is the same artifact from the other end:
    # `0xE196` is itself a phantom type, 31/31 nested in `0x1038`.)
    lanes = _lane_blocks(by)
    n = len(lanes) // 2 or 1                    # two lane entries per track, click included
    k = 0
    for i, (z, e) in enumerate(lanes):
        if i % n == 0:
            k = 0                               # new lane group -> restart the run
        nlen = int.from_bytes(body[z + 11 : z + 15], "little")
        pos = z + 15 + nlen + 18
        # CONTAINED, or not written. `nlen` is read out of the block, so a block where +11 is not a
        # name length -- a PHANTOM framed by a stray 0x5A inside a big payload -- yields a wild
        # offset and this wrote two bytes somewhere else entirely in the file. Measured on
        # `M14 BOOM ZAHRAMAY_MIX.bak.047.ptx`: a 290-byte 0x251A at 1977310 read nlen=26403 and the
        # write landed at 2003746, ~1.31 MB inside a 0x1038 audio payload. 23 of 120 sampled
        # sessions on one real corpus were being damaged this way, 2 bytes each.
        #
        # The 0x5A block walk cannot avoid manufacturing phantoms (0x5A is 'Z' and occurs freely in
        # leaf data), so every offset DERIVED from a block's own bytes has to be bounds-checked
        # against that block before it is written -- the same guard the post-ID writes already use.
        if not (z + 15 <= pos and pos + 2 <= e):
            continue
        if int.from_bytes(body[pos : pos + 2], "little") == 0:
            continue                            # not numbered: leave it alone
        k += 1
        su16(pos, k)

    # 0x261C "C4" per-track counter. Block-END anchored, so it is the one 0x261C field whose offset
    # does not depend on the track's kind (C1/C2/C3 sit at per-kind `_B261C` offsets and are NOT
    # re-keyed here). Its value is the AUDIO-ONLY index -- measured on 43 discriminating PT-authored
    # sessions with ZERO exceptions: `multiple track types` (Click, MIDI, Audio 1, Audio 2) stores
    # [0, 1], not the global display slots [2, 3]. A duplicate cloned it verbatim ([0,1,2,2] for
    # four audio tracks), giving two tracks the same identity.
    # ONLY when `end - 19` is really inside the record's OWN bytes. That fixed end-relative offset
    # was derived from synth-shaped sessions, where it always is: 709 of 709 0x261C blocks across
    # the PT-authored controls. In the user's real sessions it is NOT -- 1226 of 1479 (83%) land
    # inside a NESTED CHILD block, so the 4-byte write ran through that child's data (it showed up
    # as writes at `end - 2` of a 0x200B, overrunning its boundary by two bytes) and accounted for
    # BOTH the 0x261C and the 0x200B churn.
    #
    # This needs no theory about what C4 means -- which is still unproven. Writing into a child
    # block is wrong whatever the field is, and containment is checkable. A conditional renumber
    # based on the VALUES was tried earlier and reverted: every predicate mis-read either the
    # post-duplicate or the post-remove shape. Structure settles it where values could not.
    _c261c = by.get(0x261C, [])
    _all = [(z, e) for _t, _lst in by.items() for z, e in _lst]
    _slots = []
    for _s, (z, e) in enumerate(_c261c):
        tgt = e - _B261C_C4_FROM_END
        if any(zz > z and ee <= e and zz <= tgt < ee for zz, ee in _all):
            continue                          # lands in a child: leave Pro Tools' bytes alone
        _slots.append((_s, tgt))
    # ...and even in-record, only when the session actually USES this as an index. In real sessions
    # the slot is ZERO in 622 of 622 records, and no offset anywhere in a real 0x261C's own bytes
    # holds a per-track index (scanned every offset, 40 sessions, no hits). The "audio display
    # index" is an artefact of synth-shaped sessions; writing 0,1,2,... over a real session's zeros
    # was corruption. An all-zero slot set means the session does not use the convention -- and that
    # test survives both post-edit shapes, which is where the earlier value-based predicates failed:
    # a duplicate gives [0,1,2,3,3] and a removal [0,1,3], neither of which is all-zero.
    # ...and only when the session ACTUALLY USES this as a dense index, judged on the PRE-EDIT
    # session by the caller (`c4_dense`). The old test -- "some slot is nonzero" -- was calibrated
    # on one vendor's writer and silently corrupted others. Measured by applying this pass to
    # UNEDITED Pro Tools sessions, where every byte it writes is a rewrite of PT's own value:
    #
    #     corpus                      not a no-op    bytes rewritten
    #     control_files                3/120  (all our own broken fixtures)      6
    #     /Volumes/Samples             0/120                                     0
    #     /Volumes/Mac 4TB SSD       115/120                                18062
    #     /Volumes/Hack 2TB SSD Old   14/120                                 1088
    #
    # /Volumes/Samples reads clean only because THAT writer puts a child block over the target, so
    # the containment guard above skips all 2580/2580 records. That is a property of one vendor's
    # files, not of the format, and it is why this looked safe. On the other drives the slot holds
    # real values (512, 729590018, ...) and they were being overwritten with 0,1,2,... on records
    # belonging to SURVIVING tracks -- the exact class of defect this project keeps reverting.
    #
    # With the pre-edit gate the pass still fires on 65/120 controls that genuinely use the
    # convention, and on 0/120 Mac-4TB and 1/120 Hack sessions (that one is genuinely dense).
    if c4_dense is None:
        c4_dense = any(int.from_bytes(body[t : t + 4], "little") for _s, t in _slots)
    if c4_dense:
        for _s, t in _slots:
            su32(t, _s)
    return bytes(body)


def validate_pt_invariants(data: bytes) -> "list[dict]":
    """Check the structural rules Pro Tools enforces. Returns [] when clean; each entry carries a
    `severity` of "error" or "nonconforming".

    These are NOT guesses. Each was measured across the PT-authored corpus, and every "error" here
    was violated by an edit that `validate()`, a clean reload (`rc=0`) AND a byte-exact
    duplicate->remove round-trip all accepted -- while Pro Tools rejected the file. The round-trip
    only proves `remove . duplicate = identity`, which stays true when both sides are wrong the
    same way; this checks the INTERMEDIATE against ground truth instead.

    **error** -- PT is known to REJECT these (each is calibrated against a real PT verdict):
      * containment: every 0x2589/0x1057/0x210B/0x2620/0x261C lies inside its container. A copy
        spliced at a container's exact end landed OUTSIDE it and CRASHED PT ON LOAD (bug #22).
      * uniqueness of the per-track ordinals (0x2519 entries, 0x251A lanes, the per-track subtree
        field, 0x261C C4) and of track names. Duplicated ordinals are what made PT show a copy
        under the SOURCE's name and then crash on close (bugs #20/#23).
      * 0x2589 view-chain ordinals: one per NAME ENTRY, no repeats. The basis is the name table,
        not `track_types`, which omits the click (bug #19).

    **nonconforming** -- PT-authored sessions are always dense, but PT ACCEPTS gaps, so this is a
    style note rather than a fault. Calibration: our own `12_audio_remove_in_click_session.ptx`
    carries 0x2519 ordinals [1, 3, 4] after a middle track was removed and Pro Tools opened AND
    closed it cleanly. Reporting density as an error would have made this function reject a file PT
    demonstrably accepts.
    """
    out: "list[dict]" = []

    def err(kind: str, detail: str) -> None:
        out.append({"severity": "error", "invariant": kind, "detail": detail})

    def note(kind: str, detail: str) -> None:
        out.append({"severity": "nonconforming", "invariant": kind, "detail": detail})

    bl = _size_driven_blocks(data)
    by = _blocks_by_type(data)

    # PLACEMENT COUNT vs reality. Every placement holder declares its clip count in a u32 that sits
    # immediately before its first wrapper, and `remove_clip` decrements it. Nothing else checked
    # it, and nothing structural could: `placement_lanes` counts wrappers by walking blocks, so a
    # holder whose declared count is stale reads back perfectly while carrying a number Pro Tools
    # would act on. That is the project's recurring failure shape -- every reader says rc=0 and PT
    # is the one that objects.
    #
    # Non-circular by construction, which `_lane_count_pos` is NOT: that helper SEARCHES backwards
    # for a u32 equal to the count it was handed, so it can only ever find a field that already
    # agrees. Measured at the FIXED offset instead: `u32 at (first wrapper - 4)` equals the actual
    # wrapper count in **9423 of 9423** holders across the corpus -- 9235 audio, 185 MIDI, 3 video,
    # 100.00%. So the fixed position is the rule, the backward scan has never once been needed, and
    # this check is inert on any untouched session.
    for _hct, _wct, _kind in _PLACEMENT_CHAINS:
        _wr = sorted(by.get(_wct, ()))
        for _hz, _he in sorted(by.get(_hct, ())):
            _ins = [wz for wz, _we in _wr if _hz < wz < _he]
            if not _ins:
                continue
            _fp = min(_ins)
            if _fp - 4 < _hz:
                continue
            _decl = int.from_bytes(data[_fp - 4 : _fp], "little")
            if _decl != len(_ins):
                err("placement-count",
                    "0x%04x %s holder at %d declares %d placements, %d present"
                    % (_hct, _kind, _hz, _decl, len(_ins)))

    nt = [(z, e) for z, e, c in bl if c == 0x2519]
    if not nt:
        return [{"severity": "error", "invariant": "name-table", "detail": "no 0x2519 block"}]
    entries = _inline_name_entries(data, nt[0][0], nt[0][1], bl)
    names = [n for n, _p in entries]
    n_ents = len(entries)

    # TRACK IDENTITY -- the confirmed crash-on-close cause. Pro Tools gives each track a contiguous
    # block of 10 session-unique ids in its 0x261B. A byte-copied track subtree inherits the
    # SOURCE'S range, and PT then treats the two tracks as ONE object: it shows the copy under the
    # source's name whatever name we wrote, selects both together, and CRASHES ON CLOSE. Measured
    # 2026-07-25: 4 of 4 duplicates carrying a collision crashed on close; 3 of 3 removals, which
    # allocate nothing, closed cleanly.
    _idr = []
    for _z, _e, _c in bl:
        if _c != 0x261B:
            continue
        _r = _track_id_run(data[_z:_e])
        if _r is not None:
            _idr.append(_r[1])
    # DEMOTED to `nonconforming` (2026-07-26). As an ERROR this rejected UNTOUCHED Pro Tools files:
    # 13 of 50 sampled `/Volumes/Samples` sessions, 12 of 50 Mac-4TB, 2 of 60 controls.
    #
    # The detection is GENERATION-DEPENDENT, which is what disqualifies it as an error. `0x261B`
    # has at least two layouts -- the ten-UUID identity list is present in 100% of blocks on two
    # drives and absent from 68% on another -- and `_track_id_run` locates its id run by a signature
    # derived from the UUID-bearing form. Collisions correlate accordingly:
    #
    #     0x261B WITHOUT the UUID list   collide in 13/24  = 54.2%
    #     0x261B WITH    the UUID list   collide in  6/121 =  5.0%
    #     mixed                          collide in  0/62  =  0.0%
    #
    # An 11x difference by layout says most of these are the LOCATOR misfiring on a form it was not
    # derived from -- the same failure as `_ORD2015_ANCHOR`'s 18-byte literal (24.1% of blocks) and
    # `_uuid_list_offset`'s hardcoded `count == 10`.
    #
    # PHANTOMS RULED OUT, unlike the 0x251A lane case where exactly one framed accident per session
    # was the whole story. Every 0x261B has a legitimate track-record parent -- 0x261C 7701,
    # 0x261E 2304, 0x261D 384, no exceptions -- and filtering to record-parented blocks leaves the
    # collision count unchanged at 19/207. So this is the locator misreading REAL blocks of the
    # other layout, not noise being counted.
    #
    # This does NOT weaken the duplicate fix. `_reallocate_duplicate_track_ids` still MAKES the
    # copy's range unique -- that is what Pro Tools confirmed -- and is independent of whether this
    # validator REJECTS a pre-existing file for having collisions. Enforcing on what we write and
    # refusing what PT wrote are different questions, and only the first is settled.
    # MARKER SCAFFOLD MATRIX: every 0x2077 carries one 17-byte 0x2506 row per PLAYLIST (§5c).
    # A track add/remove that does not add/drop the row leaves the matrix inconsistent, which is
    # exactly the defect `sync_marker_playlist_rows` fixes. NOTE, not error: 1 of 386 real PT
    # sessions is legitimately RAGGED (`2 BUSS for Jon.ptx` has two 0x2030 lists whose markers
    # hold 69 and 1 rows), so an error here would reject a file Pro Tools wrote. Report the shape
    # and let the caller judge.
    _mk = [(z, e) for z, e, c in bl if c == 0x2077]
    if _mk:
        _npl = sum(1 for _z, _e, c in bl if c == 0x210B) or sum(1 for _z, _e, c in bl if c == 0x2015)
        _rc = sorted({len(_marker_scaffold_rows(data, z, e)) for z, e in _mk})
        if _npl and _rc != [_npl]:
            note("marker-scaffold-matrix",
                 f"markers hold {_rc} 0x2506 rows but the session has {_npl} playlists -- each "
                 f"marker should carry exactly one row per playlist; a track add/remove that "
                 f"skipped the row leaves it short. (1 of 386 real sessions is legitimately "
                 f"ragged, so this is a note rather than an error)")

    # MANDATED CHILD and TERMINATOR SPAN, both measured over 792,373 checks on untouched corpus
    # sessions with ZERO violations (§5c). A splice that drops a wrapper's child, or that resizes
    # an empty terminator, produces exactly these signatures -- and neither is visible to the
    # count-container or name-table checks above.
    #
    # NOTE severity, not error: 100% on PT-authored files says the rule is real, but it does NOT
    # say Pro Tools REJECTS a file that breaks it, and "error" in this validator means a
    # PT-confirmed rejection. Promote only if PT is observed refusing one.
    for _z, _e, _c in bl:
        _want = _MANDATED_CHILD.get(_c)
        if _want is not None:
            _q, _seen = _z + 9, False
            while _q < _e and data[_q] == 0x5A:
                _sz = int.from_bytes(data[_q + 3 : _q + 7], "little")
                if _sz <= 0 or _q + 7 + _sz > _e:
                    break
                if int.from_bytes(data[_q + 7 : _q + 9], "little") == _want:
                    _seen = True
                    break
                _q += 7 + _sz
            if not _seen:
                note("mandated-child",
                     f"0x{_c:04x} at {_z} has no 0x{_want:04x} child -- every one of the "
                     f"{len(_MANDATED_CHILD)} wrapper types carries its child in 100% of "
                     f"untouched corpus instances, so a missing one means a splice dropped it")
                break
        _span = _TERMINATOR_SPAN.get(_c)
        if _span is not None and _e - _z != _span:
            note("terminator-span",
                 f"0x{_c:04x} at {_z} spans {_e - _z}, not {_span} -- the empty-terminator types "
                 f"are byte-constant in every untouched instance measured")
            break

    # RULER SHAPE and the 0x2011 SINGLE-CHILD rule -- both measured with zero exceptions
    # (645 rulers, 2,040 holders, 240 sessions). Same NOTE-severity reasoning as above.
    for _z, _e, _c in bl:
        _kids, _q = [], _z + 9
        if _c in _RULER_CONTENT or _c in _ONE_2011_HOLDERS:
            while _q < _e and data[_q] == 0x5A:
                _sz = int.from_bytes(data[_q + 3 : _q + 7], "little")
                if _sz <= 0 or _q + 7 + _sz > _e:
                    break
                _kids.append(int.from_bytes(data[_q + 7 : _q + 9], "little"))
                _q += 7 + _sz
        if _c in _RULER_CONTENT:
            _want = _RULER_CONTENT[_c]
            if _kids.count(0x2619) != 1 or _kids.count(_want) != 1:
                note("ruler-shape",
                     f"the 0x{_c:04x} ruler at {_z} does not hold exactly one 0x2619 name and "
                     f"one 0x{_want:04x} content block -- every ruler does, in 645 measured")
                break
        elif _c in _ONE_2011_HOLDERS and _kids.count(0x2011) != 1:
            note("one-2011-child",
                 f"0x{_c:04x} at {_z} holds {_kids.count(0x2011)} 0x2011 children, not 1 -- the "
                 f"{len(_ONE_2011_HOLDERS)} known holders each hold exactly one, 2,040 measured")
            break

    _dupids = sorted({v for v in _idr if _idr.count(v) > 1})
    if _dupids:
        note("track-id-uniqueness",
             f"{len(_dupids)} track id range(s) used by more than one 0x261B: {_dupids[:6]} "
             f"-- a duplicate that SHARES its source's range crashed PT on close, but real PT "
             f"sessions also show collisions here (54% on the layout lacking the 0x261B UUID list, "
             f"5% on the layout carrying it), so most are this locator misreading that form")

    for child, parent in ((0x2589, 0x258A), (0x1057, 0x1058), (0x210B, 0x2107),
                          (0x2620, 0x2624), (0x261C, 0x2624)):
        spans = [(z, e) for z, e, c in bl if c == parent]
        if not spans:
            continue
        for z, e, c in bl:
            if c == child and not any(pz < z and e <= pe for pz, pe in spans):
                err("containment", f"0x{child:04x} at {z} is outside every 0x{parent:04x}")

    if len(set(names)) != len(names):
        err("unique-track-names",
            f"repeated: {sorted({n for n in names if names.count(n) > 1})}")

    def check(kind: str, vals: "list[int]", dense_from: "int | None") -> None:
        # ZEROS ARE EXCLUDED from the uniqueness test. Zero is the "not numbered" sentinel in every
        # one of these tables (40.6% of entries in real sessions), so zeros repeat BY DESIGN --
        # `[1, 2, 0, 0, 3, 4, ...]` is a normal Pro Tools shape, not a defect. Counting them as
        # repeats made this reject untouched real sessions, which is the same false-positive trap
        # the 0x261C-C4 rule fell into. It is the NONZERO values that must be distinct.
        nz = [v for v in vals if v]
        if len(set(nz)) != len(nz):
            err(kind, f"repeated values in {vals[:24]}")
        elif dense_from is not None and nz != list(range(dense_from, dense_from + len(nz))):
            note(kind, f"{vals[:24]} is unique but not dense from {dense_from}")

    # BYTE length, not character count. `_inline_name_entries` returns DECODED names, and names
    # are UTF-8: `Butterbeans Café ...` is one byte longer than its Python length, so `len(n)`
    # aimed this read two bytes early and produced a garbage "ordinal". It surfaced as an
    # ERROR-severity finding on 23 of 440 untouched sessions -- every one of them a session with
    # a non-ASCII name, and ZERO all-ASCII sessions -- i.e. the validator rejecting files Pro
    # Tools itself wrote. Latent until UTF-8 names began parsing at all; the entry walk used to
    # stop at the first accented name, so these entries never reached this line.
    check("0x2519-entry-ordinals",
          [int.from_bytes(data[p + len(n.encode()) + 18 : p + len(n.encode()) + 20], "little")
           for n, p in entries], 1)

    # Only lanes the 0x2519 name table actually contains. The odd counts that made this check reject
    # 12 untouched Pro Tools sessions were PHANTOMS, one per session, and the parent names them:
    #
    #     290 of 291 lanes   parent 0x2519   legitimate
    #       1 of 291         parent 0xe196   not a real content type, and nested in a leaf payload
    #
    # Identical in all four sessions inspected (291, 381, 353, 291 lanes). So `2N+1` was `2N` real
    # lanes plus one framed accident, the lane-major model is CORRECT, and the fix is to stop
    # feeding the check noise rather than to weaken it. `0x5A` is 'Z' and occurs freely in leaf
    # payloads; that is unavoidable, and filtering by PARENT is the cheap structural answer.
    _nt19 = [(z, e) for z, e, c in bl if c == 0x2519]
    lanes = [(z, e) for z, e in by.get(0x251A, [])
             if any(nz < z and e <= ne for nz, ne in _nt19)] or by.get(0x251A, [])
    if lanes:
        nl = len(lanes) // 2 or 1
        lv = [int.from_bytes(
                  data[z + 15 + int.from_bytes(data[z + 11 : z + 15], "little") + 18:][:2],
                  "little")
              for z, _e in lanes]
        # lane-major: the same ordinal list repeated once per lane
        for half in range(0, len(lv), nl):
            check("0x251A-lane-ordinals", lv[half : half + nl], None)
        # An odd count should now be impossible -- the phantom that caused it is filtered by parent
        # above -- but it is still reported rather than asserted, because an unequal split is a
        # statement about THIS model not describing the session, not proof the session is wrong.
        if len(lv) % 2:
            note("0x251A-lane-ordinals",
                 f"{len(lv)} lanes is odd -- cannot be the same run repeated per lane; "
                 f"the lane-major model does not describe this session")
        elif lv[:nl] != lv[nl:]:
            err("0x251A-lane-ordinals", f"{lv} is not the same run repeated per lane")

    anchors = set()
    for ct in (0x200A, 0x200B, 0x2015):
        for z, e in by.get(ct, []):
            a = data.find(_ORD2015_ANCHOR, z, e)
            if a >= 0:
                anchors.add(a)
    # A REPEAT IS AN ERROR -- the only invariant here with a direct Pro Tools crash behind it.
    # Reported 2026-07-27 on a duplicate whose column read [1,2,3,4,4]: "Two tracks named Audio 4
    # and Pro Tools crashes on close." Every stored NAME in that file was correct and distinct, so
    # PT is not displaying a name there: it resolves the track through this ordinal, and two tracks
    # on one slot collapse into a single object.
    #
    # THIS SUPERSEDES THE OLD NOTE HERE, which said "NOT an error: PT LOADS sessions whose per-track
    # subtree ordinals repeat -- our own `mixedmidi_click_*` carry [1,2,5,3,4,3] and were
    # PT-confirmed to open." That observation is TRUE and was incomplete: those files were confirmed
    # to OPEN, and the crash happens on CLOSE. Both reports stand; the conclusion drawn from the
    # first did not.
    #
    # GAPPED-BUT-DISTINCT IS NOT AN ERROR. The value is the index of the track's first playlist, so
    # a multi-playlist session legitimately reads [1, 2, 12, 22, ...] -- `SANTA CLAUS IS COMIN` has
    # 36 tracks and 126 playlists. Real sessions are strictly increasing in 1609/1609 Hack-drive,
    # 250/250 Samples and 255/255 PT-authored controls, and never repeat; only our own output does.
    sub = [int.from_bytes(data[a + 18 : a + 22], "little") for a in sorted(anchors)]
    if len(set(sub)) != len(sub):
        _dups = sorted({v for v in sub if sub.count(v) > 1})
        err("per-track-subtree-ordinal",
            f"{sub} repeats {_dups} -- Pro Tools resolves a track through this ordinal, so two "
            f"tracks on one slot become one object and it crashes on close")
    elif any(b <= a for a, b in zip(sub, sub[1:])):
        note("per-track-subtree-ordinal", f"{sub} is distinct but not increasing")

    # The COUNT is the rule (bug #19: a copy with no chain at all). Repeated display ROWS are only
    # nonconforming -- `mixedmidi_reorder_*` and `midi9` repeat them and PT opens them fine.
    chains = sorted(int.from_bytes(data[z + 9 : z + 11], "little")
                    for z, _e in by.get(0x2589, []))
    # Counted against TRACK RECORDS, not name-table entries. Measured over 120 real sessions the
    # chain count is either 0 (absent -- 58 sessions) or exactly the track-record count (62), with
    # no other value: 120/120. Comparing it to the name-entry count only ever worked because the
    # two usually coincide, and it rejected a real session with 18 entries but 54 tracks and 54
    # chains -- a file Pro Tools opens.
    # DEMOTED to `nonconforming` (2026-07-26), for the same reason the 0x2580 check below was: as an
    # ERROR it rejects UNTOUCHED Pro Tools files. That "120/120" was measured on the corpus reachable
    # at the time; across 131 sessions that carry chains on four drives it holds 124/131, so a hard
    # error fires on 7 real sessions PT itself wrote.
    #
    # NOT caused by `0x261F` joining `_TRACK_RECORD_TYPES`, which was the obvious suspect and was
    # checked: counting the SEVEN kinds scores 124/131 and the six 121/131, and no session matches
    # six-but-not-seven. The seven-kind count is the better predictor, so the remaining 7 have some
    # other cause and the addition made this check slightly MORE accurate, not less.
    _n_trk = sum(1 for _z, _e, _c in bl if _c in _TRACK_RECORD_TYPES)
    if chains:
        if len(chains) != _n_trk:
            note("0x2589-view-chains", f"{len(chains)} chains for {_n_trk} track records")
        elif sorted(chains) != list(range(len(chains))):
            note("0x2589-view-chains", f"{chains[:16]} is not a permutation of range({len(chains)})")

    # DEMOTED to `nonconforming` (2026-07-25). As an ERROR this rejected 119 of 120 UNTOUCHED real
    # Pro Tools sessions -- files PT wrote and opens. Real sessions store ZERO in these slots (622
    # of 622 records measured), which is "repeated values" by definition; gating on all-zero still
    # left 102 of 120 failing, because real sessions mix zeros with a few repeated non-zeros.
    #
    # A rule that fires on 85% of untouched real sessions is a false-positive generator, which is
    # the over-strictness trap this project has now hit four times. The index reading is
    # synth-shaped only: scanning EVERY offset in a real 0x261C's own bytes found no per-track index
    # anywhere (40 sessions, no hits). Kept as a note because it is still true of, and useful for,
    # synth-shaped sessions.
    _c4 = [int.from_bytes(data[e - _B261C_C4_FROM_END : e - _B261C_C4_FROM_END + 4], "little")
           for z, e in by.get(0x261C, [])]
    if len(set(_c4)) != len(_c4):
        note("0x261C-C4", f"repeated values in {_c4[:8]}")     # NONCONFORMING, not an error

    # The `Click II` plug-in descriptor (0x1017 under 0x1018) is present IFF the session has a
    # click track -- 35 sessions with both, 247 with neither, 0 exceptions across all 282.
    # NONCONFORMING rather than an error: removing the last click leaves the descriptor behind,
    # and Pro Tools ACCEPTED exactly that file (09_click_remove, opened and closed cleanly), so
    # PT tolerates the stale entry even though it never emits one.
    names = []
    for z, e in by.get(0x1017, []):
        if z < 31:
            continue
        pl = data[z + 9 : e]
        nl = int.from_bytes(pl[1:5], "little")
        if 0 < nl <= 64:
            names.append(pl[5 : 5 + nl].decode("latin1", "replace"))
    has_desc = "Click II" in names
    has_click = any(n.lower().startswith("click") for n in [n for n, _p in entries])
    if has_desc != has_click:
        note("click-plugin-descriptor",
             f"'Click II' descriptor present={has_desc} but click track present={has_click}")

    # REFERENTIAL INTEGRITY. Added after bug #25 escaped every structural check: the gate verified
    # ordinals and containment but never that a reference RESOLVES. Both hold on 162/162
    # PT-authored sessions.
    try:
        regions = region_list(data)
    except Exception:
        regions = []
    lanes = [(z, e) for z, e, c in bl if c == 0x1052]
    for z, e, c in bl:
        if c != 0x104F or z < 31:
            continue
        # AUDIO placements only (0x1054 > 0x1052 > 0x1050 > 0x104F); the MIDI chain
        # (0x1058 > 0x1057 > 0x1056 > 0x104F) indexes a different list.
        if not any(lz < z and e <= le for lz, le in lanes):
            continue
        ridx = int.from_bytes(data[z + 11 : z + 15], "little")
        if not 0 <= ridx < len(regions):
            err("region-reference",
                f"placement at {z} references region {ridx}, but the session has "
                f"{len(regions)} regions")
    if regions:
        try:
            _audio_file_list(data)
        except Exception as exc:
            err("audio-file-list", f"session has regions but no parseable 0x103A list ({exc})")

    # CROSS-CONTAINER AGREEMENT: a container's stored count must equal the children it frames.
    # The second defect class the gate was blind to (after references) -- every earlier check
    # looked at one value in isolation. All five hold with ZERO mismatches on 162/162 PT-authored
    # sessions. Only the instance that actually frames children is judged: several of these types
    # also appear as small empty duplicates, and a session legitimately stores 0 for a kind it
    # has none of.
    for cont, kids in ((0x1015, (0x1014,)), (0x1058, (0x1057,)), (0x262A, (0x2629,)),
                       (0x1054, (0x1052,)), (0x2624, tuple(sorted(_TRACK_RECORD_TYPES)))):
        for z, e, c in bl:
            if c != cont or z < 31:
                continue
            n = sum(1 for zz, ee, cc in bl if z < zz < e and cc in kids)
            if n == 0:
                continue
            stored = int.from_bytes(data[z + 9 : z + 13], "little")
            if stored != n:
                # NONCONFORMING, not an error. PT-authored sessions agree with ZERO mismatches
                # (162/162), but Pro Tools TOLERATES a disagreement: `mixedmidi_click_*` store
                # 0x1058=1 while framing 2 0x1057 children and were PT-confirmed to open
                # (2026-06-20). Reporting this as an error made the negative-control test fail --
                # the third time an "all PT files do X" observation turned out to be a
                # normalisation rather than a rule PT enforces.
                note("container-count",
                     f"0x{cont:04x} at {z} stores {stored} but frames {n} "
                     f"{'/'.join('0x%04x' % k for k in kids)} children")
            break

    # ORDERING: the per-track records under 0x2624 appear in NAME-TABLE order. Matched by identity
    # GUID (the 0x2619 inside each record), never by name -- a name regex breaks on custom track
    # names and on `.dupN` suffixes, which is how a first attempt at this produced 38 phantom
    # violations. Holds on 153/153 PT-authored sessions that have per-track records.
    # This is the third blind-spot class: the gate could not previously see ORDER at all.
    def _guid_after(z: int, e: int, frm: int) -> "bytes | None":
        k = data.find(b"\x2a\x00\x00\x00", frm, e)
        return data[k + 4 : k + 12] if 0 <= k and k + 12 <= e else None

    eg = []
    for nm, pos in entries:
        # BYTE length -- see the note on the ordinal check above; a decoded name is shorter than
        # its bytes whenever it carries a non-ASCII character.
        _nb = len(nm.encode())
        g = _guid_after(0, pos + _nb + 24, pos + _nb)
        if g:
            eg.append(g)
    rg = []
    for z, e, c in sorted(bl):
        if c not in _TRACK_RECORD_TYPES:
            continue
        for zz, ee, cc in bl:
            if cc == 0x2619 and z < zz < ee <= e:
                g = _guid_after(zz, ee, zz)
                if g:
                    rg.append(g)
                break
    if rg:
        seq = [g for g in eg if g in set(rg)]
        if seq != rg:
            note("record-order",
                 "per-track records are not in name-table order "
                 f"({len(seq)} matched entries vs {len(rg)} records)")
    return out


def _renumber_body_after_duplicate(data: bytes, copy_slot: int, kinds: "list[int]",
                                   base_n: int, donor_free: "list[tuple[int, bytes]]") -> bytes:
    """BODY renumber run at the END of `duplicate_track`. `copy_slot` is the copy's 0-based
    display row (source row + 1); `kinds` the AFTER layout's channel counts; `base_n` +
    `donor_free` captured from the pre-edit session. Regroups the copy's lanes, recomputes every
    track's 0x261c free field to its fresh-synth value (donor slots verbatim, generated slots by
    the per-kind pool formula), then re-keys all pure-positional fields. Result: BODY
    byte-identical to synth(N+1) of the same layout."""
    out = _regroup_copy_lanes(data, copy_slot - 1, kinds)
    out = _recompute_free_fields(out, kinds, base_n, donor_free)
    return _rekey_positional_by_slot(out, kinds, base_n)


def _renumber_body_after_remove(data: bytes, kinds: "list[int]", base_n: int,
                                donor_free: "list[tuple[int, bytes]]") -> bytes:
    """BODY renumber run at the END of `remove_track`, on the already-spliced + reindexed session.
    `kinds` the AFTER layout's channel counts; `base_n` + `donor_free` captured from the pre-edit
    session. Recomputes every survivor's 0x261c free field + re-keys the pure-positional fields to
    its new slot -- the exact inverse of the duplicate renumber, so the round-trip is byte-exact."""
    out = _recompute_free_fields(data, kinds, base_n, donor_free)
    return _rekey_positional_by_slot(out, kinds, base_n)


def _assert_name_table_complete(data: bytes, bl=None, where: str = "appears truncated") -> None:
    """Refuse to edit a session whose 0x2519 walk is demonstrably INCOMPLETE.

    `_inline_name_entries` stops at the first framed child of the name-table block. On 249 of 250
    real sessions that is the whole table, but one (`20230209 GENE GOO GOO DOLLS.ptx`) continues
    PAST that child: its last visible entry's 23-byte trailer runs beyond the child's start and
    another length-prefixed name (`Audio 6`) follows immediately, so the walk sees 18 names for 54
    track records.

    Editing such a session would renumber and splice against a third of its tracks. Until the walk
    handles a table interleaved with child blocks, refuse loudly rather than corrupt quietly --
    every editor here identifies tracks through this table.

    TOLERANCE OF ONE, calibrated to a measured gap rather than chosen to make a number go away.
    Adding `0x261F` to `_TRACK_RECORD_TYPES` raised the record count by 12-13 on the sessions that
    carry it and made 7 of 220 real sessions -- previously editable -- start refusing, every one
    short by EXACTLY 1. The shortfall distribution over 612 sessions on four drives is bimodal with
    nothing in between::

        shortfall   0  ->  562 sessions      exact
        shortfall   1  ->    7 sessions      <- these
        shortfall  20+ ->   42 sessions      genuine truncation, up to 152

    A one-row shortfall is not the failure this guard exists to catch (its motivating case is 18
    names for 54 records) and treating the two as one condition conflates them.

    WHAT IS NOT KNOWN: WHY those 7 are short by one. Either one track record legitimately has no
    display row, or the walk misses a single entry there. An attempt to identify the record by
    matching names was inconclusive -- the name reader used for it is not reliable enough on these
    sessions to trust -- so this is an honest tolerance over an unexplained off-by-one, not a
    diagnosis. What it costs if the cause is a missed entry: that one track cannot be TARGETED by
    name (a clean "not found", not corruption), while the renumbering passes operate on the entries
    that do exist.
    """
    if bl is None:
        bl = _size_driven_blocks(data)
    nt = [(z, e) for z, e, c in bl if c == 0x2519]
    if not nt:
        return
    n_ents = len(_inline_name_entries(data, nt[0][0], nt[0][1], bl))
    n_trk = sum(1 for _z, _e, c in bl if c in _TRACK_RECORD_TYPES)
    if n_ents < n_trk - 1:
        raise ValueError(
            f"name table {where}: {n_ents} entries parsed for {n_trk} track records. Every editor "
            "identifies tracks through this table, so proceeding would operate on a subset of "
            "them. Refused.")


def remove_track(data: bytes, track_name: str) -> bytes:
    """Remove an AUDIO track (with all its clips) from an arbitrary session, leaving every
    other track/clip/plugin/automation intact.

    Splices out the track's COMPLETE per-track block-set: its track-list entries
    (0x1014/0x251A), lane(s) (0x1052, with their placements) in the shared 0x1054, its 0x261C
    playlist subtree (0x261B detail + first view-chain + plugin/routing), its SECOND
    view-chain subtree (0x2589, attributed positionally, in the 0x258A container), its 0x210B
    block (in 0x2107), and its inline 0x2519 name entry — then rebuilds the master index
    (dropping the removed refs + offset-shifting survivors) and re-syncs every container
    child-count (including the 0x258A/0x2107 counts and the name-table entry count). Handles
    AUDIO (0x261C) and MIDI (0x2620) tracks, and a CLICK/aux (0x261E) track by name; the track
    is identified by NAME across the three per-track record containers (audio has a 0x1014
    track-list entry, MIDI a 0x1057 in 0x1058, click none). Unique names. The track's source
    regions/audio linger in the clips bin (harmless). Validated so the per-track block delta
    equals a real session's, the EOS simulator (`validate`) is clean, rc=0, and every other
    block is byte-identical (MIDI dup->remove round-trips byte-exact; PT display-confirm pending)."""
    # Removing the session's ONLY track leaves no track for the scaffolding (the shared 0x1054
    # lane container, the 0x2624 playlist list, the name table) to describe, and the splice
    # emits a session Pro Tools rejects rather than an empty one. Refuse instead of writing a
    # file that fails to open: every 1-track fixture in the local corpus produced an invalid
    # result this way, while 2..512-track removals are clean.
    # Count TRACK RECORDS, not `track_types()`. track_types does not report click/aux tracks, so
    # it returns 0 for `128 click tracks.ptx` -- a genuine 128-track Pro Tools session -- and this
    # guard refused every edit on it with "the session's only track". Any session whose tracks are
    # all of a kind track_types omits was uneditable for the same reason.
    _assert_name_table_complete(data)
    if sum(1 for _z, _e, c in _size_driven_blocks(data) if c in _TRACK_RECORD_TYPES) <= 1:
        raise ValueError(
            "cannot remove the session's only track (a .ptx needs at least one track); "
            "create the replacement track first, or synthesize a new session")
    nb = track_name.encode()
    # BODY positional renumber (synth-shaped mono/stereo/mixed sessions only): capture the
    # pre-edit channel-count layout, the grow-path threshold, and the donor 0x261c free fields so
    # the closing renumber (after the splice) can restore every survivor's by-slot BODY fields --
    # making the result byte-identical to a fresh synth(N-1) and the duplicate->remove round-trip
    # byte-exact. Real sessions (clips/plugins/automation/click) fail `_is_synth_shaped` and keep
    # the no-renumber splice path (the per-track blocks are larger/differently framed there).
    _do_renumber = _is_synth_shaped(data)
    if _do_renumber:
        _kinds_before = _track_kinds(data)
        _base_n = _view_scale_base_n(data)
        _donor_free = _capture_donor_free(data, _kinds_before, _base_n)
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    zend = {z: e for z, e, _c in bl}
    zct = {z: c for z, _e, c in bl}

    def innermost(j: int):
        best = None
        for z, e, c in bl:
            if z < j < e and (best is None or z > best[0]):
                best = (z, e, c)
        return best

    def guids_in(blk: bytes) -> "list[bytes]":
        # a track-list entry stores its 8-byte GUID after a `2A 00 00 00` tag
        out, k = [], 0
        while True:
            k = blk.find(b"\x2a\x00\x00\x00", k)
            if k < 0 or k + 12 > len(blk):
                break
            # SKIP offset 3: that is the block's own SIZE field, and a block whose declared size is
            # exactly 42 spells `2a 00 00 00` there. The bytes that follow are then the CONTENT-TYPE
            # word, not a GUID -- and `transform` rewrites whatever this returns. On a real session
            # that turned two copied `0x251A` lane blocks into type `0x7F40`, so the block walk no
            # longer enumerated them as lanes and `remove_track` could not find the track it had
            # just created. The tag never legitimately sits in the size field.
            if k != 3:
                out.append(blk[k + 4 : k + 12])
            k += 1
        return out

    ident: "set[int]" = set()          # zmarks of the track's blocks
    nt_ranges: "list[tuple[int, int]]" = []  # name-table string byte-ranges (not blocks)
    seed_guids: "set[bytes]" = set()
    pos = 0
    while True:
        j = data.find(nb, pos)
        if j < 0:
            break
        if j >= 4 and int.from_bytes(data[j - 4 : j], "little") == len(nb):
            b = innermost(j)
            if b:
                z, e, c = b
                if c in (0x1014, 0x1057, 0x251A):        # track-list entry: seed the GUID
                    # 0x1014 = audio track-list entry, 0x1057 = MIDI track-list entry (in 0x1058);
                    # both carry the name + a GUID after a 2A tag. A MIDI 0x1057's GUID differs
                    # from its identity GUID, so seeding it too catches the 0x1057 itself.
                    ident.add(z)
                    seed_guids.update(guids_in(data[z:e]))
                elif c == 0x1052:                       # a lane (with its placements)
                    ident.add(z)
                elif c == 0x210B:                       # per-track 0x210B (name + playlist GUID)
                    ident.add(z)
                    seed_guids.update(guids_in(data[z:e]))
                elif c == 0x2519:                       # a whole inline name entry
                    span = _name_entry_span(data, j, bl)  # handles the short-trailer LAST entry
                    if span:
                        nt_ranges.append(span)
        pos = j + 1
    # link name -> GUID -> the 0x102D that carries it -> its enclosing per-track record. The
    # record container is 0x261C (audio), 0x2620 (MIDI), or 0x261E (click/aux) -- all siblings
    # under the 0x2624 track-list, each nesting one 0x102D (whose 0x2619 holds the identity GUID).
    pl_ranges: "list[tuple[int, int]]" = []
    for z, e, c in bl:
        # 0x102D for audio/MIDI/click; 0x2619 for the folder kind (0x263B), whose identity is a
        # bare 0x2619 with no 0x102D wrapper -- keying only on 0x102D made those tracks
        # unlocatable even once 0x263B was in the container set.
        if c in (0x102D, 0x2619) and any(g in data[z:e] for g in seed_guids):
            cs = [(cz, ce) for cz, ce, cc in bl
                  if cc in _TRACK_RECORD_TYPES and cz < z < ce]
            if cs:
                pz, pe = max(cs)
                pl_ranges.append((pz, pe))
    # EXACTLY ONE record, or nothing -- see duplicate_track's note. Here the stakes are higher: a
    # seed that matched every record would have REMOVED every track. `Audio 42` in any session with
    # at least 42 tracks matched all of them, because `2A 00 00 00` is both the GUID tag and the
    # number 42.
    pl_ranges = list(dict.fromkeys(pl_ranges))
    if len(pl_ranges) == 1:
        _pz, _pe = pl_ranges[0]
        ident |= {zz for zz, _ee, _cc in bl if _pz <= zz < _pe}
    else:
        pl_ranges = []
    if not pl_ranges:
        # POSITIONAL FALLBACK -- the mirror of duplicate_track's. A track's display name (0x2519 /
        # 0x251A / 0x1014) can differ from the name its 0x102D -> 0x2619 record was created with,
        # and those records may carry no GUID, so neither the name search nor the GUID seed finds
        # anything. The Nth name-table entry is the Nth track record: validated at 225/225 against
        # the name/GUID route on sessions where that route resolves. Fallback only.
        _ntab = [(z, e) for z, e, c in bl if c == 0x2519]
        _ents = ([n for n, _q in _inline_name_entries(data, _ntab[0][0], _ntab[0][1], bl)]
                 if _ntab else [])
        _recs = sorted((zz, ee) for zz, ee, cc in bl if cc in _TRACK_RECORD_TYPES)
        if track_name in _ents and len(_ents) == len(_recs):
            pz, pe = _recs[_ents.index(track_name)]
            pl_ranges.append((pz, pe))
            ident |= {zz for zz, _ee, _cc in bl if pz <= zz < pe}
    if not pl_ranges:
        raise ValueError(
            f"cannot locate track {track_name!r} (not found, or an unsupported track type)")

    # The track's SECOND view-chain subtree (0x2589) is attributed by the block's OWN display-row
    # ordinal (u16 at zmark+9), NOT by zmark rank. The blocks are not stored in display order --
    # a real session's zmark order reads [2, 0, 1, 3] -- so the old rank-based pick selected
    # another track's chain. That is not a cosmetic slip: each 0x2589 NESTS its children
    # (0x2037/0x203a/0x2580), so one wrong pick silently deleted ~18 blocks / 268 bytes of a
    # SURVIVING track's view state, and eos/rc/validate() all still passed.
    #
    # Only attribute when the ordinals form a clean 0..N-1 map onto the tracks. A session whose
    # 0x2589 set does not correspond 1:1 to its tracks (e.g. 4 chains for 3 tracks) gets none
    # removed: a stale extra view chain is harmless (PT recomputes view state), deleting a live
    # one is not. The old `len(s2589) == len(playlists)` guard was a weak proxy that could pass
    # by coincidence and then delete the wrong block.
    # ...but ONLY on a synth-shaped session, where the mapping is proven (the dup/remove pair is
    # byte-identical to a fresh synth there). A real session's 0x2589 set does NOT correspond 1:1
    # to `track_types` -- one fixture carries 4 chains for 3 tracks, i.e. display rows exist that
    # track_types never reports (master/aux) -- so no track-keyed attribution is sound. Leaving a
    # stale chain is harmless (PT recomputes view state); deleting a live one silently destroyed
    # ~18 blocks of a SURVIVING track's state while eos/rc/validate() all passed.
    s2589 = [(z, int.from_bytes(data[z + 9 : z + 11], "little"))
             for z, _e, c in bl if c == 0x2589]
    if s2589 and pl_ranges and _do_renumber:
        rows = [t.name for t in track_types(data)]
        if sorted(o for _z, o in s2589) == list(range(len(rows))) and track_name in rows:
            row = rows.index(track_name)
            for z2589, ordinal in s2589:
                if ordinal == row:
                    ident.add(z2589)
                    break

    # Off the synth path, the EXACT INVERSE of duplicate_track's chain handling: attribute against
    # the NAME-TABLE entries (display rows -- a click has one, `track_types` does not report it),
    # drop the removed track's chain, and shift every chain BELOW it up one so the ordinals stay
    # `range(name entries)`. See duplicate_track for why the track_types basis was unsound and why
    # skipping (the old behaviour) silently violated that PT invariant.
    _rm_view_row: "int | None" = None
    _nt_b = [(z, e) for z, e, c in bl if c == 0x2519]
    if s2589 and pl_ranges and not _do_renumber and _nt_b:
        ents = [n for n, _p in _inline_name_entries(data, _nt_b[0][0], _nt_b[0][1], bl)]
        if sorted(o for _z, o in s2589) == list(range(len(ents))) and track_name in ents:
            row = ents.index(track_name)
            for z2589, ordinal in s2589:
                if ordinal == row:
                    ident.add(z2589)
                    _rm_view_row = row
                    break

    # ---- INLINE per-track splices (byte-ranges added to nt_ranges, NOT whole blocks) ----
    # (a) 0x202a/0x202b <ALL> ordinal lists: drop the LAST 2-byte ordinal of each (identity
    #     means "last" == the removed rank). Operate on the 0x202b container so both nested
    #     0x202a lists (same physical bytes) are covered; the container shrink handles the sizes.
    for z, e, c in bl:
        if c == 0x202B:
            for lst in _all_ordinal_lists(data, z, e):
                nt_ranges.append((lst["ords_end"] - 2, lst["ords_end"]))
    # (b) 0x2519 header ordinal: ONLY when the removed track was the LAST header entry (its ord
    #     was the special-last 2-byte form). Removing it strands the NEW last survivor with a
    #     full 4-byte ordinal -> truncate to 2 bytes. A non-last removal leaves the special-last.
    # "Last" means last INLINE NAME-TABLE ENTRY, read from the table itself -- NOT
    # `track_types(data)[-1]`. track_types does not report every entry (a click track is absent),
    # so on a click-bearing session removing the last *reported* track looked last-by-model while
    # the table continued past it: this truncated a survivor's ordinal by 2 bytes that nothing
    # ever restored, shrinking the 0x2519 block (768 -> 766) across a duplicate->remove.
    z2519, e2519 = [(z, e) for z, e, c in bl if c == 0x2519][0]
    _entries = _inline_name_entries(data, z2519, e2519, bl)
    if _entries and _entries[-1][0] == track_name and len(_entries) > 1:
        prev_name, prev_name_pos = _entries[-2][0], _entries[-2][1]
        ord_pos = prev_name_pos + len(prev_name.encode()) + 18   # name_end +6 fixed +4 tag +8 guid
        nt_ranges.append((ord_pos + 2, ord_pos + 4))

    # (c) `next_kind` chain repair -- the exact INVERSE of duplicate_track's. Entry i's `next_kind`
    # u16 names the KIND of entry i+1 (0 audio / 1 MIDI / 2 click / 5 folder-VCA / 7 instrument /
    # 11 master-bus, shifted << 8) -- validated 3379/3379 consecutive pairs over 40 real corpus
    # sessions. Removing a NON-LAST entry leaves its predecessor pointing at whatever followed the
    # removed one, which is precisely the removed entry's OWN next_kind, so the repair is a copy.
    # (Removing the LAST entry is handled by the truncation above: the predecessor becomes last and
    # loses the field entirely.) Without this, duplicate->remove stopped inverting byte-exactly --
    # the predecessor kept naming the copy's kind after the copy was gone.
    _idx = next((i for i, (n, _q) in enumerate(_entries) if n == track_name), None)
    if _idx is not None and 0 < _idx < len(_entries) - 1:
        _pn, _pp = _entries[_idx - 1]
        _cn, _cp = _entries[_idx]
        _dst = _pp + len(_pn.encode()) + 20      # predecessor's next_kind (name_end + 18 + 2)
        _src = _cp + len(_cn.encode()) + 20      # the removed entry's next_kind
        _d = bytearray(data)
        _d[_dst : _dst + 2] = data[_src : _src + 2]
        data = bytes(_d)

    # outermost removed blocks (a subtree parent covers its children) + the name-table entries
    ranges = [(z, zend[z]) for z in ident
              if not any(zz < z and zend[zz] >= zend[z] for zz in ident if zz != z)]
    ranges = sorted(ranges + nt_ranges)
    merged: "list[tuple[int, int]]" = []
    for z, e in ranges:
        if merged and z <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((z, e))
    ranges = merged

    def shift(o: int) -> "int | None":
        s = 0
        for z, e in ranges:
            if e <= o:
                s += e - z
            elif z < o < e:
                return None                              # inside a removed range
        return o - s

    body = bytearray()
    prev = 0
    for z, e in ranges:
        body += data[prev:z]
        prev = e
    body += data[prev : ref.start]
    # Inverse of duplicate_track's chain fixup: with the removed track's chain gone, every chain
    # BELOW its display row moves up one, keeping ordinals == range(name entries).
    if _rm_view_row is not None:
        for _z2589, _ordv in s2589:
            if _ordv > _rm_view_row:
                _nz = shift(_z2589)
                if _nz is not None:
                    body[_nz + 9 : _nz + 11] = (_ordv - 1).to_bytes(2, "little")
    for z, e, _c in bl:                                  # shrink enclosing containers
        if z in ident:
            continue
        inside = sum((re - rz) for rz, re in ranges if z < rz and e >= re)
        if inside:
            nz = shift(z)
            if nz is not None:
                body[nz + 3 : nz + 7] = (
                    int.from_bytes(body[nz + 3 : nz + 7], "little") - inside
                ).to_bytes(4, "little")

    recs = _FI.parse_records(ref.data, set(zct.values()), set(zct))
    def _in(o: int) -> bool:
        return any(z <= o < e for z, e in ranges)
    for r in recs:
        r.child_refs = [c for c in r.child_refs if not _in(c.offset)]
        for c in r.child_refs:
            c.offset = shift(c.offset)
        new_elems = []
        for el in r.elements:
            offs = [shift(o) for o in el.offsets if not _in(o)]
            if offs:
                el.offsets = offs
                new_elems.append(el)
        r.elements = new_elems
    # Pruning + offset-shift above is INCOMPLETE: it leaves the removed track's now-empty
    # per-track records in place and strands the two per-record counts that encode the OLD
    # track count. `rebuild_after_track_drop` drops the dead records and re-ranks the
    # track-count ordinal (the packed-offset table's `tag1` count is derived at serialize
    # time), making the whole index byte-identical to synth(new_count).
    recs = _FI.rebuild_after_track_drop(recs)
    out = _set_index_offset(bytes(body) + _FI.serialize_final_block(recs))
    out = _fix_all_ordinal_counts(out)                   # <ALL> u16 counts (0x202a/0x202b)
    out = _fix_name_table_intergroup(out)                # 0x2519 inter-group u32
    out = _fix_name_table_count(_fix_container_counts(out))
    # BODY renumber (synth-shaped): recompute every survivor's 0x261c free field to its
    # fresh-synth value (donor slots verbatim, generated slots by the per-kind pool formula) and
    # re-key its pure-positional fields to the new physical slot -- the exact inverse of the
    # duplicate renumber, so the round-trip stays byte-exact and the result equals synth(N-1).
    if _do_renumber:
        out = _renumber_body_after_remove(out, _track_kinds(out), _base_n, _donor_free)
    # 0x1015 counts AUDIO tracks (its 0x1014 entries), NOT all tracks -- verified on 166 PT
    # sessions (e.g. `multiple track types`: 2 audio + 1 MIDI -> 0x1015 = 2; every MIDI-bearing
    # session has 0x1015 == the 0x1014 count). So adjust it ONLY for an audio edit; removing a
    # MIDI/click track leaves it unchanged. (midi9's 0x1015 = 9 with zero audio is a synth
    # artifact, which is what the old "0x1015 = all tracks" reading was mistakenly based on.)
    audio_edit = any(zct.get(z) == 0x1014 for z in ident)
    midi_edit = any(zct.get(z) == 0x1057 for z in ident)
    out = _adjust_track_count(out, -1 if audio_edit else 0)    # 0x1015 = audio count
    if midi_edit:
        out = _adjust_midi_list_count(out, -1)                 # 0x1058 = MIDI count
    if not _do_renumber:
        # `c4_dense` is judged on the PRE-EDIT session: after the splice the column reads
        # [0,1,2,3,3] / [0,1,3], and judging THAT is what sank every earlier conditional renumber.
        out = _rekey_offsynth_positional(out, c4_dense=_c4_uses_dense_convention(data))
        # ...and close the gap the removed track leaves in the 0x2015 playlist-ordinal column, the
        # exact inverse of the repair `duplicate_track` now applies by default. Pro Tools resolves
        # a track through this ordinal -- a session with two tracks on slot 4 crashed on close --
        # so the column must stay a running total with no holes.
        out = repair_playlist_ordinals_after_remove(data, out)
    # ...and drop the removed playlist's 0x2506 row from every marker, the exact inverse.
    out = _sync_marker_rows_for_playlist_delta(data, out)
    return out


def _unique_same_length_name(length: int, taken: "set[str]") -> str:
    """A printable-ASCII track name of EXACTLY `length` bytes that is not in `taken`.

    Used as an intermediate placeholder for a variable-length duplicate: the same-length
    duplicate path runs byte-for-byte as usual, then `rename_track` changes the placeholder to
    the real (any-length) name. With `length >= 1` and a 36-char alphabet the single-char-repeat
    sweep (36 candidates) effectively always finds one; the product fallback covers the
    pathological case where every simple name is already used."""
    import itertools
    import string
    if length <= 0:
        raise ValueError("track name must be at least one byte")
    alphabet = string.ascii_uppercase + string.digits
    for ch in alphabet:                       # "QQ", "RR", ... — readable, almost always free
        cand = ch * length
        if cand not in taken:
            return cand
    head = min(length, 5)
    for combo in itertools.product(alphabet, repeat=head):
        cand = "".join(combo) + alphabet[0] * (length - head)
        if cand not in taken:
            return cand
    raise ValueError("could not find a unique same-length placeholder name")


_TRACK_ID_RUN = 10          # every 0x261B carries exactly one run of 10 consecutive u32 ids


def _track_id_run(blk: bytes, minlen: int = 4) -> "tuple[int, int, int] | None":
    """`(offset, first_id, length)` of a 0x261B's consecutive-u32 id run, or None.

    Located by SIGNATURE rather than a fixed offset: the longest sequence at 4-byte stride whose
    values ascend by exactly one. Measured over 3962 `0x261B` blocks in 67 control sessions, every
    block has such a run and its length is ALWAYS 10.
    """
    best = None
    i, n = 0, len(blk)
    while i + 4 <= n:
        v = int.from_bytes(blk[i : i + 4], "little")
        k = 1
        while i + 4 * (k + 1) <= n and int.from_bytes(blk[i + 4 * k : i + 4 * k + 4], "little") == v + k:
            k += 1
        if k >= minlen and (best is None or k > best[2]):
            best = (i, v, k)
        i += 4 * k if k > 1 else 1
    return best


def track_id_ranges(data: bytes) -> "list[tuple[int, int, int]]":
    """`(zmark, first_id, length)` per 0x261B — the per-track identity ranges Pro Tools allocates."""
    out = []
    for z, e, c in _size_driven_blocks(data):
        if c != 0x261B:
            continue
        r = _track_id_run(data[z:e])
        if r is not None:
            out.append((z, r[1], r[2]))
    return out


def _reallocate_duplicate_track_ids(data: bytes, only_first: "int | None" = None,
                                    keep: int = 1) -> bytes:
    """Give any 0x261B that SHARES another's id range a fresh one past the session high-water mark.

    Pro Tools allocates each track a contiguous block of 10 session-unique ids (`13221..13230`,
    `13231..13240`, ...) stored in its `0x261B`. `duplicate_track` byte-copies the source subtree,
    so the copy inherited the SOURCE'S ENTIRE RANGE. Pro Tools then treats the two tracks as ONE
    object: it displayed the copy under the source's name no matter what name we wrote (a session
    whose name table read [Audio 1..5] showed `Audio 1,2,3,4,4`), selecting either selected BOTH,
    and it CRASHED ON CLOSE. Confirmed on 4 of 4 duplicates tested in Pro Tools 2026-07-25, while
    all 3 removals -- which allocate nothing -- were clean.

    Renaming could never have fixed this: the display name is resolved through the id, not the
    string, and every name string was already correct in all six places PT writes it.

    `only_first` restricts the repair to ONE range -- the one the current edit duplicated. An edit
    must not silently renumber a collision that was already in the source: doing so broke the
    duplicate->remove byte-exact inverse (remove cannot undo a renumber of a range it never
    touched), and that inverse is one of this project's few real oracles. Synthesis passes None,
    because there the collision IS ours to fix at creation time.

    Idempotent, and a no-op on any session whose ranges are already disjoint (215/225 controls; the
    10 exceptions are our own click/synth output plus two PT re-saves that inherited it). Same-length
    u32 rewrites only, so no offsets move.
    """
    bl = _size_driven_blocks(data)
    ranges = []
    for z, e, c in bl:
        if c != 0x261B:
            continue
        r = _track_id_run(data[z:e])
        if r is not None:
            ranges.append((z, e, r[1], r[2]))
    if not ranges:
        return data
    hi = max(first + length - 1 for _z, _e, first, length in ranges)
    seen: "set[int]" = set()
    out = bytearray(data)
    kept: "dict[int, int]" = {}
    for z, e, first, length in ranges:
        allowed = keep if (only_first is not None and first == only_first) else 1
        kept[first] = kept.get(first, 0) + 1
        if kept[first] <= allowed:
            seen.add(first)
            continue
        if only_first is not None and first != only_first:
            continue                            # a pre-existing collision: not this edit's to fix
        new_first = hi + 1                      # past every id the session has handed out
        hi += length
        for p in range(z, e - 3):
            v = int.from_bytes(out[p : p + 4], "little")
            if first <= v < first + length:
                out[p : p + 4] = (new_first + (v - first)).to_bytes(4, "little")
        seen.add(new_first)
    return bytes(out)


def _uuid_list_offset(blk: bytes) -> "int | None":
    """Offset of a `0x261B`'s per-track UUID list: a `u16` count of 10 followed by ten DISTINCT
    16-byte ids. Located by signature, not a fixed offset (+1100 in the control ladder, but nothing
    guarantees that elsewhere).

    These are identities Pro Tools resolves a track through, alongside the integer id range. They
    are stable per track -- tracks 1..3 carry the same ten in both the 4-track and 5-track control
    sessions -- and DISTINCT across tracks: 50/50 and 40/40 in those files.
    """
    n = len(blk)
    for off in range(0, max(0, n - 162)):
        if int.from_bytes(blk[off : off + 2], "little") != 10:
            continue
        u = [blk[off + 2 + 16 * k : off + 18 + 16 * k] for k in range(10)]
        if len(set(u)) == 10 and all(any(x) for x in u):
            return off
    return None


def _reuuid_copied_track(data: bytes, zb: int, eb: int) -> bytes:
    """Give the copied track's `0x261B` ten FRESH UUIDs, unique within the session.

    duplicate_track byte-copies the source subtree, so the copy inherited all ten of the source's
    UUIDs. Pro Tools STILL treated the two tracks as one object even after the integer id range was
    made unique -- reported from Pro Tools on a file whose ranges were already disjoint: the copy
    displayed under the SOURCE's name, both selected together, crash on close.

    New values are derived deterministically (increment the leading u32 until unused) so runs stay
    reproducible; only uniqueness matters, and PT's own tracks carry unrelated ids.
    """
    blk = data[zb:eb]
    off = _uuid_list_offset(blk)
    if off is None:
        return data
    seen = set()
    for z, e, c in _size_driven_blocks(data):
        if c != 0x261B:
            continue
        o2 = _uuid_list_offset(data[z:e])
        if o2 is not None:
            seen.update(bytes(data[z + o2 + 2 + 16 * k : z + o2 + 18 + 16 * k]) for k in range(10))
    import hashlib
    out = bytearray(data)
    for k in range(10):
        a = zb + off + 2 + 16 * k
        cur = bytes(out[a : a + 16])
        # Derive a WHOLLY different 16 bytes, not an increment of the leading u32. Incrementing
        # leaves 12 of 16 bytes equal to the source's; the values are still unique, but Pro Tools'
        # own sibling tracks differ across the entire id and there is no reason to hand it a
        # near-identical one. Hashed so the result is deterministic and runs stay reproducible.
        n = 0
        new = hashlib.sha256(cur + b"ptx-dup" + bytes([n])).digest()[:16]
        while new in seen:
            n += 1
            new = hashlib.sha256(cur + b"ptx-dup" + bytes([n])).digest()[:16]
        seen.add(new)
        out[a : a + 16] = new
    return bytes(out)


def _dense_index_offsets(data: bytes, ctype: int) -> "list[tuple[int, int]]":
    """Byte offsets inside every block of `ctype` whose values across those blocks form a DENSE
    per-track index -- `0..N-1` or `1..N` -- returned as `(offset, base)`.

    Found by the property, not by a remembered position. Pro Tools' own mono ladder carries such an
    index at `0x2015 + 154` and `0x200A + 163` (1..N) and at `0x261C + 1298` and `+1304` (0..N-1),
    but those numbers are artefacts of that layout; a real session need not match them, and hard
    offsets taken from the control corpus are exactly what caused several defects here.

    A duplicate leaves two tracks sharing the source's value, which Pro Tools resolves as one track.
    Only offsets that are ALREADY dense are renumbered, so a session that does not use the
    convention is left alone.
    """
    blks = [(z, e) for z, e, c in _size_driven_blocks(data) if c == ctype]
    n = len(blks)
    if n < 3:
        return []
    span = min(e - z for z, e in blks)
    out = []
    for off in range(9, span):
        vals = [data[z + off] for z, e in blks]
        if vals == list(range(n)):
            out.append((off, 0))
        elif vals == list(range(1, n + 1)):
            out.append((off, 1))
    return out


def _give_copy_free_track_index(before: bytes, after: bytes, newg: "set[bytes]") -> bytes:
    """Give the COPY an unused value in each dense per-track index; leave every other track alone.

    The fields are located on the PRE-edit session by `_dense_index_offsets`, so a session that does
    not use the convention is untouched. Only the copy's block is written -- identified by the fresh
    identity GUIDs, the same way its `0x261B` id range and UUIDs are.

    Densifying ALL blocks was tried first and reverted: it rewrites surviving tracks, `remove_track`
    does not undo that, and three tests failed -- exactly how the post-hoc `0x2519` ordinal renumber
    failed. Touching only the copy keeps `duplicate -> remove` an exact inverse, because removing the
    copy removes the only bytes that changed.
    """
    out = bytearray(after)
    # NOT 0x261C. Its dense index is the "C4" u32 that `_rekey_offsynth_positional` already
    # renumbers by rank, which gives the copy a distinct, IN-RANGE value on its own. Writing the
    # lowest-free value on top of that produced an out-of-range one -- on `3 stereo plus click`
    # the four records came out [0, 1, 4, 3] -- and that only ever round-tripped because remove
    # renumbered unconditionally and overwrote it. Once the renumber is correctly gated to sessions
    # that use the convention, the masking stops and the stale 4 survives the round trip.
    for ctype in (0x2015, 0x200A, 0x200B):
        offs = _dense_index_offsets(before, ctype)
        if not offs:
            continue
        blks = [(z, e) for z, e, c in _size_driven_blocks(bytes(out)) if c == ctype]
        for off, base in offs:
            vals = [out[z + off] for z, e in blks if z + off < e]
            for z, e in blks:
                if z + off >= e or not any(g in bytes(out[z:e]) for g in newg):
                    continue
                nv = base
                while nv in vals and nv < 255:
                    nv += 1
                out[z + off] = nv
                vals.append(nv)
                break
    return bytes(out)


# The per-track SELECTION state, TWO binary flags in each 0x2015 -- one anchored to the block head,
# one to its end. The 0x200B > 0x200A > 0x2015 nest is one physical structure (each block starts 9
# bytes after its parent), so each byte is also +9/+18 against those, and 0x261C wraps the lot; a
# naive offset search reports each flag four times.
#
# `0x2015` is NOT fixed-length (258, 277, 218, 210, 230 and 212 bytes all occur), which is what makes
# the second flag easy to get wrong: on the 258-byte form it sits at +254, and quoting that number
# runs off the end of the four smaller forms. Anchored to the END it is binary in 226/226 sessions at
# every size. Anchoring it by its head offset instead is what made it look like a different field.
_SELECTED_SITES = (("head", 28), ("tail", 4))


def _selection_flag_offsets(data: bytes) -> "list[tuple[str, int]]":
    """The `(anchor, delta)` sites in each `0x2015` holding this session's per-track SELECTION
    flags -- `("head", d)` meaning `zmark + d`, `("tail", d)` meaning `end - d` -- or `[]` if the
    session does not match the signature, in which case nothing is written.

    Pro Tools' own ladders name this field. `0x2015` is 1:1 with the track records, in track order,
    in 226/226 control sessions, and across `1..8 mono tracks` the flag reads::

        1 mono [1]            5 mono [0,0,0,0,1]
        2 mono [1,1]          6 mono [0,0,0,0,0,1]
        3 mono [0,0,1]        7 mono [0,0,0,0,0,0,1]
        4 mono [0,0,0,1]      8 mono [0,0,0,0,0,0,0,1]

    -- the just-created track. Neither is a structural "is last" bit: `1..4 stereo tracks` have NONE
    set and `5 stereo tracks` has ALL five, which is what a user-visible selection looks like after
    clicking away or Select All, and not what a derived field would ever do.

    A duplicate inherited both, so two tracks were flagged -- reported back from Pro Tools verbatim:
    "Both Audio 4 tracks are selected". Clearing only the head flag was NOT enough: Pro Tools still
    displayed both tracks selected on a file whose head column read `[0,0,0,1,0]`, so its on-screen
    selection follows at least the tail flag. Both are cleared.

    Each site is confirmed against `data` before use rather than trusted: the column must be binary
    across every `0x2015` and at least one track must be flagged, or there is nothing to clear.
    Offsets remembered from the control corpus and applied blind are what caused several defects
    here, so a site that does not match is dropped and a session that matches neither is untouched.
    """
    blks = [(z, e) for z, e, c in _size_driven_blocks(data) if c == 0x2015]
    if not blks:
        return []
    span = min(e - z for z, e in blks)
    sites = []
    for anchor, delta in _SELECTED_SITES:
        if delta >= span:
            continue
        col = [data[(z + delta) if anchor == "head" else (e - delta)] for z, e in blks]
        if set(col) - {0, 1} or not any(col):
            continue
        sites.append((anchor, delta))
    return sites


def _normalize_one_playlist_ordinals(data: bytes) -> bytes:
    """Put the `0x2015` playlist ordinals in BLOCK order when every track owns one playlist.

    The synth writer numbered them in CREATION order, so growing a session by adding MIDI tracks
    produced a column that is a permutation of `1..N` but not sorted::

        spec [1,0,2]        ->  [1, 2, 3]        correct
        spec [1,0,2,0]      ->  [1, 2, 4, 3]     the added MIDI took 4, the stereo kept 3
        spec [1,0,2,0,0]    ->  [1, 2, 4, 5, 3]

    Pro Tools never writes that: the value is the index of the track's FIRST PLAYLIST, so the column
    is a running total and is strictly increasing by construction (250/250 real sessions). It is
    also the exact shape of the sessions this project has been carrying as broken fixtures --
    `mixedmidi_5_AMSmM` reads `[1,2,5,3,4]`, `mixedmidi_6_noextfiles` `[1,2,5,3,4,6]` -- so this was
    still a LIVE defect, not the historical artefact the notes claimed. Only the `next_kind` half of
    that old finding had been fixed.

    The edit path already renumbered these positionally (`_rekey_positional_by_slot` writes `s+1`),
    so synth and duplicate disagreed about the same field; this makes them agree.

    Guarded twice, because the field's meaning was mis-documented twice: it acts only when the
    `0x210B` count equals the `0x2015` count (one playlist per track, so `1..N` IS the running
    total) and only when the current values are already a permutation of `1..N` (a pure reordering,
    never an invention). Anything else is returned untouched.
    """
    sites = _playlist_ordinal_sites(data)
    if not sites:
        return data
    n_pl = sum(1 for _z, _e, c in _size_driven_blocks(data) if c == 0x210B)
    vals = [v for _p, v in sites]
    want = list(range(1, len(sites) + 1))
    # THE COUNT EQUALITY IS THE EVIDENCE, not the current values. One `0x210B` per `0x2015` means
    # every track owns exactly one playlist, so the running total this ordinal encodes IS `1..N` --
    # writing it is not invention.
    #
    # An earlier version also required `vals` to already be a permutation of `1..N`, which made it
    # decline exactly where it was most needed: the click path clones a donor click track and
    # inherits its ordinal, emitting `[3,1,2,3,4]` for a 4-track session with a click on top. Every
    # click-bearing session this writer produced therefore carried a REPEAT, and a repeat is what
    # Pro Tools crashes on (PT-confirmed 2026-07-27).
    #
    # Measured before relaxing it: among sessions where `n_0x210B == n_0x2015`, the column is
    # exactly `1..N` in **335/345**, and all 10 exceptions are this project's own output
    # (`mixedmidi_*`, verified with `session_is_probably_ours`). So on Pro Tools' own files this is
    # a strict no-op; it only ever repairs ours.
    if n_pl != len(sites) or vals == want:
        return data
    return _write_playlist_ordinals(data, [1] * len(sites))


def _playlist_ordinal_sites(data: bytes) -> "list[tuple[int, int]]":
    """`(absolute position, value)` of each `0x2015`'s playlist ordinal, in block order."""
    out: "list[tuple[int, int]]" = []
    for z, e, c in _size_driven_blocks(data):
        if c != 0x2015:
            continue
        a = data.find(_ORD2015_ANCHOR, z, e)
        if a >= 0 and a + 22 <= e:
            out.append((a + 18, int.from_bytes(data[a + 18 : a + 22], "little")))
    return out


def _playlist_counts(data: bytes) -> "list[int] | None":
    """Playlists owned by each track, derived from the ordinal column, or `None` if this session
    does not follow the convention (in which case no caller may write).

    Conformance is checked THREE ways, all of which must agree, because this same field has been
    documented wrongly twice: the column starts at 1, is strictly increasing, and its gaps sum to
    exactly the `0x210B` count. The third is the one that makes the counts trustworthy -- it ties
    the column to an independent object count rather than to itself.
    """
    ords = [v for _p, v in _playlist_ordinal_sites(data)]
    n_pl = sum(1 for _z, _e, c in _size_driven_blocks(data) if c == 0x210B)
    if not ords or ords[0] != 1 or any(b <= a for a, b in zip(ords, ords[1:])):
        return None
    if n_pl == 0:
        # A session with NO 0x210B at all -- real Pro Tools files do this (`01 130 G Full
        # Stereo.ptx` on /Volumes/Samples has 5 tracks, ordinals [1,2,3,4,5] and zero playlist
        # blocks). The playlist-sum check cannot run, and tying the gate to it made this decline:
        # the copy then kept its source's ordinal, which is the state Pro Tools crashes on.
        # A column that is exactly 1..N is its own evidence here -- one slot per track, the
        # degenerate case of the same rule -- so the counts are all 1.
        return [1] * len(ords) if ords == list(range(1, len(ords) + 1)) else None
    counts = [b - a for a, b in zip(ords, ords[1:])] + [n_pl + 1 - ords[-1]]
    if any(c < 1 for c in counts) or sum(counts) != n_pl:
        return None
    return counts


def _write_playlist_ordinals(data: bytes, counts: "list[int]") -> bytes:
    """Rewrite the ordinal column to the prefix sums of `counts` (`1, 1+c0, 1+c0+c1, ...`)."""
    sites = _playlist_ordinal_sites(data)
    if len(sites) != len(counts):
        return data
    out = bytearray(data)
    v = 1
    for (pos, _old), c in zip(sites, counts):
        out[pos : pos + 4] = v.to_bytes(4, "little")
        v += c
    return bytes(out)


def _marker_scaffold_rows(data: bytes, z: int, e: int) -> "list[tuple[int, int]]":
    """The `(zmark, end)` of every `0x2506` row inside one `0x2077` marker, in order.

    The rows are a CONTIGUOUS RUN with a variable-length header before them (the marker name) and
    a short trailer after, so neither end of the block is a safe anchor. Resynchronising forward
    on the next `0x5A` desynchronises when that byte occurs inside the header (it reported 0 rows
    for 6 of 388 markers on one real session); walking backward from the end hits the trailer and
    reports 0 rows for every marker. Instead find the first FULLY-FRAMED `0x2506` -- `0x5A`,
    declared size 10, content type `0x2506`, specific enough that header bytes will not fake it --
    then step in exact 17-byte units while the frame holds. Verified against the count the
    `0x2506` relation predicts: exactly right on all 3,038 markers of 94 sessions."""
    out: "list[tuple[int, int]]" = []
    q = z + 9
    def framed(p: int) -> bool:
        return (data[p] == 0x5A
                and int.from_bytes(data[p + 3 : p + 7], "little") == 10
                and int.from_bytes(data[p + 7 : p + 9], "little") == 0x2506)
    while q + 17 <= e and not framed(q):
        q += 1
    while q + 17 <= e and framed(q):
        out.append((q, q + 17))
        q += 17
    return out


def sync_marker_playlist_rows(data: bytes, at: int, count: int) -> bytes:
    """Add (`count` > 0) or drop (`count` < 0) `0x2506` rows at playlist index `at`, in EVERY marker.

    Every `0x2077` marker carries exactly one 17-byte `0x2506` row per PLAYLIST -- the relation
    `n(0x2506) == n(0x2077) x n_playlists` is exact on every session measured (§5c). So a track
    edit that changes the playlist count must add or remove one row PER MARKER, which is what Pro
    Tools itself does. A session with no markers is returned unchanged.

    The row is not pure scaffold -- its `u32` at payload+2 is a per-playlist bit -- so a new
    playlist's row is COPIED from the playlist it was duplicated from rather than invented, which
    stays correct whichever way that bit is eventually decoded.

    TWO THINGS THIS HAS TO GET RIGHT, both of which broke an earlier attempt:
      * EVERY ancestor between a marker and its top-level block owns the size growth, not just the
        outermost one. Markers nest `0x271A -> 0x2030 -> 0x2077`, and fixing only the top left the
        intermediate `0x2030` 204 bytes short, so its child walk stopped early and the LAST marker
        of each group vanished from the block stream entirely.
      * Sizes must be written WHILE the offsets they use are still valid. Doing every insertion
        first and then writing size fields at ORIGINAL offsets writes them into already-shifted
        bytes; row counts came out 211,210,209,...,203 -- a cascading drift that reads like a
        locator bug and is not one. Markers are processed right-to-left so each one's offset is
        still correct when its size is written, and containers start below every insertion so
        theirs never move.

    Validated on 94 marker-bearing sessions: markers preserved, exactly one row added per marker,
    exact byte growth, index resolves, no new validator errors -- and add-then-drop round-trips
    BYTE-IDENTICAL at two different insertion indices. 46 marker-less sessions unchanged."""
    if count == 0:
        return data
    bl = sorted(_raw_block_bounds(data, _FI.final_index_ref(data).start))
    mks = [(z, e) for z, e, c in bl if c == 0x2077]
    if not mks:
        return data
    step = 17 * count

    def top_of(j: int):
        best = None
        for z, e, _c in bl:
            if z <= j < e and (best is None or z < best[0]):
                best = (z, e)
        return best

    groups: "dict[tuple[int, int], list[tuple[int, int]]]" = {}
    for z, e in mks:
        groups.setdefault(top_of(z), []).append((z, e))

    reps = []
    for (tz, te), ms in groups.items():
        blob = bytearray(data[tz:te])
        grow_by: "dict[tuple[int, int], int]" = {}
        for z, e in ms:
            for az, ae, _c in bl:
                if az <= z and e <= ae and tz <= az and ae <= te:
                    grow_by[(az, ae)] = grow_by.get((az, ae), 0) + step
        for z, e in sorted(ms, reverse=True):
            r = _marker_scaffold_rows(data, z, e)
            if not r:
                continue
            if count > 0:
                k = max(0, min(at, len(r)))
                src = data[r[min(k, len(r) - 1)][0] : r[min(k, len(r) - 1)][1]]
                ins = r[k][0] if k < len(r) else r[-1][1]
                blob[ins - tz : ins - tz] = src * count
                delta = step
            else:
                n = -count
                if len(r) < n:
                    continue
                k = max(0, min(at, len(r) - n))
                blob[r[k][0] - tz : r[k + n - 1][1] - tz] = b""
                delta = -(r[k + n - 1][1] - r[k][0])
            blob[z - tz + 3 : z - tz + 7] = ((e - z) + delta - 7).to_bytes(4, "little")
        for (az, ae), g in sorted(grow_by.items(), key=lambda kv: kv[0][0]):
            if (az, ae) in ms:
                continue
            blob[az - tz + 3 : az - tz + 7] = ((ae - az) + g - 7).to_bytes(4, "little")
        if len(blob) != te - tz:
            blob[3:7] = (len(blob) - 7).to_bytes(4, "little")
            reps.append((tz, te, bytes(blob)))
    return _splice_and_reindex(data, reps) if reps else data


def _playlist_span_of_edit(before: bytes, after: bytes) -> "tuple[int, int] | None":
    """`(playlist index, signed count)` of the playlist slots an edit added or removed.

    Read from the repaired `0x2015` ordinal column, which is a running total, so the changed
    track's own ordinal IS the index its playlists start at."""
    b = [v for _p, v in _playlist_ordinal_sites(before)]
    a = [v for _p, v in _playlist_ordinal_sites(after)]
    if not b or not a or len(a) == len(b):
        return None
    cb, ca = _playlist_counts(before), _playlist_counts(after)
    if cb is None or ca is None or sum(ca) == sum(cb):
        return None
    short, long_ = (b, a) if len(a) > len(b) else (a, b)
    i = next((k for k in range(len(short)) if long_[k] != short[k]), len(short))
    return long_[i] - 1, sum(ca) - sum(cb)


def _sync_marker_rows_for_playlist_delta(before: bytes, after: bytes) -> bytes:
    """Apply `sync_marker_playlist_rows` for whatever playlist delta an edit produced."""
    span = _playlist_span_of_edit(before, after)
    if span is None:
        return after
    at, n = span
    return sync_marker_playlist_rows(after, at, n)


def repair_playlist_ordinals_after_add(before: bytes, after: bytes,
                                       copy_index: "int | None" = None) -> bytes:
    """Give a duplicated track its own playlist ordinal, shifting the tracks below it.

    A byte-copied duplicate inherits its source's ordinal, so two tracks claim the same first
    playlist -- 216/216 control sessions. The copy owns as many playlists as its source (it IS a
    byte copy), so the repair is: insert the source's count at the copy's position and recompute the
    column as prefix sums.

    OPT-IN, and off by default. It rewrites SURVIVING tracks -- every track below the copy shifts --
    which is the exact class of change reverted repeatedly in this project, and Pro Tools opened,
    used and closed an unrepaired duplicate cleanly, so the defect is FIDELITY, not a crash. Trading
    a PT-confirmed-working path for an unverified one is a bad trade until a Pro Tools run says
    otherwise. `repair_playlist_ordinals_after_remove` is its exact inverse, so enabling both keeps
    `duplicate -> remove` byte-exact.

    Returns `after` unchanged when the pre-edit session does not follow the convention.
    """
    counts = _playlist_counts(before)
    if counts is None:
        return after
    if copy_index is None:
        copy_index = _added_block_index(before, after, 0x2015)
    if copy_index is None or not (0 < copy_index <= len(counts)):
        return after
    src = copy_index - 1                       # a copy is inserted directly after its source
    return _write_playlist_ordinals(after, counts[:copy_index] + [counts[src]] + counts[copy_index:])


def repair_playlist_ordinals_after_remove(before: bytes, after: bytes,
                                          removed_index: "int | None" = None) -> bytes:
    """Close the gap a removed track leaves in the playlist ordinal column — the exact inverse of
    `repair_playlist_ordinals_after_add`. Opt-in, for the same reason."""
    counts = _playlist_counts(before)
    if counts is None:
        return after
    if removed_index is None:
        removed_index = _removed_block_index(before, after, 0x2015)
    if removed_index is None or not (0 <= removed_index < len(counts)):
        return after
    return _write_playlist_ordinals(after, counts[:removed_index] + counts[removed_index + 1 :])


def _removed_block_index(before: bytes, after: bytes, ctype: int) -> "int | None":
    """Index of the one `ctype` block a track-remove DELETED, mirroring `_added_block_index`."""
    pre = [bytes(before[z:e]) for z, e, c in _size_driven_blocks(before) if c == ctype]
    post = [bytes(after[z:e]) for z, e, c in _size_driven_blocks(after) if c == ctype]
    if len(pre) != len(post) + 1:
        return None
    hits = [i for i in range(len(pre)) if pre[:i] + pre[i + 1 :] == post]
    return hits[0] if hits else None


def _added_block_index(before: bytes, after: bytes, ctype: int) -> "int | None":
    """Index of the one `ctype` block a track-add INSERTED, or `None` if that cannot be pinned down.

    Found by deletion: the added block is the one whose removal turns the post-edit run back into
    the pre-edit run byte for byte. No offsets and no GUIDs are involved -- the copy's `0x2015` does
    not contain the fresh per-channel GUIDs that identify its `0x261B` and `0x261C`, so the route
    used for those identity fixes cannot reach it.

    When the copy is still byte-identical to its source both positions qualify; the LATER one is
    returned, because a copy is inserted immediately after the track it was made from.
    """
    pre = [bytes(before[z:e]) for z, e, c in _size_driven_blocks(before) if c == ctype]
    post = [bytes(after[z:e]) for z, e, c in _size_driven_blocks(after) if c == ctype]
    if len(post) != len(pre) + 1:
        return None
    hits = [i for i in range(len(post)) if post[:i] + post[i + 1 :] == pre]
    return hits[-1] if hits else None


def _deselect_copied_track(before: bytes, after: bytes, newg: "set[bytes]") -> bytes:
    """Clear the SELECTION flag on the copy, so a duplicate of a selected track does not come back
    with two tracks selected.

    Only the copy's bytes change -- as with its `0x261B` id range, its UUIDs and its dense index --
    which keeps `duplicate -> remove` an exact inverse, since removing the copy removes the only
    bytes that were written.

    Pro Tools' own Duplicate Tracks instead MOVES the selection to the new track. That is not done
    here: it would rewrite a surviving track, and `remove_track` cannot restore a bit it never saw.
    Leaving the source selected and the copy not is a state Pro Tools itself writes -- the stereo
    ladder saves sessions with any subset flagged, including none.
    """
    sites = _selection_flag_offsets(before)
    idx = _added_block_index(before, after, 0x2015)
    if not sites or idx is None:
        return after
    blks = [(z, e) for z, e, c in _size_driven_blocks(after) if c == 0x2015]
    z, e = blks[idx]
    out = bytearray(after)
    for anchor, delta in sites:
        p = (z + delta) if anchor == "head" else (e - delta)
        if z <= p < e:
            out[p] = 0
    return bytes(out)


def _renumber_dense_track_indices(before: bytes, after: bytes) -> bytes:
    """Re-densify the per-track indices that a track add/remove left duplicated or gapped.

    `before` is the pre-edit session, used only to decide WHICH offsets the session actually uses as
    a dense index; `after` is rewritten. Doing it this way means a copy no longer shares its
    source's index, and no session that lacks the convention is touched.
    """
    out = bytearray(after)
    for ctype in (0x2015, 0x200A, 0x200B, 0x261C):
        for off, base in _dense_index_offsets(before, ctype):
            blks = [(z, e) for z, e, c in _size_driven_blocks(bytes(out)) if c == ctype]
            if len({z for z, _e in blks}) != len(blks):
                continue
            for i, (z, e) in enumerate(blks):
                if z + off < e:
                    out[z + off] = (base + i) & 0xFF
    return bytes(out)


def duplicate_track(data: bytes, source_track: str, new_name: str) -> bytes:
    """Add an AUDIO track by DUPLICATING an existing one under `new_name` — the inverse of
    `remove_track`, validated by round-trip byte-identity (`duplicate_track` then
    `remove_track(new_name)` == original).

    Copies the source track's COMPLETE per-track block-set (track-list entries 0x1014/0x251A,
    lane(s) 0x1052 in the shared 0x1054, the 0x261C playlist subtree, the SECOND view-chain
    subtree 0x2589, the 0x210B block, and the inline 0x2519 name entry), substituting the
    track name and fresh per-channel GUIDs; inserts each copy after its source; grows the
    enclosing containers and re-syncs their child-counts; and adds the new blocks' master-index
    references. The copy shares the source's clips/regions (a true duplicate).

    The rebuilt master index is CANONICAL — byte-identical to a fresh
    `synthesize_stereo_inline(N+1)` of the same track layout (the new track's records are
    added with proper separate elements/instance records ordered by body rank, not doubled
    in place), via `final_index.canonicalize_after_track_add`. `remove_track` is its exact
    inverse, so the round-trip stays byte-exact.

    A DIFFERENT-LENGTH `new_name` is handled by duplicating under a same-length placeholder and
    then `rename_track`-ing to the real name (which grows/shrinks the containing blocks and
    reindexes for the size delta). The same-length path below is left byte-for-byte unchanged, so
    the PT-confirmed synth oracles (stereo/mono/mixed duplicate byte-identical to synth(N+1)) are
    untouched. The variable-length path inherits `rename_track`'s constraints (it currently uses
    the scan parser, which stumbles on some MIDI-clip sessions).

    Handles AUDIO (0x261C) and MIDI (0x2620) tracks. Constraints: unique source name; cannot
    duplicate the LAST track in the name table (short-trailer entry — rename/reorder first), which
    the click always is; a CLICK/aux (0x261E) track is not yet duplicable (the canonicalize
    body-rank sort hardcodes 0x261C — a latent issue gated off by the last-entry rule). Validated
    so the per-track block delta equals a real session's, the EOS simulator (`validate`) is clean,
    rc=0, the round-trip (`duplicate_track` then `remove_track(new_name)`) is byte-identical
    (audio AND MIDI; PT display-confirm pending), and every other block is
    byte-intact."""
    import struct
    # BYTES, not characters. `_unique_same_length_name` produces a name of exactly N BYTES, and
    # the same-length path splices byte-for-byte, so a 23-byte source name that is 22 CHARACTERS
    # long (`00 PRINT_CLIX 135·3BPM`) took a 22-byte placeholder and spliced one byte short --
    # corrupting the master index into "bad element tag". Failed on all 28 corpus sessions with an
    # accented track name, while an ASCII-named track in the SAME session duplicated cleanly.
    if len(new_name.encode()) != len(source_track.encode()):
        taken = {t.name for t in track_types(data)} | {source_track, new_name}
        placeholder = _unique_same_length_name(len(source_track.encode()), taken)
        return rename_track(duplicate_track(data, source_track, placeholder),
                            placeholder, new_name)
    # the source's 0-based display row (the copy is spliced in right after it, so the copy's
    # row is source_row + 1); captured before any edit for the closing BODY renumber.
    _names_before = [t.name for t in track_types(data)]
    _source_row = _names_before.index(source_track) if source_track in _names_before else None
    # BODY positional renumber (synth-shaped mono/stereo/mixed sessions only): capture the
    # pre-edit channel-count layout, the grow-path threshold, and the donor 0x261c free fields so
    # the closing renumber can make the duplicate byte-identical to a fresh synth(N+1). Real
    # sessions (clips/plugins/automation/click) fail `_is_synth_shaped` and keep the splice path.
    _do_renumber = _is_synth_shaped(data)
    if _do_renumber:
        _base_n = _view_scale_base_n(data)
        _donor_free = _capture_donor_free(data, _track_kinds(data), _base_n)
    ref = _FI.final_index_ref(data)
    bl = sorted(_raw_block_bounds(data, ref.start))
    zend = {z: e for z, e, _c in bl}
    zct = {z: c for z, _e, c in bl}

    def guids_in(blk: bytes) -> "list[bytes]":
        out, k = [], 0
        while True:
            k = blk.find(b"\x2a\x00\x00\x00", k)
            if k < 0 or k + 12 > len(blk):
                break
            # SKIP offset 3: that is the block's own SIZE field, and a block whose declared size is
            # exactly 42 spells `2a 00 00 00` there. The bytes that follow are then the CONTENT-TYPE
            # word, not a GUID -- and `transform` rewrites whatever this returns. On a real session
            # that turned two copied `0x251A` lane blocks into type `0x7F40`, so the block walk no
            # longer enumerated them as lanes and `remove_track` could not find the track it had
            # just created. The tag never legitimately sits in the size field.
            if k != 3:
                out.append(blk[k + 4 : k + 12])
            k += 1
        return out

    def innermost(j: int):
        best = None
        for z, e, c in bl:
            if z < j < e and (best is None or z > best[0]):
                best = (z, e, c)
        return best

    sb, nn = source_track.encode(), new_name.encode()
    ident: "set[int]" = set()
    seed: "set[bytes]" = set()
    nt_ranges: "list[tuple[int, int]]" = []
    _last_entry_dup: "tuple[int, int] | None" = None   # (start, end) when the source is LAST
    pos = 0
    while True:
        j = data.find(sb, pos)
        if j < 0:
            break
        if j >= 4 and int.from_bytes(data[j - 4 : j], "little") == len(sb):
            b = innermost(j)
            if b:
                z, e, c = b
                if c in (0x1014, 0x1057, 0x251A):        # 0x1057 = MIDI track-list entry (0x1058)
                    ident.add(z)
                    seed.update(guids_in(data[z:e]))
                elif c == 0x1052:
                    ident.add(z)
                elif c == 0x210B:                       # per-track 0x210B (name + playlist GUID)
                    ident.add(z)
                    seed.update(guids_in(data[z:e]))
                elif c == 0x2519:                       # copy the WHOLE inline name entry
                    span = _name_entry_span(data, j, bl)
                    if span:
                        st, en = span
                        # The LAST entry uses the SHORT (21-byte) trailer because its `next_kind`
                        # u16 is absent -- there is no next entry (see the spec). Duplicating it
                        # is the mirror of remove_track's last-entry truncation: the source stops
                        # being last, so it is PROMOTED to the 23-byte form by inserting
                        # `kind(copy) << 8` (the copy has the source's kind) before its pad byte,
                        # and the copy becomes the new last in the short form with ordinal N+1.
                        if en - st < 4 + len(sb) + _NAME_ENTRY_SUFFIX:
                            _last_entry_dup = (st, en)
                        else:
                            nt_ranges.append((st, en))
        pos = j + 1
    pl_ranges: "list[tuple[int, int]]" = []
    for z, e, c in bl:
        if c in (0x102D, 0x2619) and any(g in data[z:e] for g in seed):
            cs = [(cz, ce) for cz, ce, cc in bl
                  if cc in _TRACK_RECORD_TYPES and cz < z < ce]
            if cs:
                pz, pe = max(cs)
                pl_ranges.append((pz, pe))
    # EXACTLY ONE record, or this route has identified nothing. The seeded GUID is not always
    # unique, in two distinct ways, and acting on a multi-way match splices the copy in once per
    # match:
    #
    #   * `2A 00 00 00` is the GUID TAG, so it is also how the number 42 is stored. Track 42's own
    #     ordinal spells the tag, and the 8 bytes after it -- ordinary neighbouring data, not an id
    #     -- are read as its GUID and found inside EVERY track record. `Audio 42` resolves to all
    #     512 records in `512 mono tracks.ptx`, all 100 in `100 stereo tracks.ptx`, and so on for
    #     every session with at least 42 tracks. (The size-field case at offset 3 was already
    #     skipped; this is the same hazard at a different position, and cannot be fixed by skipping
    #     an offset.)
    #   * genuinely shared ids: in `midi9.ptx` MIDI 9's seed occurs 18 times and sits inside MIDI 2's
    #     record too. The copy went in twice -- 11 records for 10 name entries -- and the result
    #     could then not be edited at all, the name-table guard (correctly) refusing it.
    #
    # 15 of 4458 tracks across the control corpus are ambiguous this way. Falling through to the
    # positional rule resolves all 15; the 4443 that already resolve uniquely are unaffected.
    pl_ranges = list(dict.fromkeys(pl_ranges))
    if len(pl_ranges) == 1:
        _pz, _pe = pl_ranges[0]
        ident |= {zz for zz, _ee, _cc in bl if _pz <= zz < _pe}
    else:
        pl_ranges = []
    if not pl_ranges:
        # POSITIONAL FALLBACK. A track has TWO names and they need not agree: the display name lives
        # in 0x2519/0x251A/0x1014, while the per-track record's 0x102D -> 0x2619 keeps the name the
        # track was CREATED with (`Kick In` vs `Audio 2`). Where those records also carry no GUID,
        # BOTH routes above come up empty and this used to refuse -- 36 of 250 sessions in one
        # corpus, every one of them uneditable.
        #
        # The Nth name-table entry is the Nth track record. Validated against the name/GUID route
        # itself: on 225 tracks across 10 sessions where that route resolves uniquely, it selects
        # the same record the positional rule does -- 225/225. Counts agree too (entries ==
        # track records in every session checked). Used ONLY as a fallback, so no session that
        # resolves today changes behaviour.
        _ntab = [(z, e) for z, e, c in bl if c == 0x2519]
        _ents = ([n for n, _q in _inline_name_entries(data, _ntab[0][0], _ntab[0][1], bl)]
                 if _ntab else [])
        _recs = sorted((zz, ee) for zz, ee, cc in bl if cc in _TRACK_RECORD_TYPES)
        if source_track in _ents and len(_ents) == len(_recs):
            pz, pe = _recs[_ents.index(source_track)]
            pl_ranges.append((pz, pe))
            ident |= {zz for zz, _ee, _cc in bl if pz <= zz < pe}
    if not pl_ranges:
        raise ValueError(f"cannot locate track {source_track!r} (or an unsupported track type)")

    # A MASTER/BUS track (0x261D) has no 0x1014 track-list entry, so the copy is not findable
    # afterwards: `remove_track` cannot locate it and the session is left with an un-removable
    # track. Refuse up front instead of emitting that. Measured across 487 real sessions, this is
    # the only track kind that fails -- audio, MIDI and click sources in the SAME session all
    # duplicate and round-trip byte-exact.
    if any(c == 0x261D for _z, _e, c in bl if (_z, _e) in [(pz, pe) for pz, pe in pl_ranges]):
        raise ValueError(
            f"cannot duplicate {source_track!r}: it is a master/bus track (0x261D), which carries "
            "no 0x1014 track-list entry, so the copy could not be located or removed afterwards")

    # The source's track-id range, read BEFORE the splice: the copy will inherit it verbatim and
    # must be moved off it, but any OTHER collision already in the session is left alone.
    _assert_name_table_complete(data, bl)
    _src_id_first = None
    for _z, _e, _c in bl:
        if _c == 0x261B and any(pz < _z < pe for pz, pe in pl_ranges):
            _r = _track_id_run(data[_z:_e])
            if _r is not None:
                _src_id_first = _r[1]
                break
    # How many blocks ALREADY share that range. The source itself may be half of a pre-existing
    # collision (our own click sessions are), and only the one the copy adds is ours to move.
    _src_id_len, _new_id_first, _all_id = 0, None, []
    if _src_id_first is not None:
        for _z, _e, _c in bl:
            if _c == 0x261B:
                _r = _track_id_run(data[_z:_e])
                if _r is not None:
                    _all_id.append((_r[1], _r[2]))
                    if _r[1] == _src_id_first:
                        _src_id_len = _r[2]
        # The LOWEST FREE slot at or after the source's own range, stepping a whole range at a time.
        # Not the session-wide maximum + 1: a synthesized session carries TWO id sequences -- the
        # donor's (20661...) and a separate low run for grown tracks (81, 91, ... 191) -- and
        # `synthesize(N+1)` continues the LOW one with 201, so a high-water rule allocated 20741 and
        # made a grown duplicate differ from the equivalent fresh synth. This rule reproduces both
        # oracles: 201 for the synth ladder, 13261 for Pro Tools' own mono ladder.
        _cand = _src_id_first + (_src_id_len or 1)
        while _src_id_len and any(_cand < _f + _l and _f < _cand + _src_id_len for _f, _l in _all_id):
            _cand += _src_id_len
        _new_id_first = _cand

    # The source track's SECOND view-chain subtree (0x2589), attributed by the block's OWN
    # display-row ordinal (u16 @ zmark+9) -- the SAME rule remove_track uses, so the two agree on
    # what a track's chain is and dup->remove stays a perfect inverse. The blocks are not stored
    # in display order (a real session's zmark order reads [2, 0, 1, 3]), so the old zmark-rank
    # pick copied another track's chain; and its `len(s2589) == len(playlists)` guard skipped
    # whole sessions (4 chains / 3 playlists), leaving the copy with no chain at all while
    # remove still deleted one -- the round-trip asymmetry. Copied verbatim (no name/GUID inside)
    # by the units machinery below.
    # ...and, like remove_track, ONLY on a synth-shaped session: a real session's 0x2589 set does
    # not correspond 1:1 to `track_types` (4 chains for 3 tracks in one fixture -- display rows
    # track_types never reports), so no track-keyed attribution is sound there. Both editors skip
    # it off the synth path, which keeps dup->remove a perfect inverse on real sessions.
    s2589 = [(z, int.from_bytes(data[z + 9 : z + 11], "little"))
             for z, _e, c in bl if c == 0x2589]
    if s2589 and pl_ranges and _do_renumber:
        rows = [t.name for t in track_types(data)]
        if sorted(o for _z, o in s2589) == list(range(len(rows))) and source_track in rows:
            row = rows.index(source_track)
            for z2589, ordinal in s2589:
                if ordinal == row:
                    ident.add(z2589)
                    break

    # OFF the synth path the chain is attributed against the NAME-TABLE entries, not
    # `track_types`: the ordinals are display rows, and a click has a row but no track_types
    # entry, which is exactly why the track_types basis reported "no 1:1" and skipped. The
    # name-entry basis IS sound -- on 162/162 PT-authored corpus sessions the chain ordinals are
    # exactly `range(len(name entries))`. Skipping left the copy with NO view chain (N+1 entries,
    # N chains), silently violating that invariant; the byte-exact round-trip hid it because
    # remove skipped symmetrically. `_dup_view_chain` carries (source chain zmark, its row) to the
    # post-splice ordinal fixup below.
    _dup_view_chain: "tuple[int, int] | None" = None
    _nt = [(z, e) for z, e, c in bl if c == 0x2519]
    if s2589 and pl_ranges and not _do_renumber and _nt:
        ents = [n for n, _p in _inline_name_entries(data, _nt[0][0], _nt[0][1], bl)]
        if sorted(o for _z, o in s2589) == list(range(len(ents))) and source_track in ents:
            row = ents.index(source_track)
            for z2589, ordinal in s2589:
                if ordinal == row:
                    ident.add(z2589)
                    _dup_view_chain = (z2589, row)
                    break

    # fresh per-channel GUIDs (deterministic, guaranteed absent)
    gmap: "dict[bytes, bytes]" = {}
    for g in seed:
        ng = bytes(b ^ 0x5A for b in g)
        while ng in data or ng in gmap.values():
            ng = bytes(b ^ 0x37 for b in ng)
        gmap[g] = ng

    def transform(blk: bytes) -> bytes:
        blk = blk.replace(sb, nn)
        # Rewrite GUIDs ONLY where a GUID actually lives -- immediately after a `2a 00 00 00` tag.
        # A blanket `blk.replace(g, ng)` matches those 8 bytes ANYWHERE, and on real sessions a
        # GUID's byte pattern straddled a nested `0x1014` header: the copy came out with a
        # corrupted size/content-type word, which survived into the removed file and left
        # duplicate->remove 2 bytes short of byte-exact. Same hazard, and same fix, as the track-id
        # remap: target the field, never the byte pattern.
        b = bytearray(blk)
        k = 0
        while True:
            k = b.find(b"\x2a\x00\x00\x00", k)
            if k < 0 or k + 12 > len(b):
                break
            if k != 3:                      # offset 3 is the block's own SIZE field, not a tag
                ng = gmap.get(bytes(b[k + 4 : k + 12]))
                if ng is not None:
                    b[k + 4 : k + 12] = ng
            k += 1
        return bytes(b)

    # outermost copy-units + name-table strings -> insert a transformed copy after each
    units = sorted((z, zend[z]) for z in ident
                   if not any(zz < z and zend[zz] >= zend[z] for zz in ident if zz != z))
    inserts = [(e, transform(data[z:e]), z) for z, e in units]
    inserts += [(b, transform(data[a:b]), None) for a, b in nt_ranges]

    # The copy is spliced in IMMEDIATELY AFTER the source entry, so the source's `next_kind` -- a
    # u16 describing the kind of the entry that FOLLOWS it -- now describes the COPY, i.e. it must
    # become the source's own kind. It was left untouched, so the chain kept naming whatever used
    # to follow: duplicating `MIDI 1` in a mixed session produced `MIDI 1 -> MIDI 2` still tagged
    # 0x0000 (audio) instead of 0x0100 (MIDI). A violation is NOT known to be fatal -- four control
    # files carry one and are PT-confirmed to OPEN -- so this is about writing what PT writes, not
    # about a crash. Pro Tools' own duplicate of
    # an audio track (corpus pair THE WIND_Vocal Tracking bak.095 -> bak.096, which is PT's Duplicate
    # Track writing `Voc 2.dup1`) keeps this chain exact, and ours did not.
    #
    # The COPY keeps the source's ORIGINAL next_kind, which correctly describes what follows IT --
    # so this patch must land AFTER the copy blobs above are materialised from the unpatched bytes.
    if nt_ranges:
        _srck = {0x261C: 0, 0x2620: 1, 0x261E: 2}.get(
            next((c for z, e, c in bl if (z, e) in [(pz, pe) for pz, pe in pl_ranges]), 0x261C), 0)
        _d = bytearray(data)
        for _a, _b in nt_ranges:
            _d[_b - 3 : _b - 1] = (_srck << 8).to_bytes(2, "little")   # 2 bytes before the pad
        data = bytes(_d)

    # Duplicating the LAST name entry (see the 0x2519 trailer section of the spec): the source is
    # promoted to the 23-byte form by inserting its `next_kind` u16 -- the kind of the entry that
    # now follows it, i.e. the copy, i.e. the source's own kind -- immediately before its pad byte,
    # and the copy is appended as the new last in the short 21-byte form with ordinal N+1.
    if _last_entry_dup is not None:
        _st, _en = _last_entry_dup
        _kind = {0x261C: 0, 0x2620: 1, 0x261E: 2}.get(
            next((c for z, e, c in bl
                  if (z, e) in [(pz, pe) for pz, pe in pl_ranges]), 0x261C), 0)
        inserts.append((_en - 1, (_kind << 8).to_bytes(2, "little"), None))
        _nents = len(_inline_name_entries(data, _nt[0][0], _nt[0][1], bl)) if _nt else 0
        _cp = bytearray(transform(data[_st:_en]))
        _op = 4 + len(nn) + 18                      # <len><name><flags4><zeros2><tag4><guid8>
        if _op + 2 <= len(_cp) and _nents:
            _cp[_op : _op + 2] = (_nents + 1).to_bytes(2, "little")
        inserts.append((_en, bytes(_cp), None))

    # ---- INLINE per-track inserts (inverse of remove_track's <ALL> splices) ----
    # Append one u16 == the list's next index before each 0x202a/0x202b <ALL> trailer, so the
    # copy's ordinal records equal a valid track's; identity ordinals stay dense [0..N].
    # The new index is each list's OWN member count, NOT `len(track_types(data))`: those agree
    # only when the <ALL> group happens to hold exactly the reported tracks. A click-bearing
    # session's <ALL> holds [0, 1] while track_types reports 1 (the click track is not reported),
    # so the model inserted a second `1` -- a DUPLICATE ordinal in the group. Reading the list's
    # own length is the same value on the synth path (8 members <-> 8 tracks) and correct here.
    for z, e, c in bl:
        if c == 0x202B:
            for lst in _all_ordinal_lists(data, z, e):
                next_index = (lst["ords_end"] - lst["ords_start"]) // 2
                inserts.append((lst["ords_end"], struct.pack("<H", next_index), None))
    inserts.sort()

    def shift(o: int) -> int:
        return o + sum(len(ins) for at, ins, _su in inserts if at <= o)

    body = bytearray()
    prev = 0
    unit_copy: "dict[int, int]" = {}   # source unit start -> its copy's new-body start
    for at, ins, su in inserts:
        body += data[prev:at]
        if su is not None:
            unit_copy[su] = len(body)
        body += ins
        prev = at
    body += data[prev : ref.start]

    def copy_off(src_block: int) -> "int | None":
        for z, e in units:
            if z <= src_block < e:
                return unit_copy[z] + (src_block - z)
        return None

    # Off-synth view-chain ordinals: the copy sits at display row R+1 (the name entry is spliced
    # directly after the source's), so the verbatim copy -- which still carries the source's row R
    # -- is restamped R+1 and every chain BELOW the source shifts down one. Keeps the PT invariant
    # `chain ordinals == range(name entries)` that a real session always satisfies. remove_track
    # applies the exact inverse, so dup->remove stays byte-exact.
    if _dup_view_chain is not None:
        _src2589, _row = _dup_view_chain
        _co = copy_off(_src2589)
        if _co is not None:
            body[_co + 9 : _co + 11] = (_row + 1).to_bytes(2, "little")
            for _z2589, _ordv in s2589:
                if _ordv > _row:
                    _nz = shift(_z2589)
                    body[_nz + 9 : _nz + 11] = (_ordv + 1).to_bytes(2, "little")

    for z, e, _c in bl:                              # grow enclosing containers
        if z in ident:
            continue
        # A copy unit is inserted at `at == e_source_unit`. When the source unit is the LAST child
        # of an ancestor, that ancestor's declared end EQUALS the insertion offset, so a strict
        # `at < e` skipped it: the container was not grown and the copy landed just past its
        # declared end -- ORPHANED. PT-tested 2026-07-23: every duplicate whose copied 0x2589
        # escaped 0x258A/0x258B this way CRASHED PRO TOOLS ON LOAD (01/04/07) or failed with
        # "unexpected stream type ... Zoom Memories" (10, the 0x2551 stream PT reads there); every
        # duplicate without the orphan merely loaded. 8/8 + 4/4 correlation across the tested set,
        # with a controlled pair (06 dup-audio grew all four ancestors and LOADED; 07 dup-MIDI on
        # the SAME session left 0x258A/0x258B ungrown and CRASHED). Grow when the insertion is at
        # the end too, provided the source unit really is inside this container.
        grow = sum(len(ins) for at, ins, _su in inserts
                   if (z < at < e) or (at == e and _su is not None and z < _su))
        if grow:
            nz = shift(z)
            body[nz + 3 : nz + 7] = (
                int.from_bytes(body[nz + 3 : nz + 7], "little") + grow
            ).to_bytes(4, "little")

    recs = _FI.parse_records(ref.data, set(zct.values()), set(zct))
    copy_childref_offsets: "set[int]" = set()   # offset VALUES the copy introduced (childrefs)
    copy_element_offsets: "set[int]" = set()     # ...and elements — used by the canonicalizer
    for r in recs:                                   # add a copy ref per source ref; shift the rest
        ncr = []
        for c in r.child_refs:
            ncr.append((c, False))
            if c.offset in ident:
                co = copy_off(c.offset)
                copy_childref_offsets.add(co)
                ncr.append((_FI.ChildRef(rel=0, child_type=c.child_type,
                                         offset=co, flags=c.flags), True))
        for c, is_copy in ncr:
            if not is_copy:
                c.offset = shift(c.offset)
        r.child_refs = [c for c, _ in ncr]
        for el in r.elements:
            no = []
            for o in el.offsets:
                no.append((o, False))
                if o in ident:
                    co = copy_off(o)
                    copy_element_offsets.add(co)
                    no.append((co, True))
            el.offsets = [o if is_copy else shift(o) for o, is_copy in no]
    # The loop above produced the REVERSIBLE doubled-offset index (copy offset spliced
    # beside each source offset). Canonicalize it to the fresh-synth(N+1) shape so the
    # duplicate's index is byte-identical to synth(N+1): split the doubled elements/instance
    # records into separate single-offset ones ordered by body rank, and re-rank the
    # per-track ordinals. `rebuild_after_track_drop` is its exact inverse, so the
    # duplicate→remove round-trip stays byte-exact.
    dup_body = _set_index_offset(bytes(body) + _FI.serialize_final_block(recs))
    _z2t, by_type = _FI.block_layout(dup_body)
    block_ranks = {ct: {z: i for i, z in enumerate(zs)} for ct, zs in by_type.items()}
    recs = _FI.canonicalize_after_track_add(
        recs, block_ranks, copy_childref_offsets | copy_element_offsets)
    out = _set_index_offset(bytes(body) + _FI.serialize_final_block(recs))
    out = _fix_all_ordinal_counts(out)                   # <ALL> u16 counts (0x202a/0x202b)
    out = _fix_name_table_intergroup(out)                # 0x2519 inter-group u32
    out = _fix_name_table_count(_fix_container_counts(out))
    # BODY positional renumber (synth-shaped): give the copy its display slot's channels/ordinals/
    # element-ids/view-scale/lane order (per its channel count), recompute every track's 0x261c
    # free field to its fresh-synth value, and slot-align the downstream tracks -- so the BODY is
    # byte-identical to a fresh synth(N+1) of the same mono/stereo/mixed layout.
    if _do_renumber and _source_row is not None:
        out = _renumber_body_after_duplicate(out, _source_row + 1, _track_kinds(out),
                                             _base_n, _donor_free)
    else:
        # NOT synth-shaped (a real session: clips/plugins/automation/MIDI). The fixed-offset
        # renumber above is derived only for the synth layout, so it is skipped -- but the copy
        # was spliced from the source and therefore still carries the SOURCE's channel indices.
        # Two tracks claiming the same channels is exactly what crashes Pro Tools on close (the
        # verify_2 fault), so give the copy free channels. This is structural (set_track_channels
        # locates the fields via the name length + the 0x2a marker), so it holds for any layout.
        # Channels need only be UNIQUE, not contiguous -- removal already leaves gaps that PT
        # accepts -- so the lowest free indices are safe.
        out = _assign_free_channels(out, new_name)
    # 0x1015 counts AUDIO tracks (its 0x1014 entries), NOT all tracks (166 PT sessions) -- so
    # bump it only when the duplicated track is audio; a MIDI/click copy leaves it unchanged.
    audio_edit = any(zct.get(z) == 0x1014 for z in ident)
    midi_edit = any(zct.get(z) == 0x1057 for z in ident)
    out = _adjust_track_count(out, 1 if audio_edit else 0)     # 0x1015 = audio count
    if midi_edit:
        out = _adjust_midi_list_count(out, 1)                  # 0x1058 = MIDI count
    if not _do_renumber:
        # `c4_dense` is judged on the PRE-EDIT session: after the splice the column reads
        # [0,1,2,3,3] / [0,1,3], and judging THAT is what sank every earlier conditional renumber.
        out = _rekey_offsynth_positional(out, c4_dense=_c4_uses_dense_convention(data))

    # NOT verified on the way OUT. Asserting completeness on `out` was tried and REVERTED: it fires
    # on `duplicate_track`'s own placeholder-then-rename step, whose intermediate table reads short
    # on ordinary PT-authored sessions that round-trip byte-exactly. A check that rejects files
    # which are fine is the trap this project has hit repeatedly, so the guard stays on the INPUT.
    #
    # KNOWN GAP: on the one session with a phantom-truncated table the input now walks 54/54 but
    # the spliced output walks 18/55, so duplicate_track returns a file whose table later passes
    # cannot re-walk. remove_track's input guard catches it downstream. Fixing it properly means
    # making the SPLICE phantom-aware, not adding another assertion.

    # THE COPY MUST NOT INHERIT THE SOURCE'S TRACK IDS. Pro Tools gives each track a contiguous
    # block of 10 session-unique ids in its 0x261B and resolves a track's IDENTITY through them, so
    # a shared range made PT treat the two tracks as ONE object: it displayed the copy under the
    # SOURCE's name no matter what name we wrote (a name table reading [Audio 1..5] showed
    # `Audio 1,2,3,4,4`), selecting either selected BOTH, and it CRASHED ON CLOSE -- 4 of 4
    # duplicates tested in PT on 2026-07-25, against 3 of 3 clean removals, which allocate nothing.
    #
    # The copy's 0x261B is found by its NEW identity GUIDs, not by block order: the copy is spliced
    # next to its source, so "the later of the two" can be an ORIGINAL track, and renumbering that
    # one broke the duplicate->remove inverse. Only values inside the source's own range are moved,
    # which is why this cannot disturb an unrelated field that merely holds the same number.
    # Off-synth only, like the re-key above. The synth path renumbers ids POSITIONALLY so that a
    # grown session equals a fresh `synthesize(N+1)`, and `synthesize()` applies its own
    # collision sweep to whatever it returns, so its delivered output is disjoint anyway.
    if not _do_renumber and _src_id_first is not None and _new_id_first is not None and _src_id_len:
        _newg = set(gmap.values())
        _o = bytearray(out)
        _b261b = [(_z, _e) for _z, _e, _c in _size_driven_blocks(out) if _c == 0x261B]
        _hit = next((i for i, (_z, _e) in enumerate(_b261b)
                     if any(_g in out[_z:_e] for _g in _newg)), None)
        if _hit is None:
            # NOT EVERY TRACK HAS A GUID. `0x102D` is universal (9945/9945 records on four drives)
            # but the `2A 00 00 00` tag inside it is not: 376 of 476 blocks on the PT HD 10.3/11.2
            # corpus carry it, so ~21% of tracks there have none. With no fresh GUID to match, this
            # loop found no block and silently left the copy SHARING its source's id range -- the
            # exact condition Pro Tools crashed on. Measured before the fallback: 4 of 4 such
            # duplicates produced a shared range (5 ranges/5 distinct -> 6 ranges/5 distinct).
            #
            # Fall back to the structural identification `_deselect_copied_track` already uses: the
            # block whose removal turns the post-edit run back into the pre-edit run, taking the
            # LATER position when the copy is still byte-identical to its source.
            _hit = _added_block_index(data, out, 0x261B)
        if _hit is not None and _hit < len(_b261b):
            _z, _e = _b261b[_hit]
            for _p in range(_z, _e - 3):
                _v = int.from_bytes(_o[_p : _p + 4], "little")
                if _src_id_first <= _v < _src_id_first + _src_id_len:
                    _o[_p : _p + 4] = (_new_id_first + (_v - _src_id_first)).to_bytes(4, "little")
        out = bytes(_o)

    # ...and FRESH UUIDs for the copy. The integer id range alone was not enough: Pro Tools still
    # showed the copy under the source's name, selected both together, and crashed on close.
    # Off-synth only, like the id remap: the synth path renumbers positionally so a grown session
    # equals a fresh synthesize(N+1), and rewriting UUIDs there breaks that equivalence.
    if not _do_renumber:
        out = _give_copy_free_track_index(data, out, set(gmap.values()))
        # ...and the copy must not inherit the source's SELECTION flag. Pro Tools reported the copy
        # and its source selected together; ours read [0,0,0,1,1] where PT's own 5-track session
        # reads [0,0,0,0,1].
        out = _deselect_copied_track(data, out, set(gmap.values()))
        # NOT densifying ALL blocks (only the copy is touched above). `_renumber_dense_track_indices` finds them
        # correctly (0x2015+154, 0x200A+163, 0x261C+1298/+1304 in the mono ladder, located by the
        # dense-sequence property rather than by remembered offsets), but renumbering them touches
        # SURVIVING tracks, and remove_track does not undo that -- it broke the duplicate->remove
        # inverse on three tests, the same way the post-hoc 0x2519 ordinal renumber did. The helper
        # is kept because the analysis is right; what is missing is giving the COPY a free index
        # without renumbering its neighbours.
        _newg3 = set(gmap.values())
        for _z3, _e3, _c3 in _size_driven_blocks(out):
            if _c3 == 0x261B and any(_g in out[_z3:_e3] for _g in _newg3):
                out = _reuuid_copied_track(out, _z3, _e3)
                break

    # THE 0x2015 PLAYLIST ORDINAL, REPAIRED BY DEFAULT AS OF 2026-07-27 -- Pro Tools settled it.
    #
    # A duplicate leaves the copy sharing its source's ordinal. Reported from Pro Tools on a file
    # whose column read [1, 2, 3, 4, 4]: "Two tracks named Audio 4 and Pro Tools crashes on close."
    # Every stored NAME in that file is correct and distinct -- 0x2519, 0x251A, 0x1014, 0x102D,
    # 0x2619, 0x210B all hold exactly one `Audio 5` -- so Pro Tools is not reading a name there; it
    # resolves the track through this ordinal, and two tracks claiming slot 4 collapse into one
    # object. Same signature as the 0x261B id-range collision, one field along.
    #
    # The file Pro Tools CONFIRMED working carries [1, 2, 3, 4, 5], identical to its own
    # `5 mono tracks.ptx`. So the repair is not a fidelity nicety -- it is the difference between a
    # session that opens and one that crashes, and the opt-in default was wrong.
    #
    # This was a REGRESSION, bisected to commit 59add8f: at 388ec46 (the PT-confirmed state) a
    # duplicate emitted [1, 2, 3, 4, 5] and afterwards [1, 2, 3, 4, 4]. It is now produced
    # deliberately by `repair_playlist_ordinals_after_add` rather than as a side effect, so it
    # cannot regress silently again -- `remove_track` applies the exact inverse.
    if not _do_renumber:
        out = repair_playlist_ordinals_after_add(data, out)
    # The copy is a new PLAYLIST, and every 0x2077 marker holds one 0x2506 row per playlist. Pro
    # Tools adds those rows when it adds a track; we added none, leaving the matrix short by
    # n(0x2077) on any session WITH markers. That never surfaced because every session confirmed
    # in Pro Tools so far has NO markers, which makes this a strict no-op on all of them.
    out = _sync_marker_rows_for_playlist_delta(data, out)
    return out


def _track_1014_rows(data: bytes) -> "list[tuple[str, int, list[int]]]":
    """(name, track_offset, channel indices) per 0x1014, read structurally (any session)."""
    rows: "list[tuple[str, int, list[int]]]" = []
    ref = _FI.final_index_ref(data)
    for z, _e, ct in sorted(_raw_block_bounds(data, ref.start)):
        if ct != 0x1014:
            continue
        nlen = int.from_bytes(data[z + 9 : z + 13], "little")
        name = _decode_name(data[z + 13 : z + 13 + nlen])
        toff = z + 7                      # block content start (the 0x1014 word)
        rows.append((name, toff, track_channel_indices(data, toff)))
    return rows


def _assign_free_channels(data: bytes, name: str) -> bytes:
    """Give the track called `name` the lowest channel indices no other track uses."""
    rows = _track_1014_rows(data)
    target = None
    used: set[int] = set()
    for nm, toff, chans in rows:
        if nm == name and target is None:
            target = (toff, len(chans))
        else:
            used.update(chans)
    if target is None:
        return data
    toff, nchan = target
    free: list[int] = []
    c = 0
    while len(free) < nchan:
        if c not in used:
            free.append(c)
        c += 1
    return set_track_channels(data, toff, free)


# --- arbitrary track naming --------------------------------------------------

def track_name_occurrences(data: bytes, name: str | bytes) -> list[int]:
    """Offsets of every length-prefixed occurrence of a track name. A track name
    lives in 8 places (0x1014, 0x1052 x2, 0x251a x2, 0x210b, the 0x2519 name-table
    entry, and 0x2619), each preceded by a u32 = name length. The length-prefix
    test distinguishes a real name slot from a coincidental substring (and
    'Audio 1' from 'Audio 10')."""
    needle = name.encode() if isinstance(name, str) else bytes(name)
    out: list[int] = []
    pos = 0
    while True:
        j = data.find(needle, pos)
        if j < 0:
            break
        if j >= 4 and int.from_bytes(data[j - 4 : j], "little") == len(needle):
            out.append(j)
        pos = j + 1
    return out


def rename_track(data: bytes, old_name: str | bytes, new_name: str | bytes) -> bytes:
    """Rename a track in a full unxored session. Replaces every length-prefixed
    occurrence of `old_name` with `new_name`, updates each occurrence's u32 length
    prefix, grows/shrinks every containing block's `block_size` (and its
    ancestors, via containment), then recomputes the index offsets for the shifted
    layout. Returns the new unxored session (ready for `writer.encrypt_session_data`)."""
    old = old_name.encode() if isinstance(old_name, str) else bytes(old_name)
    new = new_name.encode() if isinstance(new_name, str) else bytes(new_name)
    if old == new:
        return data
    import bisect
    delta = len(new) - len(old)
    ref = _FI.final_index_ref(data)
    old_index_start = ref.start
    # SIZE-DRIVEN throughout. The scan parser (parse/flat_blocks, _FI.offset_holes)
    # undercounts real sessions and RAISES on some MIDI-clip ones (`bad element tag`).
    # Capture the index holes as (abs_pos, target_offset) from the ORIGINAL layout;
    # reindex is a pure offset-shift (no block is added or removed), so a target at
    # old offset o moves by delta per occurrence that precedes it.
    holes, _zm = _index_offset_holes(data)
    body = bytearray(data[:old_index_start])
    index = data[old_index_start:]
    occ = track_name_occurrences(bytes(body), old)
    if not occ:
        raise ValueError(f"track name {old!r} not found (length-prefixed) in body")
    # block bounds from the ORIGINAL full session (body alone has no index for
    # final_index_ref to anchor on); every bound lies in the body region we mutate.
    snapshot = _size_driven_blocks(data)
    # Bump every containing block's size by (occurrences within it) * delta. A block
    # contains an occurrence iff the name offset lies within [zmark, end); ancestors
    # contain their descendants' occurrences, so this also grows them.
    for z, e, _c in snapshot:
        cnt = sum(1 for j in occ if z <= j < e)
        if cnt:
            sz = int.from_bytes(body[z + 3 : z + 7], "little")
            body[z + 3 : z + 7] = (sz + cnt * delta).to_bytes(4, "little")
    # Also adjust content-internal path-wrapper length fields (`5a 0a 00 <u32 size>`)
    # that span a renamed occurrence -- opaque embedded session-folder paths inside a
    # 0x261c (and 0x2067/0x200b) that the block walk does not descend into. A stale
    # wrapper size leaves Pro Tools reading past the data -> "end of stream". (No-op
    # for track renames, whose occurrences never fall inside a path wrapper.)
    parsed_zmarks = {z for z, _e, _c in snapshot}
    scan = 0
    while True:
        k = body.find(b"\x5a\x0a\x00", scan)
        if k < 0:
            break
        scan = k + 1
        if k in parsed_zmarks:
            continue  # a real (already-bumped) block, not an opaque path wrapper
        size = int.from_bytes(body[k + 3 : k + 7], "little")
        cstart, cend = k + 7, k + 7 + size
        if size <= 0 or cend > len(body):
            continue
        cnt = sum(1 for j in occ if cstart <= j < cend)
        if cnt:
            body[k + 3 : k + 7] = (size + cnt * delta).to_bytes(4, "little")
    # Apply edits high->low so lower offsets stay valid: fix length prefix, splice.
    for j in sorted(occ, reverse=True):
        body[j - 4 : j] = len(new).to_bytes(4, "little")
        body[j : j + len(old)] = new
    # Reattach the index and rewrite each hole's offset for the shifted layout.
    occ_sorted = sorted(occ)

    def _shift(o: int) -> int:                       # occurrences strictly before o
        return o + delta * bisect.bisect_left(occ_sorted, o)

    out = bytearray(bytes(body) + index)
    new_index_start = len(body)
    for abs_pos, target in holes:
        npos = new_index_start + (abs_pos - old_index_start)
        out[npos : npos + 4] = _shift(target).to_bytes(4, "little")
    # The first block stores the index's absolute offset, which moved with the
    # body-size change; rewrite it (else "magic ID does not match").
    return _set_index_offset(bytes(out))


def set_track_names(data: bytes, names) -> bytes:
    """Rename the first `len(names)` tracks (in track order) via `rename_track`. `names`
    is a list (or per-track dict {index: name}); `None`/missing entries are left as-is.
    Captures the current track names first, so it works whether the tracks still have
    their default `Audio N` names or were renamed earlier (and AFTER clips are placed —
    rename_track renames the track plus its placement lanes consistently). Returns unxored
    bytes."""
    current = [t.name for t in track_types(data)]
    if isinstance(names, dict):
        items = names.items()
    else:
        items = enumerate(names)
    out = data
    for i, name in items:
        if name is None or not (0 <= i < len(current)) or current[i] == name:
            continue
        out = rename_track(out, current[i], name)
    return out


def session_name(data: bytes) -> str | None:
    """The embedded session name (the length-prefixed `…​.ptx` string in the
    0x2067 session-info block). Pro Tools titles a session by its filesystem
    filename, so this is metadata, but a synthesized session should carry a
    sensible value. Returns None if not found."""
    ptf = parse(data)
    blocks = [b for b in flat_blocks(ptf) if b.content_type == 0x2067]
    if not blocks:
        return None
    b = blocks[0]
    blk = data[b.offset : b.offset + b.block_size]
    # 0x2067 can hold several `.ptx` references (save-as/template remnants); the
    # primary session name is the FIRST length-prefixed one.
    search = 0
    while True:
        dot = blk.find(b".ptx", search)
        if dot < 0:
            return None
        end = dot + 4
        for start in range(end - 1, max(0, end - 260) - 1, -1):
            if start >= 4 and int.from_bytes(blk[start - 4 : start], "little") == end - start:
                return blk[start:end].decode("latin-1")
        search = end


# --- track-type enumeration (mono / stereo / MIDI) --------------------------
# A track declares its type via its header block:
#   audio -> 0x1014 channel map; the byte after `<type:2><namelen:4><name>` is
#            nchan-1 and the next is nchan (1=mono, 2=stereo).
#   MIDI  -> 0x1057 header (no audio channel map, no 0x1052 lanes).
# (Buses/master/click are not 0x1014/0x1057 audio/MIDI tracks and are skipped.)
_AUDIO_HDR = 0x1014
_MIDI_HDR = 0x1057


@dataclass
class TrackInfo:
    name: str
    kind: str         # "mono" | "stereo" | "midi"
    channels: int     # 2 stereo, 1 mono, 0 midi
    offset: int       # header block offset (for ordering / locating)


def _hdr_name(data: bytes, b: Block) -> tuple[str, int]:
    """(name, namelen) for a 0x1014/0x1057 header block."""
    nlen = int.from_bytes(data[b.offset + 2 : b.offset + 6], "little")
    name = _decode_name(data[b.offset + 6 : b.offset + 6 + nlen])
    return name, nlen


def track_types(data: bytes) -> list[TrackInfo]:
    """Enumerate audio/MIDI tracks with their type, in header-offset order.

    Mono vs stereo is read from the 0x1014 channel-count byte; MIDI is any
    0x1057 header. This is the structural basis for mix-and-match composition
    (you must know each track's type + channel footprint to place it)."""
    ptf = parse(data)
    out: list[TrackInfo] = []
    for b in flat_blocks(ptf):
        if b.content_type == _AUDIO_HDR:
            name, nlen = _hdr_name(data, b)
            nchan = data[b.offset + 6 + nlen + 1]
            out.append(TrackInfo(name, "stereo" if nchan == 2 else "mono", nchan, b.offset))
        elif b.content_type == _MIDI_HDR:
            name, _ = _hdr_name(data, b)
            out.append(TrackInfo(name, "midi", 0, b.offset))
    out.sort(key=lambda t: t.offset)
    return out


def channel_count(data: bytes) -> int:
    """Total audio channels (mono=1, stereo=2 per audio track). Mirrors the
    session field at 0x1054 + 2."""
    return sum(t.channels for t in track_types(data))


def set_track_channels(data: bytes, track_offset: int, channels: list[int]) -> bytes:
    """Rewrite the channel indices in a track's 0x1014 channel map (whose content
    starts at `track_offset`). `channels` is the absolute channel index list:
    `[c0]` for mono, `[c0, c1]` for stereo. This is what mix-and-match composition
    uses to place a track at its cumulative channel allocation: a track unit sourced
    by index from a uniform control carries that control's channel numbers, so they
    must be rewritten to the target session's allocation.

    Channel indices live ONLY in 0x1014 (the 0x261c playlist does NOT encode them).
    Positions are relative to the name length (pre-marker) and the 0x2a marker
    (post-ID), so they are correct for BOTH mono and stereo — a mono 0x1014 is 2
    bytes shorter before the marker, so its post-ID channel sits at a different
    absolute offset (marker+19 for both; the bug that mis-wrote mono as if stereo)."""
    out = bytearray(data)
    nlen = int.from_bytes(out[track_offset + 2 : track_offset + 6], "little")
    # BOUND the marker search to this block. `track_offset` is the block's content start
    # (`zmark + 7`), so its declared size sits in the four bytes just before it and the block ends
    # at `track_offset + size`. Unbounded, `find` walks into the NEXT block when this one carries no
    # `2a 00 00 00` tag, and the two channel writes below (`marker + 19`, `marker + 23`) then land in
    # a neighbour's header -- observed writing `40 00` over a 0x1014's size/content-type word, which
    # unframed that block so the walk lost it (0x1014 count stayed 57 instead of going to 58) and
    # duplicate->remove came back 2 bytes short of byte-exact on 2 of 40 real sessions.
    _blk_end = track_offset + int.from_bytes(out[track_offset - 4 : track_offset], "little")
    marker = out.find(b"\x2a\x00\x00\x00", track_offset, max(track_offset, _blk_end))
    # Channel indices are u16 (the field's high byte is 0 below 256 but in use from
    # 256 up -- i.e. stereo track >=129, channel index 2k-2 >= 256). Writing a single
    # byte truncated them and raised at N>=129. Pre-marker pair is contiguous (stride
    # 2); the post-ID pair is stride 4.
    # The post-ID offsets `marker + 19` / `marker + 23` come from the control corpus and DO NOT hold
    # for every real 0x1014. On `20230209 GENE GOO GOO DOLLS.ptx` the marker sits legitimately inside
    # the block (221957 < 221970) but `marker + 19` is 221976 -- SIX BYTES PAST the block's end --
    # so the channel value `40 00` was written over the NEXT block's size/content-type word. That
    # unframed the neighbour, the walk lost it (0x1014 count stayed 57 instead of 58), and
    # duplicate->remove came back 2 bytes short of byte-exact.
    #
    # Guarded by CONTAINMENT, which needs no theory about where the field really lives: a write that
    # would leave this block is skipped. The pre-marker channel, whose offset is derived from the
    # name length and is always in-block, is unaffected.
    c0 = int(channels[0]).to_bytes(2, "little")
    out[track_offset + nlen + 11 : track_offset + nlen + 13] = c0   # channel0 pre-marker
    if marker >= 0 and marker + 21 <= _blk_end:
        out[marker + 19 : marker + 21] = c0                          # channel0 post-ID
    if len(channels) > 1:
        c1 = int(channels[1]).to_bytes(2, "little")
        out[track_offset + nlen + 13 : track_offset + nlen + 15] = c1  # channel1 pre-marker
        if marker >= 0 and marker + 25 <= _blk_end:
            out[marker + 23 : marker + 25] = c1                        # channel1 post-ID
    return bytes(out)


def track_channel_indices(data: bytes, track_offset: int) -> list[int]:
    """Read a track's 0x1014 channel indices ([c0] mono / [c0, c1] stereo)."""
    nlen = int.from_bytes(data[track_offset + 2 : track_offset + 6], "little")
    nchan = data[track_offset + 6 + nlen + 1]
    base = track_offset + nlen + 11
    u16 = lambda o: int.from_bytes(data[o : o + 2], "little")  # channel index is u16 (see set_track_channels)
    return [u16(base)] if nchan != 2 else [u16(base), u16(base + 2)]


def set_session_name(data: bytes, new_name: str) -> bytes:
    """Set the embedded session name. Accepts a name with or without the `.ptx`
    suffix. Implemented via `rename_track` (the session name is a single
    length-prefixed string in 0x2067), so it handles the body-size shift + index
    + index-offset pointer the same way."""
    cur = session_name(data)
    if cur is None:
        raise ValueError("no 0x2067 session name found")
    new = new_name if new_name.endswith(".ptx") else new_name + ".ptx"
    return rename_track(data, cur, new)
