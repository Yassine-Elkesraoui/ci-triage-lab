import json, statistics as st
from scipy import stats as sp

def load(p):
    return json.load(open(p, encoding="utf-8"))["scored"]

s030 = load("eval/results/scored_030.json")
s050 = load("eval/results/scored_050.json")

def num(x):
    return isinstance(x, (int, float))

def tag(r):
    return r["question_id"] + "-" + r["language"]

print("="*60)
print("STEP 7 - HYPOTHESIS STATISTICS")
print("="*60)

# ---------- H1: RAG lifts faithfulness on ANSWERABLE questions ----------
def h1(rows, label):
    pairs = [(r["rag_faithfulness"], r["baseline_faithfulness"])
             for r in rows
             if r["answerable"] and num(r["rag_faithfulness"]) and num(r["baseline_faithfulness"])]
    rag = [a for a, b in pairs]
    base = [b for a, b in pairs]
    print(f"\n[H1 @ {label}] answerable pairs (both non-None): n={len(pairs)}")
    if pairs:
        print(f"  mean RAG faithfulness      = {st.mean(rag):.3f}")
        print(f"  mean baseline faithfulness = {st.mean(base):.3f}")
        print(f"  mean DELTA (RAG - base)    = {st.mean(rag)-st.mean(base):+.3f}")
        t, p = sp.ttest_rel(rag, base)
        print(f"  paired t-test: t={t:.3f}, p={p:.4f}")
    return pairs

h1(s030, "0.3")
h1(s050, "0.5")

# ---------- H2: median latency (RAG vs baseline) ----------
def h2(rows, label):
    rl = [r["rag_latency_s"] for r in rows if num(r.get("rag_latency_s"))]
    bl = [r["baseline_latency_s"] for r in rows if num(r.get("baseline_latency_s"))]
    print(f"\n[H2 @ {label}] median latency (seconds)")
    if rl: print(f"  RAG median      = {st.median(rl):.3f}s  (n={len(rl)})")
    if bl: print(f"  baseline median = {st.median(bl):.3f}s  (n={len(bl)})")

h2(s030, "0.3")
h2(s050, "0.5")

# ---------- H3: threshold trade-off ----------
def h3(rows, label):
    fg = [r for r in rows if not r["answerable"] and r["grounded"]]
    fn = [r for r in rows if r["answerable"] and not r["grounded"]]
    fn_en = [r for r in fn if r["language"] == "en"]
    fn_frpt = [r for r in fn if r["language"] in ("fr", "pt")]
    fg_tags = [tag(r) for r in fg]
    fn_tags = [tag(r) for r in fn]
    print(f"\n[H3 @ {label}]")
    print(f"  FALSE GROUNDINGS (unanswerable yet grounded): {len(fg)}  -> {fg_tags}")
    print(f"  FALSE NEGATIVES  (answerable not grounded):   {len(fn)}  [EN={len(fn_en)}  FR/PT={len(fn_frpt)}]  -> {fn_tags}")

h3(s030, "0.3")
h3(s050, "0.5")
print("\n" + "="*60)
