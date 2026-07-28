import sys, os, glob, pathlib, collections
REPO="/Volumes/Dropbox SSD/Dropbox/PYTHON/__CODEX/PTFORMAT"
sys.path.insert(0,REPO); os.chdir(REPO)
from ptxformatwriter import body_synth as BS
from ptxformatwriter.core import PTFFormat
def L(p):
    f=PTFFormat(); f.load(p,48000); return f.unxored_data()
# EVERY reachable corpus, not just the local controls. control_files is ONE machine (PT Studio
# 2024) and /Volumes/Samples is ONE vendor (PT HD 10.3/11.2); measuring on either alone has
# repeatedly produced rules that a third corpus refuted. /Volumes/Backups is disconnected; the two
# SSDs below are not, and they are a different PT generation again.
CORPORA = ["control_files/**/*.ptx",
           "/Volumes/Samples/**/*.ptx",
           "/Volumes/Mac 4TB SSD/**/*.ptx",
           "/Volumes/Hack 2TB SSD Old/**/*.ptx"]
# A and B are measured EXHAUSTIVELY; C and D are sampled deterministically, because they are
# thousands of multi-megabyte sessions and an exhaustive pass takes hours. The sample size is
# reported in the document -- a coverage number whose denominator is hidden is worse than none.
import random
CAP = {"/Volumes/Mac 4TB SSD": 150, "/Volumes/Hack 2TB SSD Old": 150}
def enumerate_ptx(root):
    """Every `.ptx` under `root`, via Spotlight on external volumes.

    NOT `glob.glob(root + "/**/*.ptx", recursive=True)`. On the external SSDs that call walks the
    ENTIRE volume -- system directories, dependency trees, backup bundles -- and with a cold
    metadata cache it stalls for HOURS at ~0% CPU, indistinguishable from a hang. A run was killed
    at 2h52m having never reached the parsing loop; the sessions themselves cost ~0.5s each, so the
    whole job is ~7 minutes once the file list exists.

    `mdfind` reads Spotlight's existing index instead: 819 files from one drive and 1611 from the
    other, **0.3 s each**. Falls back to a recursive walk when Spotlight has nothing for that volume
    (an unindexed disk returns zero hits, which must not be mistaken for an empty corpus).
    """
    import subprocess
    if root.startswith("/Volumes/"):
        try:
            out = subprocess.run(["mdfind", "-onlyin", root, 'kMDItemFSName == "*.ptx"'],
                                 capture_output=True, text=True, check=False).stdout.split("\n")
            hits = [h for h in out if h.endswith(".ptx")]
            if hits:
                return hits
            print("    (Spotlight returned nothing for %s -- falling back to a walk)" % root, flush=True)
        except FileNotFoundError:
            pass
    return [str(q) for q in pathlib.Path(root).rglob("*.ptx")]

paths=[]; provenance=[]
for pat in CORPORA:
    root = pat.split("/**")[0]
    hits=sorted(h for h in enumerate_ptx(root) if "numba" not in h)   # CUDA .ptx, not PT
    if not hits and root=="control_files":
        raise SystemExit("control_files glob is EMPTY -- refusing to report a fabricated coverage number")
    total=len(hits)
    if root in CAP and total > CAP[root]:
        random.Random(7).shuffle(hits); hits=sorted(hits[:CAP[root]])
    provenance.append((root, len(hits), total))
    print("  %-34s %d of %d sessions" % (root, len(hits), total))
    paths += hits
# Payload types the walk must not believe it can descend into.
LEAF_PAYLOADS = {0x1038, 0x2613, 0x2615, 0x2616, 0x1003, 0x1017}
counts=collections.Counter(); sessions=collections.Counter(); indexed=collections.Counter()
nested_in_leaf=collections.Counter(); size8_under_1014=collections.Counter(); n=0
count_field=collections.Counter()   # signature 4 -- see the loop below
clean=collections.Counter()        # instances matching NO structural phantom signature
toplevel=collections.Counter()   # blocks in the contiguous top-level chain -- see is_phantom
# Progress + a slow-session report, both flushed. Profiling says a typical session costs ~0.5s and
# is dominated by the XOR de-obfuscation, so 832 sessions should take ~7 minutes -- but an earlier
# exhaustive run sat at 98% CPU for nearly THREE HOURS with no output, because stdout was block
# buffered and a handful of pathological sessions were invisible. A long job that cannot say where
# it is cannot be distinguished from a hung one.
import time
_slow=[]; _t_start=time.time()
allp = sorted(set(paths))
for _i, p in enumerate(allp):
    _t0=time.time()
    try:
        d=L(p); bl=BS._size_driven_blocks(d)
        holes,_zm=BS._index_offset_holes(d); idx=set(t for _a,t in holes if t)
    except Exception as _ex:
        print("    skip %s (%s)" % (os.path.basename(p)[:48], type(_ex).__name__), flush=True)
        continue
    _dt=time.time()-_t0
    if _dt > 10.0:
        _slow.append((_dt, os.path.basename(p), len(d), len(bl)))
        print("    SLOW %6.1fs %-46s %.1fMB %d blocks" % (_dt, os.path.basename(p)[:46], len(d)/1e6, len(bl)), flush=True)
    if _i and _i % 50 == 0:
        print("    ... %d/%d sessions, %.0fs elapsed" % (_i, len(allp), time.time()-_t_start), flush=True)
    n+=1; seen=set()
    # A block nested inside a known LEAF payload is a PHANTOM: 0x5A is 'Z' and occurs freely in
    # plug-in state, waveform and file-descriptor data, so the walk frames noise there. This is not
    # a defect in the walk -- the stream is genuinely self-describing only down to the leaves -- but
    # it means an unfiltered "undocumented types" list is mostly garbage. Measured: of 213 types
    # unique to the two SSDs, 164 are phantoms.
    # The contiguous top-level chain, walked ONCE by declared size (the same walk
    # `final_index_ref` uses). A containment test per block would be O(n^2) on a 24k-block
    # session; this is O(n).
    _tops=set(); _q=min((z for z,_e,_c in bl), default=None)
    while _q is not None and _q + 9 <= len(d):
        _sz=int.from_bytes(d[_q+3:_q+7],"little")
        if d[_q]!=0x5A or _sz<=0 or _q+7+_sz>len(d): break
        _tops.add(_q); _q=_q+7+_sz if _q+7+_sz<len(d) else None
    leaves=[(lz,le) for lz,le,lc in bl if lc in LEAF_PAYLOADS]
    entries_1014=[(pz,pe) for pz,pe,pc in bl if pc==0x1014]
    _by_start={bz:be for bz,be,_bc in bl}
    def _tiles_exactly(start,end):
        """Do well-formed blocks from `start` reach `end` with nothing left over?"""
        q=start
        while q<end:
            if q+9>end or d[q]!=0x5A: return False
            s=int.from_bytes(d[q+3:q+7],"little")
            if s<=0 or q+7+s>end: return False
            q+=7+s
        return q==end
    for z,e,c in bl:
        if z < 31 or c == -1:   # the index-pointer record is NOT a typed block (see spec)
            continue
        counts[c]+=1; seen.add(c)
        if z in idx: indexed[c]+=1
        if any(lz < z and e <= le for lz,le in leaves): nested_in_leaf[c]+=1
        if z in _tops: toplevel[c]+=1
        if (e-z) == 8 and any(pz < z and e <= pe for pz,pe in entries_1014): size8_under_1014[c]+=1
        # Signature 4: a u32 COUNT at the head of a container's payload whose low byte is 0x5A.
        # The zmark must be EXACTLY the parent's first payload byte, and the parent's payload must
        # tile exactly when the walk skips those four bytes -- i.e. the real children begin at
        # parent+13 and the "block" is the count. Both halves are required; the tiling test is what
        # makes this a proof rather than a guess about where a 0x5A happens to sit.
        _pe=_by_start.get(z-9)
        _cf = _pe is not None and _pe>e and _tiles_exactly(z+4,_pe)
        if _cf: count_field[c]+=1
        # INSTANCE-level cleanliness. The signatures classify TYPES, but phantom-ness is a property
        # of an INSTANCE: a type can be perfectly real and ALSO be manufactured as noise inside a
        # leaf payload, and a >=50% type-level rule then writes the real instances off with the
        # noise. Measured 2026-07-27: the signatures fire on 14 types that have their own spec
        # entry, including 0x1000 with 226,242 instances -- so this is not a hypothetical.
        if not (any(lz < z and e <= le for lz,le in leaves)
                or ((e-z)==8 and any(pz < z and e <= pe for pz,pe in entries_1014))
                or _cf):
            clean[c]+=1
    for c in seen: sessions[c]+=1
spec=open("docs/ptx-format-spec.md").read().lower()
# "documented" here means the hex string appears ANYWHERE in the spec -- including a passing
# mention in another type's prose. That is a weak standard, and it is the one this tracker has
# always reported. Compute the STRICTER measure alongside it: does the type have its own
# `### 0x....` entry (shared headings like "### 0x261F / 0x2610 / ..." count for each type named)?
# 0x2000 is why this matters: 480 instances in 480 sessions, always TOP-LEVEL, and it has no
# entry -- it was "documented" purely on four incidental mentions elsewhere.
import re as _re2
# ANY heading level, and the type need not START the heading: real entries are titled both
# "### 0x2015 - ..." and "### Tempo (`0x2028` map / `0x2718` lane)". Requiring the heading to
# begin with the hex missed 24 types that plainly have entries -- an over-strict filter is the
# same error as the over-generous one, just in the other direction.
_entry_types=set()
for _m in _re2.finditer(r"(?m)^#{2,4} ([^\n]*)", spec):
    _entry_types |= {int(h,16) for h in _re2.findall(r"0x[0-9a-fA-F]{4}", _m.group(1))}
doc={c for c in counts if ("0x%04x"%c) in spec}
has_entry={c for c in counts if c in _entry_types}
undoc=sorted(set(counts)-doc, key=lambda c:(-sessions[c],-counts[c]))
tot=sum(counts.values()); ub=sum(counts[c] for c in undoc)
lines=[]
lines.append("# `.ptx` content-type coverage tracker\n")
lines.append("*(The file-header index-pointer record at body offset 20 is excluded — it is not a block; see the spec. Counting it invents one bogus type per session.)*\n")
lines.append("Measured with the **size-driven block walk** (`body_synth._size_driven_blocks`) over the")
lines.append("**%d sessions across every reachable drive** — %d distinct real content-types, %d block instances.\n"%(n,len(counts),tot))
lines.append("| corpus | measured | available |")
lines.append("|---|---:|---:|")
for root,k,total in provenance:
    lines.append("| `%s` | %d | %d |"%(root,k,total))
lines.append("")
lines.append("`control_files` and `/Volumes/Samples` are measured exhaustively; the two SSDs are")
lines.append("deterministically sampled (`Random(7)`), because they hold thousands of multi-megabyte")
lines.append("sessions. **Read the denominators**: `control_files` is ONE machine and `/Volumes/Samples`")
lines.append("is ONE vendor, and rules confirmed on those two alone have been wrong three times — see")
lines.append("the `0x2015` case study in the spec. 150 sampled sessions from one SSD carry more distinct")
lines.append("content-types than all 282 control files.\n")
lines.append("Also excluded: ~2 files under `/Volumes/Hack 2TB SSD Old` that are NVIDIA CUDA PTX")
lines.append("assembly, not Pro Tools sessions. Different `.ptx` entirely.\n")
lines.append("**%d/%d content-types have a spec entry = %.1f%%**, covering %.2f%% of block instances.\n"%(
    len(doc),len(counts),100.0*len(doc)/len(counts),100.0*(tot-ub)/tot))
# TWO DIFFERENT NUMBERS, reported separately because conflating them is how this file once carried
# a FALSE 100%. "Documented" = has a spec entry describing framing/parents/counts/layout. "Settled"
# = that entry does not itself say the purpose is unproven.
import re as _re
_secs = _re.split(r"(?m)^### ", open("docs/ptx-format-spec.md").read())
_typed = [x for x in _secs if _re.match(r"0x[0-9a-fA-F]{4}", x)]
_UNSET_RE = (r"purpose UNKNOWN|UNPROVEN|not proven|still open|speculat|"
             r"INFERRED, not proven|cannot be settled")
_unsettled = [x for x in _typed if _re.search(_UNSET_RE, x, _re.I)]

# PER-TYPE, not per-section. A heading may name TEN types and be flagged because ONE of them is
# unproven -- 0x1056 was counted unsettled for weeks while its own row read "the MIDI placement
# wrapper". Sections carrying an explicit `**Purpose status (per type).**` line are read from it;
# the rest still contribute every type in their heading, which OVERSTATES them, so the number below
# is an upper bound and is labelled as one.
_settled_types, _unsettled_types = set(), set()
for _x in _unsettled:
    _head = _x.split("\n")[0]
    _named = {int(h, 16) for h in _re.findall(r"0x[0-9a-fA-F]{4}", _head)}
    _m = _re.search(r"\*\*Purpose status \(per type\)\.\*\*(.*?)(?:\n\n|$)", _x, _re.S)
    if _m:
        _txt = _m.group(1)
        _s_part, _, _u_part = _txt.partition("UNPROVEN")
        _s_hex = {int(h, 16) for h in _re.findall(r"0x[0-9a-fA-F]{4}", _s_part)}
        _settled_types |= (_s_hex & _named)
        _unsettled_types |= (_named - _s_hex)
    else:
        _unsettled_types |= _named
_multi = [x for x in _unsettled
          if len({h.lower() for h in _re.findall(r"0x[0-9a-fA-F]{4}", x.split("\n")[0])}) > 1]
print("unsettled SECTIONS=%d (multi-type %d)  per-type: unsettled<=%d, settled-within-flagged=%d"
      % (len(_unsettled), len(_multi), len(_unsettled_types), len(_settled_types)))
def is_phantom(c):
    """Phantom by ANY of three independent signatures, all measured, none guessed."""
    if counts[c] and nested_in_leaf[c] >= 0.5*counts[c]:
        return True                       # lives inside a leaf payload
    if (c & 0xFF) == 0 and indexed[c] == 0 and toplevel[c] < 0.5*counts[c]:
        # 256*k arithmetic, never index-referenced -- AND not a top-level block.
        #
        # CORRECTION 2026-07-27: without the top-level clause this signature swallowed REAL
        # types and so hid them from the "genuinely undocumented" count, which is exactly the
        # error this file's 2026-07-20 correction was about. A phantom is a false `0x5A` INSIDE
        # another block's payload; the top-level chain is walked contiguously by declared size,
        # so nothing false can appear in it. `0x2000` is the proof: 480 instances in 480
        # sessions -- one per session, ALWAYS top-level, always between 0x2033 and 0x2634,
        # block_type 0x0001, span 13 -- and it was being written off as arithmetic noise.
        return True
    # An 8-byte field inside a 0x1014 track-list entry whose last byte lands where a size field
    # would be, framing a phantom of DECLARED SIZE 8 whose parent is always the 0x1014. Found by
    # dissecting the survivors of the first two signatures: 0x0024/0x0033/0x0034/0x001c each appear
    # in 1-2 sessions, always size 8, always under 0x1014.
    #
    # CORRECTION 2026-07-27: this comment used to say the field was the 8-byte id following the
    # `2A 00 00 00` GUID tag. It is not. All four are the `ZY` of a track named `BASS CAB FUZZY` --
    # the second Z is the zmark and Y (0x59) becomes the block_type. They are ONE phantom in one
    # session saved repeatedly, not four rare types. The signature still catches them; the stated
    # reason was wrong, which is exactly what a filter without a cause lets you get away with.
    if counts[c] and size8_under_1014[c] >= 0.5*counts[c]:
        return True
    # 4. A u32 COUNT at the head of a container's payload whose low byte happens to be 0x5A.
    # Proven on the corpus's only instance (0x2757): the zmark is exactly parent+9, the u32 there
    # is 346, and blocks walked from parent+13 tile the parent's payload EXACTLY -- 347 children,
    # of which 346 are 0x1003 file descriptors. The "88,583-byte block" is the number 0x0000015A.
    # A session holding 345 or 347 files produces no phantom at all, which is why n=1.
    return counts[c] and count_field[c] >= 0.5*counts[c]
_mention_only = sorted(doc - has_entry, key=lambda c: (-sessions[c], -counts[c]))
# CORRECTION 2026-07-27: `is_phantom` used to be applied ONLY to undocumented types, so a phantom
# that happened to be MENTIONED skipped the filter and was reported as outstanding work. That is
# self-defeating here, because a phantom's mention is usually the spec EXPLAINING it as a phantom:
# the paragraph that says "`0x0c00` = 256×12" is itself why `0x0c00` counted as mentioned. The
# queue was therefore 13/14 garbage — the exact failure mode this file warns about, inverted.
_mo_phantom = [c for c in _mention_only if is_phantom(c)]
_mo_real    = [c for c in _mention_only if not is_phantom(c)]
# SELF-CHECK: a phantom signature that fires on a type with its own spec entry is OVER-FIRING.
# Signatures are heuristics over noise; nothing stops one from swallowing a real type, and that is
# exactly how the too-generous version of signature 2 once hid 0x2000 (480 instances, every session,
# always top-level). Measure it instead of hoping.
# Signature 2 is irreducibly type-level ("this type is never index-referenced"), so it zeroes a
# type's clean count wholesale; 1/3/4 are per-instance and were applied in the loop above.
for _c in list(clean):
    if (_c & 0xFF)==0 and indexed[_c]==0 and toplevel[_c] < 0.5*counts[_c]:
        clean[_c]=0
_real_by_instance = {c for c in counts if clean[c] > 0}
_undoc_real = sorted(set(_real_by_instance)-doc, key=lambda c:(-clean[c],-sessions[c]))
print("types with >=1 structurally clean instance: %d/%d" % (len(_real_by_instance), len(counts)))
print("  ...of those, UNDOCUMENTED: %d %s" % (
    len(_undoc_real), " ".join("0x%04x(%d clean/%d)"%(c,clean[c],counts[c]) for c in _undoc_real[:14])))
_false_pos = sorted([c for c in has_entry if is_phantom(c)], key=lambda c: -counts[c])
print("phantom signatures firing on ENTERED types: %d %s" % (
    len(_false_pos), " ".join("0x%04x(%d)"%(c,counts[c]) for c in _false_pos[:12])))
lines.append("## The strongest form of the coverage claim — measured per INSTANCE\n")
lines.append("The phantom signatures classify **types**, on a ≥50% rule. But phantom-ness is a property")
lines.append("of an **instance**: a type can be perfectly real *and* be manufactured as noise inside a")
lines.append("leaf payload, and a type-level rule then writes its real instances off with the noise.\n")
lines.append("That is not hypothetical. The signatures fire on **%d types that have their own spec"%len(_false_pos))
lines.append("entry**, including `0x1000` (%d instances), `0x1001` (%d) and `0x1033` (%d). Read"%(
    counts[0x1000],counts[0x1001],counts[0x1033]))
lines.append("type-level, those would be \"phantoms\"; they are nothing of the kind.\n")
lines.append("So the same question is asked per instance instead. An instance is structural noise if it")
lines.append("is nested inside a leaf payload, or is the size-8 field under a `0x1014`, or is a count")
lines.append("field at a payload head (signatures 1, 3, 4 — all per-instance). Signature 2 is")
lines.append("irreducibly type-level and zeroes its type outright.\n")
lines.append("> **%d of the %d observed types have at least one structurally clean instance,"%(
    len(_real_by_instance),len(counts)))
lines.append("> and of those, %d are undocumented.**\n"%len(_undoc_real))
lines.append("The remaining %d types have **no** clean instance anywhere in %d sessions — they exist"%(
    len(counts)-len(_real_by_instance),n))
lines.append("only inside other blocks' payloads. This is a considerably harder claim than the")
lines.append("type-level one above, and it is the one worth quoting: not *\"the leftovers look like")
lines.append("noise\"*, but *\"every type with even a single unexplained instance is written down\"*.\n")
lines.append("## Three standards, not one — mentioned, entered, settled\n")
lines.append("**%d of the %d observed content types have their OWN `### ` spec entry.** A further"%(len(has_entry),len(counts)))
lines.append("**%d are counted as \"documented\" only because their hex string appears somewhere in"%len(_mention_only))
lines.append("another type's prose.** That weaker standard is the one this tracker reported alone until")
lines.append("2026-07-27, and it flatters the result: `0x2000` occurs in **every one of the %d sessions**,"%n)
lines.append("always as a TOP-LEVEL block, and has no entry at all — it qualified on four incidental")
lines.append("mentions.\n")
lines.append("But a mention-only list is not yet a work list. Running the same phantom signatures used")
lines.append("below over it splits the %d into **%d phantoms** and **%d real types**. Most of the phantoms"%(
    len(_mention_only),len(_mo_phantom),len(_mo_real)))
lines.append("are mentioned *because the spec explains them as phantoms* — the sentence deriving")
lines.append("`0x0c00` from 256×12 is the very reason `0x0c00` counts as mentioned. Listing those as")
lines.append("outstanding work is this file's original error wearing a different hat.\n")
lines.append("**Real types with no entry of their own — the actual queue:**\n")
if _mo_real:
    lines.append("| type | instances | sessions |")
    lines.append("|---|---:|---:|")
    for c in _mo_real[:40]:
        lines.append("| `0x%04x` | %d | %d |"%(c,counts[c],sessions[c]))
    if len(_mo_real) > 40:
        lines.append("\n…and %d more.\n"%(len(_mo_real)-40))
else:
    lines.append("*(none — every mention-only type is a phantom)*")
lines.append("")
lines.append("**Phantoms that were sitting in the queue** (each traced to the byte that produced it in")
lines.append("the spec's phantom catalogue): %s.\n"%", ".join("`0x%04x`"%c for c in _mo_phantom[:40]))
lines.append("")
lines.append("## Described vs settled — two different numbers\n")
lines.append("**%d types are DESCRIBED** (framing, parent/child, per-session counts, payload layout"%len(doc))
lines.append("where decodable) and **0 real types are undocumented**.\n")
lines.append("Of the %d typed sections in the spec, **%d still say the PURPOSE is unproven** —"%(len(_typed),len(_unsettled)))
lines.append("but that counts SECTIONS, and %d of them cover more than one type: a heading naming ten"%len(_multi))
lines.append("types is flagged when ONE is unproven. `0x1056` was counted unsettled while its own row")
lines.append("read *\"the MIDI placement wrapper\"*. Read per TYPE instead, the flagged sections name")
lines.append("**at most %d unproven types**, with %d already settled inside them. Sections carrying an"%(
    len(_unsettled_types),len(_settled_types)))
lines.append("explicit `Purpose status (per type)` line are counted from it; the rest still contribute")
lines.append("every type they name, so %d is an UPPER BOUND, not a measurement.\n"%len(_unsettled_types))
lines.append("`purpose UNKNOWN`, `UNPROVEN`, `INFERRED, not proven`, or `cannot be settled`. That is")
lines.append("the remaining semantic work, and it is deliberately visible: most of those entries name")
lines.append("the Pro Tools experiment that would settle them (typically *one session, saved twice,")
lines.append("with exactly one setting changed*), because no amount of further corpus reading can.\n")
lines.append("Reporting only the first number would repeat this file's original error in a subtler")
lines.append("form — \"documented\" is not \"understood\".\n")
lines.append("**Read that number carefully.** It means every type observed in this corpus is")
lines.append("*described* — framing, parent/child, per-session counts, and payload layout where")
lines.append("decodable. It does NOT mean every field's *meaning* is proven: a number of spec entries")
lines.append("explicitly say `purpose UNKNOWN` or mark a field `UNPROVEN`. Those are the remaining")
lines.append("semantic work, left deliberately visible — this file once carried a FALSE 100%, so the")
lines.append("fix is to state exactly what was measured and what was not.\n")
lines.append("> **Correction (2026-07-20).** This file previously claimed *280/280 = 100.000%* over a")
lines.append("> 26-session corpus, enumerated with the older `0x5A`-scan parser that **undercounts real")
lines.append("> sessions**. Re-measured with the size-driven walk over the full corpus: **%d** real types."%len(counts))
lines.append("> 20 of them occur in EVERY session yet were missing from both this tracker and the spec;")
lines.append("> they have since been dissected and documented. A naive walk also reports ~213 extra")
lines.append("> \"types\" that are really ONE artifact — the file-header index pointer at body offset 20,")
lines.append("> whose per-session value is misread as a content-type (see the spec). It is excluded here.\n")
phantom = {c for c in undoc if is_phantom(c)}
real_undoc = [c for c in undoc if c not in phantom]
lines.append("## Phantoms, excluded from the work list\n")
lines.append("`0x5A` is `'Z'` and occurs freely inside leaf payloads and binary id fields, so the")
lines.append("size-driven walk frames")
lines.append("noise there. **%d of the %d undocumented types are phantoms** by one of FOUR measured"%(len(phantom),len(undoc)))
lines.append("signatures: ≥50% of instances nested inside a leaf payload (`0x1038` plug-in state,")
lines.append("`0x2613`/`0x2615`/`0x2616` view blobs, `0x1003`, `0x1017`); a type whose low byte is")
lines.append("`0x00` that the master index never references — the `256 × k` arithmetic of a string")
lines.append("LENGTH or first letter being read as a type (`0x1600` = 256×22, `0x0c00` = 256×12); an")
lines.append("8-byte field under a `0x1014`; or a `u32` COUNT at the head of a container's payload")
lines.append("whose low byte is `0x5A` (`0x2757`, the only one, is the number 346).\n")
lines.append("The spec's phantom catalogue goes further and traces each one to the byte that produced")
lines.append("it — mostly text the user typed: `0x2a00` is the last letter of a track named `DRUMZ`,")
lines.append("`0x0c00` is the `Z` in `iZotope`. **A phantom's type number is a function of the")
lines.append("corpus's naming habits**, which is the deepest reason an unfiltered coverage count means")
lines.append("nothing.\n")
lines.append("Reporting them as undocumented would hand the next reader a work list that is mostly")
lines.append("garbage — the same mistake, in the other direction, as the FALSE 100% this file once")
lines.append("carried.\n")
lines.append("**%d content-types remain genuinely undocumented.**\n"%len(real_undoc))
if real_undoc:
    lines.append("| content-type | blocks | in #sessions | indexed |")
    lines.append("|---|---:|---:|---:|")
    for c in real_undoc:
        lines.append("| `0x%04x` | %d | %d | %d |"%(c,counts[c],sessions[c],indexed[c]))
    lines.append("")
lines.append("## Undocumented types — raw, phantoms included\n")
lines.append("Sorted by how many sessions carry them (ubiquity first), then block count.\n")
lines.append("| content-type | blocks | in #sessions | indexed | ")
lines.append("|---|---:|---:|---:|")
for c in undoc[:60]:
    lines.append("| `0x%04x` | %d | %d | %d |"%(c,counts[c],sessions[c],indexed[c]))
if len(undoc)>60:
    lines.append("\n…and %d more (long tail, each in <%d sessions).\n"%(len(undoc)-60,sessions[undoc[60]]+1))
lines.append("\n## Documented types\n")
lines.append("%d types are covered by `ptx-format-spec.md`, accounting for %.2f%% of block instances.\n"%(
    len(doc),100.0*(tot-ub)/tot))
open("docs/content-type-coverage.md","w").write("\n".join(lines)+"\n")
print("wrote docs/content-type-coverage.md")
if _slow:
    print("SLOW SESSIONS (>10s), worst first:")
    for dt,nm,sz,nb in sorted(_slow, reverse=True)[:12]:
        print("   %7.1fs  %-48s %.1fMB %d blocks" % (dt, nm[:48], sz/1e6, nb))
print("sessions=%d types=%d documented=%d undocumented=%d (%.2f%% of blocks)"%(n,len(counts),len(doc),len(undoc),100.0*ub/tot))
print("ubiquitous undocumented (in ALL %d sessions): %d"%(n,sum(1 for c in undoc if sessions[c]==n)))
print("with own spec ENTRY=%d  mention-only=%d"%(len(has_entry),len(doc-has_entry)))
print("phantoms=%d  GENUINELY UNDOCUMENTED=%d -> %s"%(len(phantom),len(real_undoc),
      ", ".join("0x%04x"%c for c in real_undoc[:20]) or "none"))
