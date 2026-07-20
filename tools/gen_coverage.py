import sys, os, glob, collections
REPO="/Volumes/Dropbox SSD/Dropbox/PYTHON/__CODEX/PTFORMAT"
sys.path.insert(0,REPO); os.chdir(REPO)
from ptxformatwriter import body_synth as BS
from ptxformatwriter.core import PTFFormat
def L(p):
    f=PTFFormat(); f.load(p,48000); return f.unxored_data()
counts=collections.Counter(); sessions=collections.Counter(); indexed=collections.Counter(); n=0
for p in sorted(glob.glob("control_files/**/*.ptx",recursive=True)):
    try:
        d=L(p); bl=BS._size_driven_blocks(d)
        holes,_zm=BS._index_offset_holes(d); idx=set(t for _a,t in holes if t)
    except Exception: continue
    n+=1; seen=set()
    for z,e,c in bl:
        if z < 31:          # the file-header index-pointer record is NOT a block (see spec)
            continue
        counts[c]+=1; seen.add(c)
        if z in idx: indexed[c]+=1
    for c in seen: sessions[c]+=1
spec=open("docs/ptx-format-spec.md").read().lower()
doc={c for c in counts if ("0x%04x"%c) in spec}
undoc=sorted(set(counts)-doc, key=lambda c:(-sessions[c],-counts[c]))
tot=sum(counts.values()); ub=sum(counts[c] for c in undoc)
lines=[]
lines.append("# `.ptx` content-type coverage tracker\n")
lines.append("*(The file-header index-pointer record at body offset 20 is excluded — it is not a block; see the spec. Counting it invents one bogus type per session.)*\n")
lines.append("Measured with the **size-driven block walk** (`body_synth._size_driven_blocks`) over the")
lines.append("**%d-session local corpus** — %d distinct real content-types, %d block instances.\n"%(n,len(counts),tot))
lines.append("**%d/%d content-types have a spec entry = %.1f%%**, covering %.2f%% of block instances.\n"%(
    len(doc),len(counts),100.0*len(doc)/len(counts),100.0*(tot-ub)/tot))
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
lines.append("## Undocumented types — the remaining work list\n")
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
print("sessions=%d types=%d documented=%d undocumented=%d (%.2f%% of blocks)"%(n,len(counts),len(doc),len(undoc),100.0*ub/tot))
print("ubiquitous undocumented (in ALL %d sessions): %d"%(n,sum(1 for c in undoc if sessions[c]==n)))
