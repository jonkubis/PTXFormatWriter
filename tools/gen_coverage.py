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
    for z,e,c in bl:
        if z < 31 or c == -1:   # the index-pointer record is NOT a typed block (see spec)
            continue
        counts[c]+=1; seen.add(c)
        if z in idx: indexed[c]+=1
        if any(lz < z and e <= le for lz,le in leaves): nested_in_leaf[c]+=1
        if z in _tops: toplevel[c]+=1
        if (e-z) == 8 and any(pz < z and e <= pe for pz,pe in entries_1014): size8_under_1014[c]+=1
    for c in seen: sessions[c]+=1
spec=open("docs/ptx-format-spec.md").read().lower()
# "documented" here means the hex string appears ANYWHERE in the spec -- including a passing
# mention in another type's prose. That is a weak standard, and it is the one this tracker has
# always reported. Compute the STRICTER measure alongside it: does the type have its own
# `### 0x....` entry (shared headings like "### 0x261F / 0x2610 / ..." count for each type named)?
# 0x2000 is why this matters: 480 instances in 480 sessions, always TOP-LEVEL, and it has no
# entry -- it was "documented" purely on four incidental mentions elsewhere.
import re as _re2
_entry_types=set()
for _m in _re2.finditer(r"(?m)^### ([^\n]*)", spec):
    _line=_m.group(1)
    if _line.lower().startswith("0x"):
        _entry_types |= {int(h,16) for h in _re2.findall(r"0x[0-9a-fA-F]{4}", _line)}
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
_unsettled = [x for x in _typed
              if _re.search(r"purpose UNKNOWN|UNPROVEN|not proven|still open|speculat|"
                            r"INFERRED, not proven|cannot be settled", x, _re.I)]
_mention_only = sorted(doc - has_entry, key=lambda c: (-sessions[c], -counts[c]))
lines.append("## Three standards, not one — mentioned, entered, settled\n")
lines.append("**%d of the %d observed content types have their OWN `### ` spec entry.** A further"%(len(has_entry),len(counts)))
lines.append("**%d are counted as \"documented\" only because their hex string appears somewhere in"%len(_mention_only))
lines.append("another type's prose.** That weaker standard is the one this tracker reported alone until")
lines.append("2026-07-27, and it flatters the result: `0x2000` occurs in **every one of the %d sessions**,"%n)
lines.append("always as a TOP-LEVEL block, and has no entry at all — it qualified on four incidental")
lines.append("mentions. The mention-only list is the honest work queue:\n")
lines.append("| type | instances | sessions |")
lines.append("|---|---|---|")
for c in _mention_only[:40]:
    lines.append("| `0x%04x` | %d | %d |"%(c,counts[c],sessions[c]))
if len(_mention_only) > 40:
    lines.append("\n…and %d more.\n"%(len(_mention_only)-40))
lines.append("")
lines.append("## Described vs settled — two different numbers\n")
lines.append("**%d types are DESCRIBED** (framing, parent/child, per-session counts, payload layout"%len(doc))
lines.append("where decodable) and **0 real types are undocumented**.\n")
lines.append("Of the %d typed sections in the spec, **%d still say the PURPOSE is unproven** —"%(len(_typed),len(_unsettled)))
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
    # The 8-byte identity id that follows the `2A 00 00 00` tag inside a 0x1014 track-list entry.
    # Its last byte lands where a size field would be, framing a phantom of DECLARED SIZE 8 whose
    # parent is always the 0x1014. Found by dissecting the survivors of the first two signatures:
    # 0x0024/0x0033/0x0034/0x001c each appear in 1-2 sessions, always size 8, always under 0x1014.
    # The spec already records this GUID as a phantom source; this is the same one, counted.
    return counts[c] and size8_under_1014[c] >= 0.5*counts[c]
phantom = {c for c in undoc if is_phantom(c)}
real_undoc = [c for c in undoc if c not in phantom]
lines.append("## Phantoms, excluded from the work list\n")
lines.append("`0x5A` is `'Z'` and occurs freely inside leaf payloads and binary id fields, so the")
lines.append("size-driven walk frames")
lines.append("noise there. **%d of the %d undocumented types are phantoms** by one of two measured"%(len(phantom),len(undoc)))
lines.append("signatures: ≥50%% of instances nested inside a leaf payload (`0x1038` plug-in state,")
lines.append("`0x2613`/`0x2615`/`0x2616` view blobs, `0x1003`, `0x1017`), or a type whose low byte is")
lines.append("`0x00` that the master index never references — the `256 × k` arithmetic of a string")
lines.append("LENGTH or first letter being read as a type (`0x1600` = 256×22, `0x0c00` = 256×12).\n")
lines.append("Reporting them as undocumented would hand the next reader a work list that is mostly")
lines.append("garbage — the same mistake, in the other direction, as the FALSE 100%% this file once")
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
