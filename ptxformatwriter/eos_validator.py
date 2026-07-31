"""Pre-write end-of-stream validator for Pro Tools ``.ptx`` session bodies.

Pro Tools reads a session as a tree of *size-bounded* objects. When an object's
reader consumes MORE bytes than the object's own declared ``size``, or a
count-driven inner walk runs a length field onto unrelated bytes, the reader
raises:

    "Could not complete your request because end of stream encountered."

Our own block reader is *size-driven*: it walks blocks by their declared size and
skips any leftover, so it accepts files Pro Tools rejects. This module instead
reproduces the *count-driven* read of the objects whose grammar carries an internal
element count, so we can catch that failure BEFORE writing.

``simulate(data, blocks)`` returns ``None`` when the session would read to a clean
EOF, or a dict pinpointing the object (content-type + file offset) and the field
that overruns — the object an add/remove/duplicate edit failed to keep in sync.

WIRE FORMAT (little-endian). Every object begins with a 9-byte header:

    +0  u8   marker == 0x5A
    +1  u16  format_version
    +3  u32  size          (from +7 on; object spans [zmark, zmark + 7 + size))
    +7  u16  content_type
    +9  ...  payload

NAME TABLE (content_type 0x2519) — the add/remove/duplicate-track failing path.
Its payload is a version-keyed preamble (first entry at payload +0x14 for
format_version >= 8, else +0x16), then N inline name entries, then a run of
0x5A-framed children. Each name entry is::

    u32 name_length | name[name_length] | 23-byte trailer

with a ``0x0000002A`` (u32 == 42) marker at trailer offset +6. That fixed marker
lets the walk confirm each entry is framed correctly WITHOUT interpreting the
(possibly non-ASCII) name bytes — which is what keeps it sound across sessions. An
entry whose ``name_length`` runs the trailer past the object end, or whose trailer
marker is absent, is the exact end-of-stream fault: a track's name entry was left
out of sync (a stripped-but-not-removed name, or a trailer-less inserted entry).
"""

from __future__ import annotations

import struct


MARKER = 0x5A
CT_MASTER_INDEX = 0x0002
CT_NAME_TABLE = 0x2519          # inline name-entry list (the failing path)
CT_TRACK_LIST = 0x2624         # count-prefixed track-list container

NAME_ENTRY_TRAILER = 23        # bytes after the name string (u32 ordinal form)
NAME_ENTRY_TRAILER_SHORT = 21  # ...with a u16 ordinal instead of u32
TRAILER_MARK_OFF = 6           # offset of the 0x0000002A marker inside the trailer
TRAILER_MARK = 0x2A
ENTRY_START_BASE = 0x14        # first name entry when the preamble carries no extra field
ENTRY_COUNT_OFF = 0x0E         # u16 inline-entry count (same +2 shift as the entry start)
PREAMBLE_FLAG_OFF = 0x0A       # u32: 0 = base preamble, 1 = +2-byte extended preamble
PREAMBLE_EXTRA = 2             # bytes the extended preamble adds before the count + entries


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _frames_entry_head(data: bytes, q: int, e: int) -> bool:
    """True if a name entry's HEAD (`u32 len | printable name`) begins at `q`, without
    requiring the trailer's +6 field to be 0x2A. See `check_name_table` for why that field
    cannot be used as a frame marker."""
    if q + 4 > e:
        return False
    n = _u32(data, q)
    if not (0 < n <= 64) or q + 4 + n + NAME_ENTRY_TRAILER > e:
        return False
    return all(0x20 <= b < 0x7F for b in data[q + 4 : q + 4 + n])


def _frames_entry(data: bytes, q: int, e: int) -> bool:
    """True if a name entry (`u32 len | printable name | trailer with 0x2A marker`) begins at
    `q` within object end `e`. Used to disambiguate the 23- vs 21-byte trailer stride."""
    if q + 4 > e:
        return False
    n = _u32(data, q)
    if not (0 < n <= 64) or q + 4 + n + TRAILER_MARK_OFF + 4 > e:
        return False
    if not all(0x20 <= b < 0x7F for b in data[q + 4 : q + 4 + n]):
        return False
    return _u32(data, q + 4 + n + TRAILER_MARK_OFF) == TRAILER_MARK


class EndOfStream(Exception):
    """Raised when a read would advance past the enclosing object's declared end.
    Carries the offending object's content_type, file offset, field, and detail."""

    def __init__(self, content_type: int, offset: int, field: str, detail: str):
        self.content_type = content_type
        self.offset = offset
        self.field = field
        self.detail = detail
        super().__init__(str(self))

    def __str__(self) -> str:
        return (f"end of stream: object content_type=0x{self.content_type:04x} "
                f"at file offset {self.offset} (0x{self.offset:x}); "
                f"overran on {self.field}: {self.detail}")

    def as_dict(self) -> dict:
        return {"content_type": self.content_type, "offset": self.offset,
                "field": self.field, "detail": self.detail}


def _top_level_children(blocks, z: int, e: int):
    """Direct (one-level) framed children of the object at [z, e)."""
    inner = sorted((zz, ee, cc) for (zz, ee, cc) in blocks if z < zz < e)
    top = []
    for (zz, ee, cc) in inner:
        if not any(a < zz < b for (a, b, _) in top):
            top.append((zz, ee, cc))
    return top


def check_name_table(data: bytes, blocks, z: int, e: int) -> int:
    """Walk the inline name-entry list the way the reader does. Each entry is
    ``u32 name_length | name | 23-byte trailer`` with a 0x2A marker at trailer +6.
    Raises EndOfStream on the offending entry; returns the entry count otherwise."""
    payload = z + 9
    # The preamble has two lengths, selected by a u32 flag at payload+0x0A (0 = base, 1 = +2
    # bytes). Both the entry count and the first entry shift by that amount. This is NOT keyed
    # by the block's +1 word (the block subtype): sessions with the SAME subtype 9 carry both
    # preambles -- a synth starts its entries at 0x14 while a real clip-bearing session starts
    # at 0x16, which made the old subtype-keyed guess misread real sessions' name tables as a
    # bogus multi-hundred-KB name_length. That rule was verified on 436 LOCAL sessions (362 base /
    # 74 extended) and reported as having no exceptions -- see the flag-is-a-count note below for
    # what a local-only sample missed.
    # THE FLAG IS A COUNT, NOT A BOOLEAN. This read `PREAMBLE_EXTRA if flag else 0`, i.e. two-way:
    # base, or base+2. Measured over 402 sessions on four drives the flag takes THREE values --
    # 0 (255 sessions), 1 (141) and 2 (6) -- and a flag of 2 needs base+4, not base+2. Treating it
    # as a boolean started the entry walk 2 bytes early on those, so the u32 length read straddled
    # the real length and the field before it: the reported name_length came out an exact multiple
    # of 0x10000 (a name of 7 read as 0x00070000 = 458752), and `validate` raised a false
    # end-of-stream.
    #
    # Effect on the walk itself: clean on 394 of 402 with the boolean, 400 of 402 with `2 * flag`.
    # `validate()` fired on 25 of 75 PT-authored sessions from the external drives -- while its own
    # docstring claimed "no false positives" -- and on 0 of 197 in `control_files`, which is why the
    # rule survived: its comment says "verified on 436 LOCAL sessions", and locally it is true.
    shift = PREAMBLE_EXTRA * _u32(data, payload + PREAMBLE_FLAG_OFF)
    start = ENTRY_START_BASE + shift
    # Walk exactly the STORED entry count -- the way Pro Tools reads it -- not "until the first
    # child". A trackless session (a tempo/meter-only control) stores count 0 and its name table
    # holds no entries; the until-child heuristic instead read whatever non-entry bytes followed
    # and misread a u32 (e.g. 0x01000000) as a bogus name_length -> a FALSE end-of-stream on 17
    # trackless corpus sessions. A count that is too LARGE for the entries still raises below
    # (the read runs into the child block / past object end), so real desync is still caught.
    count = struct.unpack_from("<H", data, payload + ENTRY_COUNT_OFF + shift)[0]

    children = _top_level_children(blocks, z, e)
    entries_end = children[0][0] if children else e   # entries end at the first framed child

    q = payload + start
    idx = 0
    while idx < count:
        if q + 4 > e:
            raise EndOfStream(CT_NAME_TABLE, z, f"name[{idx}].length",
                              f"length field at 0x{q:x} would read past object end 0x{e:x}")
        name_length = _u32(data, q)
        entry_end = q + 4 + name_length + NAME_ENTRY_TRAILER
        if entry_end > e:
            raise EndOfStream(
                CT_NAME_TABLE, z, f"name[{idx}].name_length={name_length}",
                f"entry at 0x{q:x} with name_length={name_length} runs to 0x{entry_end:x}, "
                f"past object end 0x{e:x} (a track's name entry was not added/removed "
                f"to match the edit)")
        # THE +6 FIELD IS A VALUE, NOT A FRAME MARKER. This raised whenever it was not 0x2A.
        # Measured over 10035 entries in 317 PT-authored sessions on four drives, 359 (3.58%)
        # hold something else -- the trailer's 16 bytes at +2 are one field, and where most
        # entries store `00000000 2A000000 <8 bytes>` these store a full 16-byte id. The entry
        # is the same 23 bytes and the walk is NOT desynced: for 350 of those 359 the next
        # entry still frames, and for 336 the ordinal at +18 continues the run unbroken.
        # Treating the field as a marker made `validate()` -- whose docstring claimed no false
        # positives -- fire on 36 of 317 PT-authored sessions, all on this check.
        #
        # Desync is still caught: a walk that has lost sync does not land on a framed entry, so
        # only raise when the +6 field is unexpected AND the next entry fails to frame.
        #
        # THE COST, MEASURED, NOT WAVED AWAY. Against deliberate in-place corruptions of 60
        # control sessions (stored count +5; first name_length +4; first name_length -1) the
        # old hard check caught 60/60, 58/60, 58/60 and this one catches 59/60, 55/60, 55/60.
        # So ~5% of synthetic desyncs now slip through, in exchange for the false-positive rate
        # going from 36/317 to 0/317 on PT-authored sessions. Also measured and REJECTED: additionally
        # requiring the +18 ordinal to continue the run. It detects no more (80/80, 79/80, 79/80
        # either way on a same-harness comparison) and takes false positives from 1/317 to 7/317.
        mark_at = q + 4 + name_length + TRAILER_MARK_OFF
        if _u32(data, mark_at) != TRAILER_MARK:
            after = q + 4 + name_length + NAME_ENTRY_TRAILER
            in_sync = (idx + 1 >= count or after >= entries_end
                       or _frames_entry_head(data, after, e))
            if not in_sync:
                raise EndOfStream(
                    CT_NAME_TABLE, z, f"name[{idx}].trailer",
                    f"entry at 0x{q:x} (name_length={name_length}) is misframed: the trailer "
                    f"field at 0x{mark_at:x} is 0x{_u32(data, mark_at):08x} (not the usual "
                    f"0x{TRAILER_MARK:08x}) AND no name entry frames at 0x{after:x}, so the "
                    f"name-entry list is out of sync with the track edit")
        # The trailer's ordinal is a u32 in almost every entry but a u16 in some, so the stride
        # is 23 OR 21 (see body_synth._next_name_entry -- bug #14). Advance by the long form,
        # but fall back to the short form when long does not land on a valid next entry, so a
        # PT-authored 21-byte entry (e.g. robust_audio_mixed12) is not misread as a bogus length.
        entry_end_short = q + 4 + name_length + NAME_ENTRY_TRAILER_SHORT
        if (entry_end < entries_end and not _frames_entry(data, entry_end, e)
                and _frames_entry(data, entry_end_short, e)):
            entry_end = entry_end_short
        q = entry_end
        idx += 1

    # The preamble stores an explicit inline-entry count (at the same +2 shift as the entries);
    # the reader reads exactly that many. A stale count reads one entry too many (past the last
    # real entry, into the framed children) or stops short — an overrun either way.
    stored = _u16(data, payload + ENTRY_COUNT_OFF + shift)
    if stored != idx:
        raise EndOfStream(
            CT_NAME_TABLE, z, "preamble.entry_count",
            f"preamble entry_count={stored} but {idx} inline entries are present "
            f"(a track's name entry was added/removed without updating the count)")
    return idx


def check_track_container(data: bytes, blocks, z: int, e: int) -> int:
    """The track-list payload begins with a u32 item_count followed by that many framed
    track objects. A count exceeding the framed children is a count-driven overrun."""
    payload = z + 9
    if payload + 4 > e:
        raise EndOfStream(CT_TRACK_LIST, z, "container.item_count",
                          "item_count field runs past object end")
    item_count = _u32(data, payload)
    children = _top_level_children(blocks, z, e)
    if item_count > len(children):
        raise EndOfStream(
            CT_TRACK_LIST, z, f"container.item_count={item_count}",
            f"item_count={item_count} exceeds the {len(children)} framed child objects the "
            f"container holds (a track was removed without decrementing the list count)")
    return item_count


def simulate(data: bytes, blocks, index_start: "int | None" = None) -> "dict | None":
    """Reproduce the reader's count-driven walk over the object set. `blocks` is the
    authoritative block set as ``(start, end, content_type)`` tuples (the size-driven
    walk). Returns ``None`` for a clean EOF, else a dict ``{content_type, offset, field,
    detail}`` naming the object and field where the reader hits end of stream."""
    ordered = sorted(blocks)
    try:
        for (z, e, ct) in ordered:
            if ct == CT_NAME_TABLE:
                check_name_table(data, ordered, z, e)
            elif ct == CT_TRACK_LIST:
                check_track_container(data, ordered, z, e)
    except EndOfStream as eos:
        return eos.as_dict()
    return None
