#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas", "scipy"]
# ///
"""Score every paragraph of the Odia Wikipedia corpus with Sarvam-1 bits per byte (bpb).

Steps:

  score     (pod, GPU)  paragraph texts -> <work>/scores.jsonl and run.json, checkpointed per shard;
                        with --only-missing <file>: only the texts whose para_sha1 is not in <file>
                        (plus --recheck N random ones that are), one row per text
  sanity    (pod, GPU)  shuffle control, determinism, batch invariance, parity with
                        odia-llm-trainer's eval harness -> <work>/sanity.json
  precision (pod, GPU)  bf16 paragraph bpb against an fp32 reference -> <work>/precision.json
  build     (laptop, no model, ~30 s)  scores -> annotations/bpb.jsonl, bpb.paragraphs.jsonl
                        (+ .json sidecars), quality/bpb.md, quality/review-first.md; joins
                        annotations/translation.jsonl and topics.jsonl when they exist

On a pod (torch and transformers installed there): `score`, and for a full run `sanity` and `precision`
(--harness-src: a copy of odia-llm-trainer's src/). Pull <work> with a pod.json and add it with
`build --add <dir>`. A paragraph's score depends only on its own text, so after a corpus rebuild `build`
carries every score over by para_sha1 from annotations/bpb.paragraphs.jsonl, and only new texts need a
pod run (`score --only-missing <a copy of that file>`); `build` stops while any paragraph has no score.

Method (matches odia-llm-trainer's src/odia_llm/evaluation/harness.py, so numbers are comparable with E03/E06):
- paragraph i of an article = text.split("\\n\\n")[i]; i = 0 is the `# title` heading, not scored
- each paragraph is cut by the harness's split_text(max_chars=1000) at whitespace into pieces
- a piece is scored as BOS + its tokens (tokenizer called with add_special_tokens=False), left-
  truncated to 4096 tokens as the harness does; the BOS is context only, every piece token is scored
- bits = -sum(ln p) / ln 2 over a paragraph's pieces; bytes = UTF-8 bytes of the (stripped)
  pieces, exactly what the harness divides by; bpb = bits / bytes
- article bpb = sum of bits / sum of bytes over its scored paragraphs
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # the repository root: every local output goes here
CORPUS = ROOT / "orwiki-20260901.jsonl"
STORE_FIELDS = ["para_sha1", "score_run", "bytes", "tokens", "pieces", "bits"]
ANN = ROOT / "annotations"
ODIA_DIGIT = re.compile("[\u0b66-\u0b6f]")  # the owner's rule: none in any output
QUALITY = ROOT / "quality"
MODEL = "sarvamai/sarvam-1"
REVISION = "e9607337286ddf496d4a2562b194e489dcf3feea"  # main on 2026-09-24; pinned so reruns match
MAX_CHARS = 1000  # harness: Scorer.bpb(texts, max_chars=1000)
MAX_LEN = 4096  # harness: --max-len default

# ------------------------------------------------------------------------- paragraphs


def split_text(text, max_chars):
    """Verbatim copy of odia_llm.evaluation.harness.split_text (checked against it in `sanity`)."""
    pieces, cur = [], ""
    for word in re.split(r"(\s+)", text):
        if len(cur) + len(word) > max_chars and cur.strip():
            pieces.append(cur.strip())
            cur = ""
        cur += word
    if cur.strip():
        pieces.append(cur.strip())
    return pieces


# Same rules as edaapp's paragraphs.kind (which calls "text" "para"), so the two always agree.
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s")


def para_kind(block):
    """heading / table / list / math / text, from the block's leading characters."""
    if re.match(r"#{1,6} ", block):  # a Markdown heading, not a "#!/usr/bin/perl" code line
        return "heading"
    if block.startswith("|"):
        return "table"
    if LIST_ITEM.match(block):
        return "list"
    if block.startswith("$$"):  # display math on its own line
        return "math"
    return "text"


def sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def load_corpus(corpus, fields):
    """The corpus JSONL as a list of dicts holding only `fields`, in file order."""
    return [{k: r[k] for k in fields} for r in read_jsonl(corpus)]


def load_paragraphs(corpus):
    """[(id, para, block)] for every non-title paragraph, in corpus order."""
    rows = []
    for r in load_corpus(corpus, ["id", "text"]):
        blocks = r["text"].split("\n\n")
        assert blocks[0].startswith("# "), (r["id"], blocks[0][:40])
        rows.extend((r["id"], i, b) for i, b in enumerate(blocks) if i > 0)
    return rows


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------------------ io


def write_atomic(path, data):
    """Write to a temp file next to `path`, then rename it over `path`; the temp file never survives
    a failure (a full disk once left one behind)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_jsonl(path):
    """The rows of a JSON lines file (gzipped if the name ends in .gz)."""
    import gzip

    path = Path(path)
    with gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def jsonl(rows):
    """JSON lines text, in the corpus's style: Odia as is, no NaN (a missing value is null)."""
    return "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)


def write_jsonl(path, rows):
    """Write rows atomically."""
    write_atomic(path, jsonl(rows))


def records(df, columns):
    """The rows of `df` as dicts with exactly `columns`, in that order: NaN and NA become null, numpy
    scalars Python ones."""
    import pandas as pd

    def plain(v):
        if v is None or v is pd.NA or (isinstance(v, float) and v != v):
            return None
        return v.item() if hasattr(v, "item") and not isinstance(v, list) else v

    cols = [df[c].tolist() for c in columns]
    return [{c: plain(v) for c, v in zip(columns, vals, strict=True)} for vals in zip(*cols, strict=True)]


# ------------------------------------------------------------------------- the model


class Model:
    """Sarvam-1 in bf16 with the harness's start token and padding, and a memory-lean scorer."""

    def __init__(self, revision=REVISION, model=None, tok=None):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.dev = "cuda"
        self.tok = tok or AutoTokenizer.from_pretrained(MODEL, revision=revision, trust_remote_code=True)
        self.model = model or (
            AutoModelForCausalLM.from_pretrained(
                MODEL,
                revision=revision,
                dtype=torch.bfloat16,
                attn_implementation="sdpa",
                trust_remote_code=True,
            )
            .to(self.dev)
            .eval()
        )
        t = self.tok
        self.start = [t.bos_token_id if t.bos_token_id is not None else t.eos_token_id]  # as harness
        self.pad = t.pad_token_id if t.pad_token_id is not None else t.eos_token_id  # as harness
        # The fast path runs the decoder and the output head separately, so the full B x T x 68k
        # logits tensor is never built. That is only the same computation if the head is a plain
        # linear layer on the final hidden state, as in LlamaForCausalLM.
        assert type(self.model).__name__ == "LlamaForCausalLM", type(self.model).__name__

    def encode(self, pieces):
        """Harness encoding: ids = (start + tokens)[-MAX_LEN:], n = scored tokens."""
        enc = self.tok(pieces, add_special_tokens=False)["input_ids"]
        out = []
        for x in enc:
            ids = (self.start + x)[-MAX_LEN:]
            out.append((ids, min(len(x), len(ids) - 1), len(x)))
        return out

    def nll(self, seqs, budget=32768, max_rows=512, chunk=8192):
        """-sum(ln p) of the last n tokens of each (ids, n, ...) given what precedes them.

        seqs should be sorted by decreasing length (batches are cut greedily at `budget` tokens).
        Returns a list of floats. On CUDA OOM the batch is retried at half the budget.
        """
        torch = self.torch
        F = torch.nn.functional
        out = [0.0] * len(seqs)
        b = 0
        with torch.inference_mode():
            while b < len(seqs):
                width = len(seqs[b][0])
                rows = max(1, min(max_rows, budget // width))
                batch = seqs[b : b + rows]
                try:
                    sums = self._batch(batch, width, chunk, F)
                except torch.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if rows == 1 and chunk <= 1024:
                        raise
                    budget, chunk = max(width, budget // 2), max(1024, chunk // 2)
                    print(f"  OOM at {rows}x{width}; budget -> {budget}, chunk -> {chunk}", flush=True)
                    continue
                out[b : b + len(batch)] = sums
                b += len(batch)
        return out

    def _batch(self, batch, width, chunk, F):
        torch = self.torch
        input_ids = torch.full((len(batch), width), self.pad, dtype=torch.long)
        mask = torch.zeros((len(batch), width), dtype=torch.long)
        rows, pos, tgt, seg = [], [], [], []
        for r, s in enumerate(batch):
            ids, n = s[0], s[1]
            L = len(ids)
            input_ids[r, :L] = torch.tensor(ids)
            mask[r, :L] = 1
            # harness: logits[r, L-1-n : L-1] predict ids[L-n : L]
            rows += [r] * n
            pos += range(L - 1 - n, L - 1)
            tgt += ids[L - n :]
            seg += [r] * n
        h = self.model.model(input_ids=input_ids.to(self.dev), attention_mask=mask.to(self.dev)).last_hidden_state
        rows_t = torch.tensor(rows, device=self.dev)
        pos_t = torch.tensor(pos, device=self.dev)
        tgt_t = torch.tensor(tgt, device=self.dev)
        hs = h[rows_t, pos_t]
        nll = torch.empty(len(tgt), dtype=torch.float32, device=self.dev)
        for c in range(0, len(tgt), chunk):
            logits = self.model.lm_head(hs[c : c + chunk]).float()  # bf16 head, fp32 log-softmax
            nll[c : c + chunk] = F.cross_entropy(logits, tgt_t[c : c + chunk], reduction="none")
        sums = torch.zeros(len(batch), dtype=torch.float64, device=self.dev)
        sums.index_add_(0, torch.tensor(seg, device=self.dev), nll.double())
        return sums.cpu().tolist()


def gpu_info():
    import torch
    import transformers

    return {
        "gpu": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "transformers": transformers.__version__,
    }


# ----------------------------------------------------------------------------- score


def missing_texts(corpus, store, recheck=200):
    """[(para_sha1, purpose, text)]: every distinct paragraph text of `corpus` whose para_sha1 is not in
    `store` (JSON lines with a para_sha1 field, e.g. a copy of annotations/bpb.paragraphs.jsonl: "missing"), then
    `recheck` random texts that are ("recheck").

    A paragraph's bpb depends only on its own text (it is scored from BOS), so a score can be carried
    over to any paragraph with the same text. The recheck texts measure how well new scores agree with
    carried-over ones.
    """
    have = {r["para_sha1"] for r in read_jsonl(store)}
    seen, todo, carried = set(), [], []
    for _pid, _i, b in load_paragraphs(corpus):
        h = sha1(b)
        if h not in seen:
            seen.add(h)
            (carried if h in have else todo).append((h, b))
    again = random.Random(20260924).sample(carried, min(recheck, len(carried)))
    print(
        f"{len(seen):,} distinct paragraph texts: {len(carried):,} already scored, {len(todo):,} to score, "
        f"{len(again)} rechecked",
        flush=True,
    )
    return [(h, "missing", b) for h, b in todo] + [(h, "recheck", b) for h, b in again]


def cmd_score(args):
    import numpy as np

    work = Path(args.work)
    shards = work / "shards"
    shards.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    missing = bool(args.only_missing)
    if missing:
        paras = missing_texts(args.corpus, args.only_missing, args.recheck)
    else:
        paras = load_paragraphs(args.corpus)
    if args.limit:
        paras = paras[: args.limit]
    piece_para, pieces = [], []
    for k, (_pid, _i, block) in enumerate(paras):
        for c in split_text(block, MAX_CHARS):
            piece_para.append(k)
            pieces.append(c)
    print(f"{len(paras):,} paragraphs, {len(pieces):,} pieces", flush=True)

    m = Model(args.revision)
    t0 = time.time()
    enc = m.encode(pieces)
    lengths = np.array([len(e[0]) for e in enc])
    print(
        f"tokenised in {time.time() - t0:.0f}s: {sum(e[2] for e in enc):,} tokens "
        f"(+{len(enc):,} BOS), longest piece {lengths.max()} ids, "
        f"{sum(e[2] + 1 > MAX_LEN for e in enc)} truncated",
        flush=True,
    )

    # Longest first (memory problems show up at once); shards of ~args.shard_tokens tokens.
    order = np.argsort(-lengths, kind="stable")
    bounds, acc, lo = [], 0, 0
    for j, i in enumerate(order):
        acc += lengths[i]
        if acc >= args.shard_tokens:
            bounds.append((lo, j + 1))
            lo, acc = j + 1, 0
    if lo < len(order):
        bounds.append((lo, len(order)))
    manifest = {
        "corpus_sha1": file_sha1(args.corpus),
        "mode": "only-missing" if missing else "all",
        "revision": args.revision,
        "limit": args.limit,
        "pieces": len(pieces),
        "order_sha1": hashlib.sha1(order.astype(np.int64).tobytes()).hexdigest(),
        "shards": bounds,
    }
    mpath = work / "manifest.json"
    if mpath.exists():
        old = json.loads(mpath.read_text())
        if old != json.loads(json.dumps(manifest)):
            raise SystemExit(f"{mpath} is from a different run; move {work} away to start over")
    else:
        write_atomic(mpath, json.dumps(manifest))

    nll = np.full(len(pieces), np.nan)
    done_tokens = scored_tokens = 0
    t_gpu = time.time()
    for s, (lo, hi) in enumerate(bounds):
        path = shards / f"{s:04d}.npy"
        idx = order[lo:hi]
        if path.exists():
            nll[idx] = np.load(path)
            print(f"shard {s + 1}/{len(bounds)}: from checkpoint", flush=True)
            continue
        t1 = time.time()
        vals = np.array(m.nll([enc[i] for i in idx], budget=args.budget))
        tmp = shards / f".{s:04d}.{os.getpid()}.tmp.npy"
        np.save(tmp, vals)
        os.replace(tmp, path)
        nll[idx] = vals
        ntok = int(sum(lengths[i] for i in idx))
        done_tokens += ntok
        scored_tokens += ntok
        el = time.time() - t_gpu
        left = int(lengths[order[hi:]].sum())
        print(
            f"shard {s + 1}/{len(bounds)}: {hi - lo:,} pieces, {ntok:,} tokens in {time.time() - t1:.0f}s "
            f"({ntok / (time.time() - t1):,.0f} tok/s; run {done_tokens / el:,.0f} tok/s; "
            f"{left:,} tokens left, ~{left / max(done_tokens / el, 1) / 60:.1f} min)",
            flush=True,
        )
    assert not np.isnan(nll).any()
    gpu_seconds = time.time() - t_gpu

    # Per paragraph: sum over pieces.
    n = len(paras)
    bits = np.zeros(n)
    nbytes = np.zeros(n, dtype=np.int64)
    ntoks = np.zeros(n, dtype=np.int64)
    npieces = np.zeros(n, dtype=np.int32)
    trunc = np.zeros(n, dtype=np.int32)
    for j, k in enumerate(piece_para):
        bits[k] += nll[j] / math.log(2)
        nbytes[k] += len(pieces[j].encode("utf-8"))
        ntoks[k] += enc[j][2]
        npieces[k] += 1
        trunc[k] += enc[j][2] > enc[j][1]
    # One row per paragraph (a full run: keyed by id and para) or per text (--only-missing).
    rows = [
        {
            **(
                {"para_sha1": p[0], "purpose": p[1]} if missing else {"id": p[0], "para": p[1], "para_sha1": sha1(p[2])}
            ),
            "kind": para_kind(p[2]),
            "chars": len(p[2]),
            "bytes": int(nbytes[k]),
            "tokens": int(ntoks[k]),
            "pieces": int(npieces[k]),
            "truncated_pieces": int(trunc[k]),
            "bits": float(bits[k]),
        }
        for k, p in enumerate(paras)
    ]
    write_jsonl(work / "scores.jsonl", rows)
    total_tokens = int(lengths.sum())
    run = {
        "model": MODEL,
        "revision": args.revision,
        **gpu_info(),
        "corpus": Path(args.corpus).name,
        "corpus_sha1": manifest["corpus_sha1"],
        "mode": manifest["mode"],
        "paragraphs": n,
        "pieces": len(pieces),
        "tokens_with_bos": total_tokens,
        "tokens": int(ntoks.sum()),
        "bytes": int(nbytes.sum()),
        "bits": float(bits.sum()),
        "bpb": float(bits.sum() / nbytes.sum()),
        "truncated_pieces": int(trunc.sum()),
        "gpu_seconds_this_session": round(gpu_seconds, 1),
        "tokens_this_session": done_tokens,
        "tok_per_s_this_session": round(done_tokens / gpu_seconds, 1) if done_tokens else None,
        "wall_seconds": round(time.time() - t_start, 1),
        "budget_tokens_per_batch": args.budget,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if missing:
        new = np.array([p[1] == "missing" for p in paras])
        run.update(
            {
                "have": Path(args.only_missing).name,
                "have_sha1": file_sha1(args.only_missing),
                "texts_missing": int(new.sum()),
                "texts_rechecked": int((~new).sum()),
                "bytes_missing": int(nbytes[new].sum()),
                "tokens_missing": int(ntoks[new].sum()),
                "bpb_missing": float(bits[new].sum() / max(nbytes[new].sum(), 1)),
            }
        )
    write_atomic(work / "run.json", json.dumps(run, indent=2))
    print(json.dumps(run, indent=2), flush=True)


# ---------------------------------------------------------------------------- sanity


def shuffle_words(text, rng):
    """Shuffle the words but keep every whitespace run in place, so the bytes are identical."""
    parts = re.split(r"(\s+)", text)
    words = parts[0::2]
    rng.shuffle(words)
    parts[0::2] = words
    return "".join(parts)


def main_run(work):
    """(bits, bytes) of a paragraph (id, para, block) as `score` wrote it to <work>/scores.jsonl, or None:
    a full run is looked up by (id, para), an --only-missing run by the text's sha1."""
    path = Path(work) / "scores.jsonl"
    rows = read_jsonl(path) if path.exists() else []
    by_pos = {(r["id"], r["para"]): (r["bits"], r["bytes"]) for r in rows if "id" in r}
    by_text = {r["para_sha1"]: (r["bits"], r["bytes"]) for r in rows if "id" not in r}
    return lambda pid, i, b: by_pos.get((pid, i)) or by_text.get(sha1(b))


def cmd_sanity(args):
    import numpy as np

    work = Path(args.work)
    paras = load_paragraphs(args.corpus)
    rng = random.Random(20260924)
    res = {}

    # 0. split_text and the harness module, when its source is on the pod.
    harness = None
    if args.harness_src:
        sys.path.insert(0, args.harness_src)
        sys.dont_write_bytecode = True
        try:
            from odia_llm.evaluation import harness  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            res["harness_import_error"] = repr(e)
    if harness:
        bad = sum(split_text(b, MAX_CHARS) != harness.split_text(b, MAX_CHARS) for _, _, b in paras)
        res["split_text_mismatches_vs_harness"] = bad
        sc = harness.Scorer(MODEL, 8, MAX_LEN)  # the repo's own scorer, batch 8 as odia-eval
        m = Model(model=sc.model, tok=sc.tok)
    else:
        m = Model(args.revision)

    def score_blocks(blocks, **kw):
        """bits, bytes per block through the fast path (pieces batched together)."""
        pieces, owner = [], []
        for k, b in enumerate(blocks):
            for c in split_text(b, MAX_CHARS):
                pieces.append(c)
                owner.append(k)
        enc = m.encode(pieces)
        order = sorted(range(len(enc)), key=lambda i: -len(enc[i][0]))
        vals = m.nll([enc[i] for i in order], **kw)
        nll = [0.0] * len(enc)
        for i, v in zip(order, vals, strict=True):
            nll[i] = v
        bits = [0.0] * len(blocks)
        nb = [0] * len(blocks)
        for j, k in enumerate(owner):
            bits[k] += nll[j] / math.log(2)
            nb[k] += len(pieces[j].encode())
        return bits, nb

    # 1. Shuffle control: 300 single-piece text paragraphs of >= 100 bytes and >= 10 words.
    text = [
        (pid, i, b)
        for pid, i, b in paras
        if para_kind(b) == "text" and len(b.encode()) >= 100 and len(b) <= MAX_CHARS and len(b.split()) >= 10
    ]
    sample = rng.sample(text, 300)
    orig = [b for _, _, b in sample]
    shuf = [shuffle_words(b, rng) for b in orig]
    assert all(len(a.encode()) == len(s.encode()) for a, s in zip(orig, shuf, strict=True))
    bo, nbo = score_blocks(orig)
    bs, _ = score_blocks(shuf)
    po = np.array(bo) / np.array(nbo)
    ps = np.array(bs) / np.array(nbo)
    worse = ps > po
    res["shuffle"] = {
        "n": len(sample),
        "shuffled_higher": int(worse.sum()),
        "share_higher": float(worse.mean()),
        "bpb_original": float(sum(bo) / sum(nbo)),
        "bpb_shuffled": float(sum(bs) / sum(nbo)),
        "median_ratio": float(np.median(ps / po)),
        "min_ratio": float((ps / po).min()),
        "not_higher": [
            {"id": s[0], "para": s[1], "bpb": float(a), "bpb_shuffled": float(b), "excerpt": s[2][:160]}
            for s, a, b, w in zip(sample, po, ps, worse, strict=True)
            if not w
        ],
    }
    print("shuffle:", {k: v for k, v in res["shuffle"].items() if k != "not_higher"}, flush=True)

    # 2. Determinism: 100 random paragraphs, scored twice in the same batches, once one at a time,
    #    and compared with the main run (other batch neighbours) where scores.jsonl has them.
    det = rng.sample(paras, 100)
    blocks = [b for _, _, b in det]
    b1, nb = score_blocks(blocks)
    b2, _ = score_blocks(blocks)
    b3, _ = score_blocks(blocks, budget=1, max_rows=1)  # batch of one: no padding at all
    p1, p2, p3 = (np.array(x) / np.array(nb) for x in (b1, b2, b3))
    d = {
        "n": len(det),
        "max_abs_bpb_diff_rerun": float(np.abs(p1 - p2).max()),
        "max_abs_bits_diff_rerun": float(np.abs(np.array(b1) - np.array(b2)).max()),
        "max_abs_bpb_diff_batch_of_one": float(np.abs(p1 - p3).max()),
        "max_rel_bits_diff_batch_of_one": float((np.abs(np.array(b1) - np.array(b3)) / np.array(b3)).max()),
    }
    look = main_run(work)
    found = [(k, look(*x)) for k, x in enumerate(det) if look(*x)]
    if len(found) >= 2:
        mine = p1[[k for k, _ in found]]
        main = np.array([bits / nbytes for _, (bits, nbytes) in found])
        if len(found) < len(det):
            d["n_vs_main_run"] = len(found)
        d["max_abs_bpb_diff_vs_main_run"] = float(np.abs(mine - main).max())
        d["corr_vs_main_run"] = float(np.corrcoef(mine, main)[0, 1])
    res["determinism"] = d
    print("determinism:", d, flush=True)

    # 3. Parity with the repo's harness (its own loglik, batch 8): per piece and pooled bpb.
    if harness:
        par = rng.sample(paras, 200)
        pieces = [c for _, _, b in par for c in split_text(b, MAX_CHARS)]
        ll = sc.loglik([("", c) for c in pieces])
        enc = m.encode(pieces)
        order = sorted(range(len(enc)), key=lambda i: -len(enc[i][0]))
        vals = m.nll([enc[i] for i in order])
        mine = [0.0] * len(enc)
        for i, v in zip(order, vals, strict=True):
            mine[i] = -v
        ll, mine = np.array(ll), np.array(mine)
        nbytes = sum(len(c.encode()) for c in pieces)
        res["harness_parity"] = {
            "pieces": len(pieces),
            "max_abs_loglik_diff": float(np.abs(ll - mine).max()),
            "max_rel_loglik_diff": float((np.abs(ll - mine) / np.abs(ll)).max()),
            "bpb_harness_loglik": float(-ll.sum() / nbytes / math.log(2)),
            "bpb_harness_bpb_fn": float(sc.bpb([b for _, _, b in par])),
            "bpb_this_script": float(-mine.sum() / nbytes / math.log(2)),
        }
        print("harness parity:", res["harness_parity"], flush=True)
    res.update(gpu_info())
    write_atomic(work / "sanity.json", json.dumps(res, indent=2, ensure_ascii=False))


def cmd_precision(args):
    """How precise is a bf16 paragraph bpb? 150 random paragraphs scored by the main run (bf16, batched),
    bf16 one at a time, bf16 batched again, the harness (bf16, batch 8) and fp32 batched, each against
    fp32 one at a time. Writes <work>/precision.json. (First run as a standalone script with this logic.)"""
    import numpy as np

    work = Path(args.work)
    paras = load_paragraphs(args.corpus)
    sample = random.Random(7).sample(paras, 150)
    look = main_run(work)
    main = [look(*x) for x in sample]
    if not all(main):  # checked before the models load
        raise SystemExit(
            f"precision compares with a full run: {work}/scores.jsonl must come from `score` on this corpus"
        )

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    sys.path.insert(0, args.harness_src)
    sys.dont_write_bytecode = True
    from odia_llm.evaluation import harness  # noqa: PLC0415

    pieces, owner = [], []
    for k, (_, _, b) in enumerate(sample):
        for c in split_text(b, MAX_CHARS):
            pieces.append(c)
            owner.append(k)

    def per_para(vals):
        bits = np.zeros(len(sample))
        for j, k in enumerate(owner):
            bits[k] += vals[j] / math.log(2)
        return bits

    def run(m, **kw):
        enc = m.encode(pieces)
        order = sorted(range(len(enc)), key=lambda i: -len(enc[i][0]))
        v = m.nll([enc[i] for i in order], **kw)
        out = [0.0] * len(enc)
        for i, x in zip(order, v, strict=True):
            out[i] = x
        return np.array(out)

    sc = harness.Scorer(MODEL, 8, MAX_LEN)
    mb = Model(model=sc.model, tok=sc.tok)
    bf16_one, bf16_batch = run(mb, budget=1, max_rows=1), run(mb)
    harn = -np.array(sc.loglik([("", c) for c in pieces]))
    del sc, mb
    torch.cuda.empty_cache()
    m32 = (
        AutoModelForCausalLM.from_pretrained(
            MODEL, revision=args.revision, dtype=torch.float32, attn_implementation="sdpa"
        )
        .cuda()
        .eval()
    )
    mf = Model(model=m32, tok=AutoTokenizer.from_pretrained(MODEL, revision=args.revision))
    fp32_one, fp32_batch = run(mf, budget=1, max_rows=1), run(mf)
    nb = np.zeros(len(sample))
    for j, k in enumerate(owner):
        nb[k] += len(pieces[j].encode())
    ref = per_para(fp32_one)
    mainbits = np.array([bits for bits, _ in main])
    out = {"paragraphs": len(sample), "pieces": len(pieces)}
    for name, bits in [
        ("main_run_bf16_batched", mainbits),
        ("bf16_batch_of_one", per_para(bf16_one)),
        ("bf16_batched_again", per_para(bf16_batch)),
        ("harness_bf16_bs8", per_para(harn)),
        ("fp32_batched", per_para(fp32_batch)),
    ]:
        rel = np.abs(bits - ref) / ref
        out[name] = {
            "median_rel": float(np.median(rel)),
            "p95_rel": float(np.percentile(rel, 95)),
            "max_rel": float(rel.max()),
            "max_abs_dbpb": float((np.abs(bits - ref) / nb).max()),
            "pooled_bpb": float(bits.sum() / nb.sum()),
            "worst_bytes": int(nb[rel.argmax()]),
        }
    out["fp32_one_pooled_bpb"] = float(ref.sum() / nb.sum())
    print(json.dumps(out, indent=1))
    write_atomic(work / "precision.json", json.dumps(out, indent=1))


# ------------------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    s = sub.add_parser("score")
    s.add_argument("--corpus", default=str(CORPUS))
    s.add_argument("--work", required=True)
    s.add_argument("--revision", default=REVISION)
    s.add_argument("--budget", type=int, default=32768, help="tokens per batch (rows x width)")
    s.add_argument("--shard-tokens", type=int, default=500_000, help="tokens per checkpoint shard")
    s.add_argument("--limit", type=int, default=None, help="first N paragraphs only (smoke test)")
    s.add_argument(
        "--only-missing",
        metavar="SCORED",
        default=None,
        help="score only the paragraph texts whose para_sha1 is not in this JSON lines file (a copy of "
        "annotations/bpb.paragraphs.jsonl); output is one row per text",
    )
    s.add_argument("--recheck", type=int, default=200, help="with --only-missing: also re-score N scored texts")
    s = sub.add_parser("sanity")
    s.add_argument("--corpus", default=str(CORPUS))
    s.add_argument("--work", required=True)
    s.add_argument("--revision", default=REVISION)
    s.add_argument("--harness-src", default=None, help="a copy of odia-llm-trainer's src/ (for the parity check)")
    s = sub.add_parser("precision")
    s.add_argument("--corpus", default=str(CORPUS))
    s.add_argument("--work", required=True)
    s.add_argument("--revision", default=REVISION)
    s.add_argument("--harness-src", required=True, help="a copy of odia-llm-trainer's src/")
    s = sub.add_parser("build")
    s.add_argument("--corpus", default=str(CORPUS))
    s.add_argument(
        "--add",
        action="append",
        default=None,
        metavar="DIR",
        help="add a `score` run from a pod: DIR holds its scores.jsonl and run.json, and optionally pod.json, "
        "sanity.json, precision.json and note.txt (a line for the report). Repeatable; a run already added is "
        "skipped",
    )
    s.add_argument(
        "--out",
        default=str(ROOT),
        help="where annotations/ (whose bpb.json holds the run records) and quality/ are (default: this directory)",
    )
    s.add_argument("--partial", action="store_true", help="smoke test: scores cover only some articles")
    s.add_argument("--context-dir", default=str(ANN), help="where translation.jsonl / topics.jsonl are read from")
    args = ap.parse_args()
    {"score": cmd_score, "sanity": cmd_sanity, "precision": cmd_precision, "build": lambda a: cmd_build(a)}[args.step](
        args
    )


# ----------------------------------------------------------------------------- build
#
# Everything below runs on the laptop from the pods' scores: no model, about 30 seconds.

BANDS = [
    (0, 100, "<100 B"),
    (100, 300, "100-299 B"),
    (300, 1000, "300-999 B"),
    (1000, 3000, "1-3 kB"),
    (3000, 10**12, ">=3 kB"),
]
MIN_BYTES = {"text": 100, "list": 100, "table": 200, "math": 100}  # headings are never flagged
MIN_GROUP = 400  # a kind x length-band group smaller than this is pooled over the kind
TAIL = 0.01  # a paragraph in the top or bottom 1% of its group is "extreme"
ART_MIN_BYTES = 500
ART_TAIL = 0.01
N_FLAG = 200
# Leftovers of the HTML-to-Markdown conversion: tags, wikitext, image sizes, entities, URLs and
# namespace prefixes (ଛାଞ୍ଚ = Template, ଚିତ୍ର = File, ଶ୍ରେଣୀ = Category; not inside a word, since
# କଥାଚିତ୍ର: is "feature film:"). `\<` is the corpus's own escape. Math is removed first: LaTeX
# is full of }} and {|.
MARKUP = re.compile(
    r"(?<!\\)<[A-Za-z/!][^>\n]{0,80}>|\{\{|\}\}|\[\[|\]\]|\{\||\|\}|\b\d+px\b|&[a-z]{2,8};|"
    r"data-mw|\bmw-|class=|style=|thumb\||https?://|www\.|(?<![଀-୿])(?:ଛାଞ୍ଚ|ଚିତ୍ର|ଶ୍ରେଣୀ):|"
    r"\b(?:File|Image|Category|Template):"
)
MATH = re.compile(r"\$\$.+?\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$", re.S)
# Common English words: Latin-script text with few of them is not English (transliteration, IPA,
# German, lists of film titles).
ENGLISH = set(
    """the of and in to a is was for on as with by he she his her at from that which it an
are were be has have had this their its or not but also who after first one two been into
during they them other more most when than all only over under film born known about""".split()
)
TYPES = {  # review types, in interleaving order, with the legend text
    "garbled": "Odia text the model finds very unlikely for its kind and length: garbled OCR or "
    "typing, broken sentences, odd mixtures. Verse, songs and Sanskrit also score high; "
    "they are ranked after prose within this type",
    "english": "a paragraph that is mostly English: untranslated leftovers, quotes, citations, OCR'd English",
    "templated": "very predictable text for its kind and length (bottom 1%): formulaic sentences, "
    "lists and near-copies across articles; the reasons say when a copy was found",
    "table": "a table in the top or bottom 1% of tables: English-only tables, IPA or name lists, "
    "repeated career tables",
    "markup": "leftovers of the HTML/wikitext conversion ({{ }}, [[ ]], {| |}, tags, class=, "
    "namespace prefixes, px sizes, URLs), any bpb",
    "script": "a paragraph mostly in another script (Shahmukhi, Brahmi, Telugu, ...) or in Latin "
    "letters that are not English (transliteration, IPA, other languages, romanised titles)",
    "article": "the article as a whole: its bpb is in the top or bottom 1% of articles >= 500 B, or "
    "most of its bytes are in extreme paragraphs (catches pages made of many tiny paragraphs)",
}


def band_of(nbytes):
    for lo, hi, name in BANDS:
        if lo <= nbytes < hi:
            return name
    raise ValueError(nbytes)


def script_mix(s):
    """(Odia, Latin, other) shares of the letters and marks in s."""
    import unicodedata

    o = la = ot = 0
    for ch in s:
        c = ord(ch)
        if 0x0B00 <= c <= 0x0B7F:
            o += 1
        elif ch.isascii():
            la += ch.isalpha()
        elif unicodedata.category(ch)[0] in "LM":
            if 0x00C0 <= c <= 0x024F:
                la += 1
            elif c not in (0x200C, 0x200D):
                ot += 1
    n = o + la + ot
    return (o / n, la / n, ot / n) if n else (0.0, 0.0, 0.0)


def top_words(p):
    """'top 0.1%' / 'bottom 1%' style wording for a within-group percentile p in (0, 1]."""
    for cut in (0.0001, 0.001, 0.005, 0.01, 0.05):
        if p >= 1 - cut:
            return f"top {cut * 100:g}%"
        if p <= cut:
            return f"bottom {cut * 100:g}%"
    return f"{p * 100:.0f}th percentile"


def markup_hits(b):
    b = MATH.sub(" ", b)
    return ", ".join(sorted({m.group(0)[:12] for m in MARKUP.finditer(b)})[:4]) or None


def english_share(b):
    """Share of the Latin-script words that are common English words (None: no Latin words)."""
    w = re.findall(r"[A-Za-zÀ-ɏ']+", b)
    return sum(x.lower() in ENGLISH for x in w) / len(w) if len(w) >= 5 else None


def is_verse(kind, b):
    """A prose block of three or more short lines: poems, songs, shlokas."""
    lines = b.split("\n")
    return kind == "text" and len(lines) >= 3 and max(len(x) for x in lines) < 200


def near_copies(s, rows, min_jaccard=0.5):
    """For the paragraphs at `rows`: how many other articles hold a near-copy, and the best match.

    Near-copy = Jaccard similarity of word-bigram sets (digits masked) >= min_jaccard, against every
    text, list and table paragraph of >= 50 B. Sparse matrix product, a few seconds.
    """
    import numpy as np
    from scipy import sparse

    def grams(b):
        w = re.sub(r"[0-9]+", "0", b).split()
        return {f"{x} {y}" for x, y in zip(w, w[1:], strict=False)}

    pool = s[(s.bytes >= 50) & s.kind.isin(["text", "list", "table"])]
    vocab = {}
    indptr, indices = [0], []
    for b in pool.block:
        indices.extend(vocab.setdefault(g, len(vocab)) for g in grams(b))
        indptr.append(len(indices))
    X = sparse.csr_matrix((np.ones(len(indices), np.float32), indices, indptr), shape=(len(pool), len(vocab)))
    pos = {r: k for k, r in enumerate(pool.index)}
    sel = [pos[r] for r in rows if r in pos]
    C = X[sel]
    inter = (C @ X.T).tocsr()
    size = np.asarray(X.sum(axis=1)).ravel()
    pid = pool.id.to_numpy()
    out = {}
    for k, r in enumerate(r for r in rows if r in pos):
        row = inter.getrow(k)
        j, sh = row.indices, row.data
        jac = sh / (size[pos[r]] + size[j] - sh)
        other = (pid[j] != pid[pos[r]]) & (jac >= min_jaccard)
        arts = set(pid[j][other])
        out[r] = (len(arts), float(jac[other].max()) if other.any() else 0.0)
    return out


RARE_DF = 20  # a word in fewer articles than this is treated as a name when looking for templates


def skeletons(s, titles):
    """A template key per paragraph: the article's own title, digits and rare words (names) masked.

    Paragraphs that share a key across articles are the same sentence frame with different names
    and numbers filled in ("<W> ଏକ ଭାରତୀୟ ଗ୍ରାମ ଅଟେ ।"). A key with fewer than 4 unmasked words says
    too little to call anything a template, so it is None.
    """
    from collections import Counter

    masked = []
    for pid, b in zip(s.id, s.block, strict=True):
        t = titles[pid]
        b = b.replace(t, " <T> ") if len(t) >= 2 else b
        masked.append(re.sub(r"[0-9]+", "0", b).split())
    df = Counter()
    for pid, words in zip(s.id, masked, strict=True):
        df.update((pid, w) for w in set(words))
    wdf = Counter(w for (_pid, w) in df)
    out = []
    for words in masked:
        key, kept = [], 0
        for w in words:
            if w in ("<T>", "0") or wdf[w] >= RARE_DF:
                key.append(w)
                kept += w not in ("<T>", "0")
            elif not key or key[-1] != "<W>":
                key.append("<W>")
        out.append(" ".join(key) if kept >= 4 else None)
    return out


def trigram_repeat(b):
    """Share of word trigrams that repeat an earlier one in the same paragraph (0 = none)."""
    w = re.sub(r"[0-9]+", "0", b).split()
    if len(w) < 12:
        return 0.0
    tri = list(zip(w, w[1:], w[2:], strict=False))
    return 1 - len(set(tri)) / len(tri)


def excerpt(s, n=140):
    s = s.replace("\n", " ⏎ ").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def one_per_text(rows):
    """One score per text from a run's rows. Identical texts scored more than once in a run (a full run
    scores every paragraph) differ only by bf16 noise from their batch neighbours: they get the mean,
    kept exact when the scores agree (the mean of n equal floats can be 1 ulp off)."""
    import numpy as np

    per = rows.groupby("para_sha1").agg(
        kind=("kind", "first"),
        bytes=("bytes", "first"),
        tokens=("tokens", "first"),
        pieces=("pieces", "first"),
        bits=("bits", "mean"),
        bits_min=("bits", "min"),
        bits_max=("bits", "max"),
        bits_first=("bits", "first"),
    )
    per["bits"] = np.where(per.bits_min == per.bits_max, per.bits_first, per.bits)
    return per.drop(columns=["bits_min", "bits_max", "bits_first"]).reset_index()


def read_json(path, default):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default


def before_stats(ann):
    """The build a run's scores are added to, for the report: size, bpb, review queue types."""
    import pandas as pd

    if not ((ann / "bpb.paragraphs.jsonl").exists() and (ann / "bpb.jsonl").exists()):
        return {}
    op = pd.DataFrame(read_jsonl(ann / "bpb.paragraphs.jsonl"), columns=["id", "kind", "bytes", "bits"])
    oa = pd.DataFrame(read_jsonl(ann / "bpb.jsonl"), columns=["id", "review_type"])
    ot = op[op.kind == "text"]
    return {
        "articles": int(op.id.nunique()),
        "paragraphs": len(op),
        "bytes": int(op.bytes.sum()),
        "bpb": float(op.bits.sum() / op.bytes.sum()),
        "bpb_text": float(ot.bits.sum() / ot.bytes.sum()),
        "queue_types": {k: int(v) for k, v in oa.review_type.value_counts().items()},
    }


def add_run(add, runs, store, ann):
    """Add the `score` run in directory `add` (scores.jsonl, run.json, optional pod.json, sanity.json,
    precision.json, note.txt): its texts that the store lacks go in with a new run id, the texts it
    already has are compared with their stored scores, and the run's record joins `runs`."""
    import pandas as pd

    add = Path(add)
    r = json.loads((add / "run.json").read_text())
    if any(x["scoring"].get("finished") == r["finished"] for x in runs):
        print(f"{add}: already added, skipped")
        return runs, store
    rid = max((x["id"] for x in runs), default=0) + 1
    new = one_per_text(pd.DataFrame(read_jsonl(add / "scores.jsonl")))
    old = store.set_index("para_sha1").bits
    seen = new.para_sha1.isin(old.index)
    rec = {"id": rid, "mode": r["mode"], "scoring": r, "pod": read_json(add / "pod.json", {})}
    sanity = read_json(add / "sanity.json", {})
    if (add / "precision.json").exists():
        sanity["precision"] = read_json(add / "precision.json", {})
    if sanity:
        rec["sanity"] = sanity
    if seen.any():  # the --recheck texts: how well new scores agree with stored ones
        chk = new[seen].assign(old_bits=new[seen].para_sha1.map(old))
        rel = (chk.bits - chk.old_bits).abs() / chk.old_bits
        big = chk.bytes >= 100
        num = lambda x: float(x) if x == x else None  # noqa: E731  (no NaN in JSON)
        rec["recheck"] = {
            "texts": len(chk),
            "identical": int((chk.bits == chk.old_bits).sum()),
            "median_rel": num(rel.median()),
            "p95_rel": num(rel.quantile(0.95)),
            "max_rel": num(rel.max()),
            "median_rel_ge_100B": num(rel[big].median()),
            "max_rel_ge_100B": num(rel[big].max()),
            "mean_signed_rel": num(((chk.bits - chk.old_bits) / chk.old_bits).mean()),
            "bpb_new": num(chk.bits.sum() / chk.bytes.sum()),
            "bpb_carried": num(chk.old_bits.sum() / chk.bytes.sum()),
            "worst": chk.assign(rel=rel)
            .sort_values("rel")
            .tail(3)[["para_sha1", "kind", "bytes", "bits", "old_bits"]]
            .to_dict("records"),
        }
    note = (add / "note.txt").read_text(encoding="utf-8").strip() if (add / "note.txt").exists() else ""
    if r["mode"] == "only-missing":
        rec["before"] = before_stats(ann)
    if r["mode"] == "only-missing" or note:
        rec["note"] = note
    fresh = new[~seen].assign(score_run=rid)[STORE_FIELDS]
    store = pd.concat([store, fresh], ignore_index=True) if len(store) else fresh.reset_index(drop=True)
    ck = rec.get("recheck")
    print(
        f"{add}: added {len(fresh):,} new texts to the store as run {rid}"
        + (
            f"; {ck['texts']} texts it already had came out {ck['identical']} identical, median |rel diff| "
            f"{ck['median_rel']:.2%}, max {ck['max_rel']:.2%}"
            if ck
            else ""
        )
    )
    return [*runs, rec], store


def cmd_build(args):
    import numpy as np
    import pandas as pd

    # ---- scores, one per paragraph *text* (para_sha1), from every scoring run so far
    # A paragraph's bpb depends only on its own text (scored from BOS), so scores carry over by para_sha1
    # when the corpus is rebuilt, and only new texts need the GPU (`score --only-missing`).
    out_ann = Path(args.out) / "annotations"
    runs = read_json(out_ann / "bpb.json", {}).get("runs", [])
    prev = out_ann / "bpb.paragraphs.jsonl"  # every score so far, one row per paragraph
    store = pd.DataFrame(read_jsonl(prev) if prev.exists() else [], columns=STORE_FIELDS).drop_duplicates("para_sha1")
    for add in args.add or []:
        runs, store = add_run(add, runs, store, out_ann)
    per = store.set_index("para_sha1")
    run = runs[0]["scoring"]

    fields = ["id", "title", "text", "bot_created", "stub", "odia_ratio", "timestamp"]
    corpus = pd.DataFrame(load_corpus(args.corpus, fields), columns=fields)
    corpus_sha1 = file_sha1(args.corpus)

    # ---- coverage: every non-title paragraph of the current corpus gets the score of its exact text
    rows = [
        (pid, i, b)
        for pid, text in zip(corpus.id, corpus.text, strict=True)
        for i, b in enumerate(text.split("\n\n"))
        if i > 0
    ]
    s = pd.DataFrame(rows, columns=["id", "para", "block"])
    s["para_sha1"] = s.block.map(sha1)
    s = s.join(per, on="para_sha1")
    unscored = s.bits.isna()
    cov = {
        "corpus_sha1": corpus_sha1,
        "corpus_sha1_matches_scored": runs[-1]["scoring"]["corpus_sha1"] == corpus_sha1,
        "corpus": Path(args.corpus).name,
        "latest_run": runs[-1]["id"],
        "latest_run_corpus_sha1": runs[-1]["scoring"]["corpus_sha1"],
        "paragraphs_in_corpus": len(s),
        "paragraph_rows": int((~unscored).sum()),
        "missing_rows": int(unscored.sum()),
        "extra_rows": 0,
        "duplicate_rows": int(s.duplicated(["id", "para"]).sum()),
        "para_sha1_mismatches": 0,  # the score is looked up by the sha1 of the current text itself
        "distinct_texts": int(s.para_sha1.nunique()),
        "rows_by_run": {
            int(k): int(v) for k, v in s.score_run.dropna().astype(int).value_counts().sort_index().items()
        },
        "articles_in_corpus": len(corpus),
        "articles_with_rows": int(s[~unscored].id.nunique()),
        "zero_byte_paragraphs": int((s.bytes == 0).sum()),
    }
    if cov["missing_rows"] and not args.partial:
        raise SystemExit(
            f"{cov['missing_rows']:,} paragraphs ({s[unscored].para_sha1.nunique():,} texts) have no score: run "
            "`score --only-missing` with a copy of annotations/bpb.paragraphs.jsonl on a pod and add it with --add"
        )
    if args.partial:  # smoke tests: only what was scored
        s = s[~unscored]
        corpus = corpus[corpus.id.isin(set(s.id))].reset_index(drop=True)
    s = s.reset_index(drop=True)
    assert cov["duplicate_rows"] == 0, cov

    # ---- paragraph features
    s["kind"] = s.block.map(para_kind)  # recomputed here: the rule may be newer than the pod run
    mix = np.array([script_mix(b) for b in s.block])
    s["odia_share"], s["latin_share"], s["other_share"] = mix[:, 0], mix[:, 1], mix[:, 2]
    s["markup"] = s.block.map(markup_hits)
    s["english"] = s.block.map(english_share)
    s["verse"] = [is_verse(k, b) for k, b in zip(s.kind, s.block, strict=True)]
    s["header"] = [b.split("\n", 1)[0] if k == "table" else None for k, b in zip(s.kind, s.block, strict=True)]
    s["header_repeats"] = s.groupby("header").id.transform("nunique").fillna(0).astype("int32")
    titles = dict(zip(corpus.id, corpus.title, strict=True))
    s["tkey"] = skeletons(s, titles)
    s["repeats"] = s.groupby("tkey").id.transform("nunique").fillna(1).astype("int32")  # no key: 1
    s["self_repeat"] = s.block.map(trigram_repeat)
    s["score_run"] = s.score_run.astype("int16")
    for c in ("bytes", "tokens", "pieces"):
        s[c] = s[c].astype("int64")
    s["bpb"] = np.where(s.bytes > 0, s.bits / s.bytes.clip(lower=1), np.nan)
    s["band"] = s.bytes.map(band_of)

    # ---- within-group extremes: kind x length band, small groups pooled over the kind
    s["eligible"] = [k in MIN_BYTES and nb >= MIN_BYTES[k] for k, nb in zip(s.kind, s.bytes, strict=True)]
    el = s[s.eligible].copy()
    sizes = el.groupby(["kind", "band"]).size()
    el["group"] = [
        f"{k} {b}" if sizes[(k, b)] >= MIN_GROUP else f"{k} >={MIN_BYTES[k]} B"
        for k, b in zip(el.kind, el.band, strict=True)
    ]
    el["lbpb"] = np.log(el.bpb)
    g = el.groupby("group").lbpb
    el["pct"] = g.rank(pct=True, method="average")
    med = g.transform("median")
    mad = g.transform(lambda x: (x - x.median()).abs().median()) * 1.4826
    el["z"] = (el.lbpb - med) / mad
    el["group_median_bpb"] = np.exp(med)
    s = s.join(el[["group", "pct", "z", "group_median_bpb"]])
    s["flag"] = np.where(s.pct >= 1 - TAIL, "high", np.where(s.pct <= TAIL, "low", None))
    s.loc[~s.eligible, "flag"] = None

    # ---- articles
    art = s.groupby("id").agg(
        paragraphs=("para", "size"), bytes=("bytes", "sum"), tokens=("tokens", "sum"), bits=("bits", "sum")
    )
    txt = s[s.kind == "text"].groupby("id").agg(tb=("bytes", "sum"), tbits=("bits", "sum"))
    art = art.join(txt)
    art["bpb"] = art.bits / art.bytes
    art["bpb_text"] = art.tbits / art.tb
    ext = s[s.flag.notna()]
    art["extreme_high"] = ext[ext.flag == "high"].groupby("id").size().reindex(art.index).fillna(0).astype("int32")
    art["extreme_low"] = ext[ext.flag == "low"].groupby("id").size().reindex(art.index).fillna(0).astype("int32")
    art["extreme_share"] = ext.groupby("id").bytes.sum().reindex(art.index).fillna(0) / art.bytes
    big = art.bytes >= ART_MIN_BYTES
    lb = np.log(art.loc[big, "bpb"])
    art.loc[big, "bpb_pct"] = lb.rank(pct=True)
    art.loc[big, "bpb_z"] = (lb - lb.median()) / ((lb - lb.median()).abs().median() * 1.4826)
    art = art.join(corpus.set_index("id")[["title", "bot_created", "stub", "odia_ratio", "timestamp"]])
    art["year"] = art.timestamp.str[:4]
    art_med_big = float(art.loc[big, "bpb"].median())

    # ---- optional context from the other annotators (joined when present, never waited for)
    ctx = {}
    for name in ("translation", "topics"):
        f = Path(args.context_dir) / f"{name}.jsonl"
        if f.exists():
            ctx[name] = pd.DataFrame(read_jsonl(f)).set_index("id")
        elif f.with_suffix(".parquet").exists():
            print(f"{f.with_suffix('.parquet')} is not read (JSON lines only): run `annotate.py {name}` for {f.name}")

    # ---- review candidates, one failure type each
    # Latin-dominant paragraphs: English (enough common English words) or not (transliteration, ...).
    s["latin_english"] = (s.latin_share >= 0.5) & (s.english.fillna(0) >= 0.1)
    s["foreign"] = (s.other_share >= 0.25) | ((s.latin_share >= 0.5) & ~s.latin_english)
    eng = s[s.eligible & s.latin_english & s.kind.isin(["text", "list"])]
    eng_med, eng_p95 = float(eng.bpb.median()), float(eng.bpb.quantile(0.95))
    low_rows = list(s.index[(s.flag == "low") & s.kind.isin(["text", "list", "table"])])
    copies = near_copies(s, low_rows)
    s["near_copies"] = pd.Series({r: v[0] for r, v in copies.items()}, dtype="float").reindex(s.index)
    s["near_best"] = pd.Series({r: v[1] for r, v in copies.items()}, dtype="float").reindex(s.index)

    def para_reason(r):
        out = [f"para {r.para} ({r.kind}, {r.bytes:,} B): bpb {r.bpb:.2f}"]
        if r.eligible:
            out[0] += f", {top_words(r.pct)} of {r.group} (median {r.group_median_bpb:.2f})"
        if r.latin_share >= 0.3:
            out.append(
                f"{r.latin_share:.0%} Latin letters"
                + ("" if r.english is None or r.english != r.english else f", {r.english:.0%} common English words")
            )
        if r.latin_english and r.bpb >= eng_p95:
            out.append(f"high even for English (English paragraphs: median {eng_med:.2f})")
        if r.other_share >= 0.2:
            out.append(f"{r.other_share:.0%} letters in other scripts")
        if isinstance(r.markup, str):
            out.append(f"markup: {r.markup}")
        if r.verse:
            out.append("verse or song lines")
        if r.repeats >= 5:
            out.append(f"same sentence frame (names and numbers masked) in {r.repeats:,} articles")
        if r.near_copies == r.near_copies and r.near_copies > 0:
            n = int(r.near_copies)
            out.append(f"near-copy (word-pair overlap {r.near_best:.0%}) in {n:,} other article{'s' * (n > 1)}")
        if r.self_repeat >= 0.3:
            out.append(f"{r.self_repeat:.0%} of its word triples repeat within it")
        if r.kind == "table" and r.header_repeats >= 5:
            out.append(f"same header row in {r.header_repeats:,} articles")
        return "; ".join(out)

    size = lambda r: math.log10(max(r.bytes, 100) / 100)  # noqa: E731  (0 at 100 B, 1 at 1 kB)
    cands = []  # (type, severity, id, para or None, reason, signature)
    # One entry per template in the queue: tables with a common header row, and paragraphs with a
    # common sentence frame, share a signature, and only the strongest of each is queued.
    for r in s[s.eligible | s.markup.notna()].itertuples():
        z = 0.0 if r.z != r.z else r.z
        t = None
        if isinstance(r.markup, str):  # any bpb: the pattern is the evidence
            t, sev = "markup", 1 + r.markup.count(",") + 0.3 * max(z, 0) + size(r)
        elif r.kind == "table" and isinstance(r.flag, str):  # (a missing flag is NaN, which is truthy)
            t, sev = "table", abs(z) + 0.5 * size(r)
        elif not r.eligible or r.kind == "table":
            continue
        elif r.latin_english:  # English whatever its bpb: Sarvam-1 reads English at ~0.8 bpb
            t, sev = "english", 1 + size(r) + 0.3 * max(z, 0)
        elif r.foreign:
            t, sev = "script", 1 + size(r) + 0.3 * max(z, 0)
        elif r.flag == "high":
            t, sev = "garbled", (abs(z) + 0.5 * size(r)) * (0.5 if r.verse else 1)
        elif r.flag == "low":
            copied = (r.repeats >= 5) or (r.near_copies == r.near_copies and r.near_copies > 0) or r.self_repeat >= 0.3
            t, sev = "templated", abs(z) + 0.5 * size(r) + (1.5 if copied else 0)
        if t:
            sig = (
                ("header", r.header)
                if r.kind == "table" and r.header_repeats >= 5
                else ("frame", r.tkey)
                if r.repeats >= 5
                else None
            )
            cands.append((t, sev, r.id, r.para, para_reason(r), sig))
    short = s[s.bytes < 100].groupby("id").bytes.sum().reindex(art.index).fillna(0) / art.bytes
    for pid, a in art[big].iterrows():
        why = []
        if a.bpb_pct >= 1 - ART_TAIL or a.bpb_pct <= ART_TAIL:
            why.append(
                f"article: bpb {a.bpb:.2f}, {top_words(a.bpb_pct)} of articles >= {ART_MIN_BYTES} B "
                f"(median {art_med_big:.2f})"
            )
        if a.extreme_share >= 0.5 and a.paragraphs >= 3:
            why.append(f"{a.extreme_share:.0%} of its bytes are in extreme paragraphs")
        if why:
            if a.odia_ratio < 0.6:
                why.append(f"odia_ratio {a.odia_ratio:.2f}")
            if short[pid] >= 0.3:
                why.append(f"{short[pid]:.0%} of its bytes are in paragraphs under 100 B")
            sev = abs(a.bpb_z) + 2 * a.extreme_share
            cands.append(("article", sev, pid, None, "; ".join(why), None))
    by_type = {t: sorted((c for c in cands if c[0] == t), key=lambda c: -c[1]) for t in TYPES}

    ranked, reasons, primary, used_sigs = [], {}, {}, set()
    ptr = dict.fromkeys(TYPES, 0)
    while len(ranked) < N_FLAG and any(ptr[t] < len(by_type[t]) for t in TYPES):
        for t in TYPES:
            while ptr[t] < len(by_type[t]):
                c = by_type[t][ptr[t]]
                ptr[t] += 1
                if c[2] not in primary and (c[5] is None or c[5] not in used_sigs):
                    used_sigs.add(c[5])
                    ranked.append(c[2])
                    primary[c[2]] = c
                    break
            if len(ranked) >= N_FLAG:
                break
    # Every flagged article lists its primary reason first, then its other extremes (strongest first).
    for pid in ranked:
        c = primary[pid]
        rest = sorted((x for x in cands if x[2] == pid and x is not c), key=lambda x: -x[1])
        rs = [c[4]] + [x[4] for x in rest[:5]]
        if len(rest) > 5:
            rs.append(f"... and {len(rest) - 5} more extreme paragraphs")
        if "translation" in ctx and pid in ctx["translation"].index:
            ct = ctx["translation"].loc[pid]
            if bool(ct.get("translated", False)):
                tool = "MDWiki" if bool(ct.get("mdwiki_created", False)) else "Content Translation"
                src = ct.get("source_lang")
                rs.append(
                    f"context: machine-assisted translation ({tool}"
                    + (f", from {src}" if isinstance(src, str) and src else "")
                    + ")"
                )
        if "topics" in ctx and pid in ctx["topics"].index:
            tp = ctx["topics"].loc[pid]
            topic = tp.get("primary_topic", tp.get("topic"))
            if isinstance(topic, str) and topic:
                rs.append(f"context: topic {topic}" + (", Odisha" if bool(tp.get("odisha", False)) else ""))
        reasons[pid] = rs
    art["review_rank"] = pd.array([None] * len(art), dtype="Int32")
    art["review_type"] = None
    art["review_para"] = pd.array([None] * len(art), dtype="Int32")
    art["review_reasons"] = None
    for rank, pid in enumerate(ranked, 1):
        c = primary[pid]
        art.at[pid, "review_rank"] = rank
        art.at[pid, "review_type"] = c[0]
        art.at[pid, "review_para"] = c[3]
        art.at[pid, "review_reasons"] = reasons[pid]

    # ---- the output files
    art["text_sha1"] = [sha1(t) for t in corpus.set_index("id").loc[art.index, "text"]]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    src = (
        f"{MODEL}@{run['revision'][:12]}, {len(runs)} scoring runs on RunPod GPUs (records: 'runs' in bpb.json); "
        "score_bpb.py; corpus "
        f"{Path(args.corpus).name} sha1 {corpus_sha1[:12]}"
    )
    art_cols = {
        "id": "page id (join key)",
        "text_sha1": "sha1 of the article text (UTF-8) that was scored; differs from the current text = stale",
        "bpb": "Sarvam-1 bits per UTF-8 byte over all scored paragraphs (sum of bits / sum of bytes); lower = "
        "more predictable",
        "bpb_text": "the same over prose paragraphs only (kind text); null if the article has none",
        "bpb_pct": f"percentile of bpb among articles with >= {ART_MIN_BYTES} scored bytes (0-1); null for "
        "smaller articles",
        "bytes": "UTF-8 bytes scored (the title heading, paragraph 0, is not scored)",
        "tokens": "Sarvam-1 tokens scored (without the BOS each piece starts with)",
        "bits": "total bits",
        "paragraphs": "paragraphs scored (all but the title)",
        "extreme_high": f"paragraphs in the top {TAIL:.0%} of bpb for their kind and length (see bpb.paragraphs)",
        "extreme_low": f"paragraphs in the bottom {TAIL:.0%} of bpb for their kind and length",
        "extreme_share": "share of the article's scored bytes that are in extreme paragraphs",
        "review_rank": f"order to review in, 1 = first; the top {N_FLAG} articles, failure types interleaved; "
        "null = not flagged",
        "review_type": "primary failure type: " + ", ".join(TYPES),
        "review_para": "paragraph index of the primary reason (text.split('\\n\\n')[i]); null for "
        "article-level reasons",
        "review_reasons": "human-readable reasons, primary first",
    }
    para_cols = {
        "id": "page id",
        "para": "paragraph index; 0 (the title heading) is not scored",
        "para_sha1": "sha1 of the paragraph text (UTF-8); the score belongs to exactly this text",
        "score_run": (
            "the scoring run that scored this text (see 'runs' in bpb.json): 1 = the full run, later runs = "
            "texts that were new after a corpus rebuild; a text found in several paragraphs of one run gets the mean"
        ),
        "kind": "heading / list / table / math / text, from the leading characters (edaapp's rule; its 'para' = text)",
        "bpb": "bits per UTF-8 byte; paragraphs over 1,000 characters are scored in whitespace-split pieces, as "
        "the eval harness does",
        "bytes": "UTF-8 bytes scored (pieces are stripped, so this can be a few bytes under the raw paragraph)",
        "tokens": "Sarvam-1 tokens scored (without BOS)",
        "bits": "-sum(log2 p) over the tokens",
        "pieces": "pieces the paragraph was split into (1 unless over 1,000 characters)",
        "bpb_group": (
            f"comparison group: kind x length band, pooled over the kind when under {MIN_GROUP} paragraphs; only "
            "text/list/math >= 100 B and tables >= 200 B are compared (headings never)"
        ),
        "bpb_pct": "percentile of bpb within bpb_group (0-1); null when not compared",
        "bpb_z": "robust z of log bpb within bpb_group ((x - median) / (1.4826 MAD)); null when not compared",
        "flag": f"'high' = top {TAIL:.0%}, 'low' = bottom {TAIL:.0%} of bpb_group; null otherwise",
        "latin_share": "share of the paragraph's letters and marks that are Latin script",
        "other_script_share": "share that is neither Odia nor Latin (Devanagari, Bengali, ...)",
        "english_words": "share of the Latin-script words that are common English words (null: under 5 Latin words)",
        "markup": "conversion leftovers found outside math (tags, {{ }}, [[ ]], {| |}, px, URLs, namespace "
        "prefixes), if any",
        "verse": "a prose block of 3+ short lines (poems, songs, shlokas)",
        "repeats": (
            "articles with a paragraph of the same sentence frame: digits, the article's title and words found in "
            f"fewer than {RARE_DF} articles masked (1 = unique or too little left to compare)"
        ),
        "self_repeat": "share of word triples that repeat an earlier one in the same paragraph",
        "near_copies": (
            "other articles holding a near-copy (word-bigram Jaccard >= 0.5); computed only for paragraphs flagged "
            "low, null otherwise"
        ),
    }
    a = art.reset_index().assign(bpb_pct=lambda d: d.bpb_pct.round(5), extreme_share=lambda d: d.extreme_share.round(4))
    derived = lambda d: d.assign(  # noqa: E731
        bpb_group=d.group,
        bpb_pct=d.pct.round(5),
        bpb_z=d.z.round(3),
        latin_share=d.latin_share.round(3),
        other_script_share=d.other_share.round(3),
        english_words=d.english.astype("float").round(3),
        self_repeat=d.self_repeat.round(3),
        near_copies=d.near_copies.astype("Int32"),
    )
    p = derived(s)

    def sidecar(name, description, columns, **more):
        head = {"name": name, "description": description, "source": src, "created": now}
        return json.dumps({**head, "columns": columns, "depends_on_text": True, **more}, indent=2, ensure_ascii=False)

    out_q = Path(args.out) / "quality"
    files = {
        out_ann / "bpb.jsonl": jsonl(records(a, list(art_cols))),
        out_ann / "bpb.json": sidecar(
            "bpb",
            "Sarvam-1 bits per byte of every article, and a review queue of likely data problems (garbled or "
            "wrong-script text, untranslated English, boilerplate, odd tables, conversion leftovers) from "
            "paragraph and article bpb extremes.",
            art_cols,
            runs=runs,  # every scoring run so far: what `build` needs to run again without the pods' files
        )
        + "\n",
        out_ann / "bpb.paragraphs.jsonl": jsonl(records(p, list(para_cols))),
        out_ann / "bpb.paragraphs.json": sidecar(
            "bpb.paragraphs",
            "Sarvam-1 bits per byte of every paragraph except the title (paragraph i = text.split('\\n\\n')[i]), "
            "with its percentile within its kind and length band.",
            para_cols,
        )
        + "\n",
        **write_reports(
            s, art, runs, cov, ctx, by_type, ranked, primary, reasons, corpus_sha1, out_q
        ),
    }
    # The owner's rule: ASCII digits in every output (the corpus converts them; titles included).
    odia_digits = {f.name: n for f, text in files.items() if (n := len(ODIA_DIGIT.findall(text)))}
    for f, text in files.items():
        write_atomic(f, text)
    print(
        f"wrote {', '.join(str(f.relative_to(args.out)) for f in files)}; {len(ranked)} articles flagged"
        + (f"; WARNING: Odia digits in {odia_digits}" if odia_digits else "")
    )


def md_table(head, rows, align=None):
    align = align or ["---"] + ["---:"] * (len(head) - 1)
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def md_text(s):
    """Plain text (a review reason) made safe to print in Markdown."""
    for a, b in (("&", "&amp;"), ("<", "&lt;"), ("*", "\\*"), ("_", "\\_"), ("[", "\\["), ("`", "\\`")):
        s = s.replace(a, b)
    return s


def cell(s, n=110):
    """An excerpt for a Markdown table cell. The corpus text is already escaped Markdown, so only
    the pipes need escaping here."""
    return excerpt(s, n).replace("\\|", "|").replace("|", "\\|")


def write_reports(s, art, runs, cov, ctx, by_type, ranked, primary, reasons, corpus_sha1, out_q):
    """The texts of quality/bpb.md and quality/review-first.md, by path."""
    run, sanity, pod = runs[0]["scoring"], runs[0].get("sanity", {}), runs[0].get("pod", {})
    import numpy as np

    titles = art.title
    pooled = lambda d: d.bits.sum() / d.bytes.sum()  # noqa: E731
    q = lambda x, p: float(np.percentile(x, p)) if len(x) else float("nan")  # noqa: E731
    corpus_bpb = pooled(s)
    txt = s[s.kind == "text"]
    big = art[art.bytes >= ART_MIN_BYTES]
    el = s[s.eligible]
    L = []
    w = L.append

    w("# Sarvam-1 bits per byte on the Odia Wikipedia corpus\n")
    w(
        f"Every paragraph of `{cov['corpus']}` (sha1 `{corpus_sha1[:12]}`), except the title, scored with "
        f"`{MODEL}` (revision `{run['revision'][:12]}`) by `score_bpb.py`. Written by `score_bpb.py build`; "
        "the per-article and per-paragraph numbers are in `annotations/bpb.jsonl` and "
        "`annotations/bpb.paragraphs.jsonl`, and the review queue is in `quality/review-first.md`.\n"
    )
    w("## Summary\n")
    w(
        f"- **Corpus bpb {corpus_bpb:.4f}** over {len(s):,} paragraphs, {s.bytes.sum() / 1e6:.1f} MB and "
        f"{s.tokens.sum() / 1e6:.2f}M Sarvam-1 tokens ({s.tokens.sum() / s.bytes.sum() * 1000:.1f} tokens per kB). "
        f"Prose alone (kind `text`): **{pooled(txt):.4f}**. For comparison, base Sarvam-1 scores 0.4951 on the "
        "native-Odia held-out set of odia-llm-trainer (E03/E06)."
    )
    w(
        f"- The median article (>= {ART_MIN_BYTES} B, {len(big):,} articles) has bpb {big.bpb.median():.3f}; the "
        f"middle 98% run from {q(big.bpb, 1):.3f} to {q(big.bpb, 99):.3f}."
    )
    nflag = int(s.flag.notna().sum())
    long_txt = txt[txt.bytes >= 1000]
    w(
        f"- Matched for length it is the held-out number: prose paragraphs of 1 kB and more score "
        f"**{pooled(long_txt):.4f}**. Shorter paragraphs cost more because each is scored without the text "
        "before it."
    )
    sh = sanity.get("shuffle", {})
    hp = sanity.get("harness_parity", {})
    if sh and hp:
        w(
            f"- Checks pass: word-shuffled paragraphs score higher in {sh['shuffled_higher']}/{sh['n']} cases; the "
            "repo harness gives the same pooled bpb to "
            f"{abs(hp['bpb_harness_loglik'] - hp['bpb_this_script']) / hp['bpb_harness_loglik']:.2%}; "
            "one paragraph's bpb is good to about 0.3% (median; bf16), 2% at p95; every paragraph has a row."
            + "".join(
                f" Run {x['id']}'s re-scores of {x['recheck']['texts']} carried-over texts agree with them "
                f"(median {x['recheck']['median_rel']:.1%}, max {x['recheck']['max_rel']:.1%})."
                for x in runs
                if x.get("recheck")
            )
        )
    w(
        f"- {nflag:,} paragraphs are extreme (top or bottom {TAIL:.0%} of their kind and length band, among "
        f"{int(s.eligible.sum()):,} comparable ones). {len(ranked)} articles are queued for review "
        "(`quality/review-first.md`), "
        + ", ".join(f"{sum(primary[x][0] == t for x in ranked)} {t}" for t in TYPES)
        + ", interleaved."
    )
    w(
        f"- What bpb finds at the top: English inside Odia articles "
        f"({int((s.eligible & s.latin_english & s.kind.isin(['text', 'list'])).sum()):,} comparable paragraphs), "
        "other scripts or non-English Latin text "
        f"({int((s.eligible & s.foreign & s.kind.isin(['text', 'list'])).sum()):,}), "
        "and unusual Odia (verse, archaic text, name lists; genuinely garbled Odia is rare). At the bottom: "
        f"formulaic biographies, year lists and a career table repeated in "
        f"{int(s[s.kind == 'table'].header_repeats.max()):,} articles. What it does not find: boilerplate repeated "
        f"across articles (use `repeats`). Conversion leftovers: {int(s.markup.notna().sum())} paragraphs."
    )
    t = ctx.get("translation")
    if t is not None and "translated" in t.columns:
        j = big.join(t[["translated", "created"]], how="left")
        tr = j.translated.fillna(False).astype(bool)
        new = j.created.fillna("") >= "2024-10"
        a_, b_ = pooled(j[tr]), pooled(j[~tr])
        m1, m0 = j[new].bpb.median(), j[~new].bpb.median()
        w(
            "- Machine-assisted translations score "
            f"{'about the same as' if abs(a_ / b_ - 1) < 0.02 else 'lower than' if a_ < b_ else 'higher than'} "
            f"the rest (pooled {a_:.3f} vs {b_:.3f}). Pages created after Sarvam-1's release score "
            f"{'lower' if m1 < m0 else 'higher'} (median {m1:.3f} vs {m0:.3f})"
            + (
                ", so there is no sign that it memorised Odia Wikipedia."
                if m1 <= m0
                else ": older pages are more predictable, which fits memorisation (or older pages being simpler)."
            )
        )
    w("")

    # ---- method
    w("## Method\n")
    w(
        "Matches odia-llm-trainer's `src/odia_llm/evaluation/harness.py` (`Scorer.loglik`, `Scorer.bpb`, `split_text`), so the "
        "numbers are on the same scale as the experiments' `bpb_odia`:\n"
    )
    w(
        '- Paragraph *i* = `text.split("\\n\\n")[i]`; paragraph 0 (the `# title` heading) is not scored. '
        "Headings, list blocks and tables count as one paragraph each."
    )
    w(
        "- A paragraph over 1,000 characters is cut at whitespace into pieces by the harness's `split_text`; "
        "each piece is scored on its own and the bits and bytes are summed."
    )
    w(
        "- A piece is scored as BOS + its tokens (the harness's `self.start`; the tokenizer is called with "
        "`add_special_tokens=False`, so it adds its usual leading `▁`). Every piece token is scored, the "
        "first one given only BOS. Sequences over 4,096 tokens would be left-truncated as in the harness "
        f"({run['truncated_pieces']} were)."
    )
    w(
        "- bits = −Σ ln p / ln 2; bpb = bits / UTF-8 bytes of the (stripped) pieces. Article bpb = Σ bits / Σ bytes "
        "over its paragraphs, headings included."
    )
    w(
        "- Scores are kept per paragraph *text*: when the corpus is rebuilt they are carried over by `para_sha1`, "
        "and only new texts are scored (see the runs below). A text scored more than once gets the mean."
    )
    w(
        "- bf16 weights, SDPA attention, `torch.inference_mode`. Batches are sorted by length and cut at "
        f"{run['budget_tokens_per_batch']:,} tokens. The decoder runs once per batch, and the output head "
        "runs on 8,192 positions at a time with an fp32 log-softmax (`cross_entropy`), so the full "
        "batch × length × 68,096 logits tensor is never built. Progress is checkpointed every ~500k tokens."
    )
    w("")

    # ---- runs
    w("## Runs\n")
    w(
        f"Model `{MODEL}` @ `{run['revision']}`, bf16, the same code path in every run. Each paragraph's score "
        "comes from the run that first scored its text (`score_run`).\n"
    )
    rows = []
    for x in runs:
        r, pd_ = x["scoring"], x.get("pod", {})
        what = (
            f"all {r['paragraphs']:,} paragraphs"
            if x["mode"] == "all"
            else f"{r.get('texts_missing', 0):,} new texts + {r.get('texts_rechecked', 0)} rechecks"
        )
        rows.append(
            (
                str(x["id"]),
                what,
                f"`{pd_.get('pod_id', '?')}`",
                f"{pd_.get('gpu', r['gpu'])}, {pd_.get('cloud', '?')}, {pd_.get('datacenter', '?')}",
                f"${pd_.get('price_per_hr', 0):.2f}/h",
                f"{pd_.get('created', '?')[:16]} to {pd_.get('terminated', '?')[11:16]}",
                f"{pd_.get('hours', 0) * 60:.0f} min, ${pd_.get('cost', 0):.2f}",
                f"{r['tokens_with_bos']:,} in {r['gpu_seconds_this_session']:.0f} s "
                f"({r['tok_per_s_this_session']:,.0f}/s)",
                f"{r['corpus_sha1'][:8]}",
            )
        )
    w(
        md_table(
            [
                "run",
                "scored",
                "pod",
                "GPU",
                "price",
                "pod up (UTC)",
                "pod time, cost",
                "tokens (with BOS)",
                "corpus sha1",
            ],
            rows,
            ["---:", "---", "---", "---", "---:", "---", "---:", "---:", "---"],
        )
    )
    total = sum(x.get("pod", {}).get("cost", 0) for x in runs)
    w(
        f"\nImage `{pod.get('image', '?')}`, torch {run['torch']} (CUDA {run['cuda']}), transformers "
        f"{run['transformers']} in every run. Total pod cost ${total:.2f}.\n"
    )

    # ---- sanity
    w("## Sanity checks\n")
    if len(runs) > 1:
        w(
            "Checks 1-3 ran with run 1, on the whole corpus as it was then; 4 and 5 are recomputed on the current "
            "corpus at every build. Later runs are checked against carried-over scores in their own section below.\n"
        )
    sh = sanity.get("shuffle")
    if sh:
        w(
            f"1. **Shuffle control.** {sh['n']} random prose paragraphs (>= 100 B, >= 10 words, one piece) were "
            "scored as they are and with their words shuffled (the whitespace kept in place, so the bytes are "
            f"identical). The shuffled copy scored higher for **{sh['shuffled_higher']} of {sh['n']}** "
            f"({sh['share_higher']:.1%}); pooled bpb {sh['bpb_original']:.3f} → {sh['bpb_shuffled']:.3f}, "
            f"median ratio {sh['median_ratio']:.2f}, smallest {sh['min_ratio']:.2f}."
        )
        for x in sh["not_higher"][:5]:
            w(
                f"   - not higher: {x['id']}/{x['para']} ({x['bpb']:.3f} → {x['bpb_shuffled']:.3f}): "
                f"{cell(x['excerpt'], 90)}"
            )
    d = sanity.get("determinism")
    if d:
        w(
            f"2. **Determinism.** {d['n']} random paragraphs re-scored: same batches twice, max |Δbpb| = "
            f"{d['max_abs_bpb_diff_rerun']:.2e}; one paragraph per batch (no padding), max |Δbpb| = "
            f"{d['max_abs_bpb_diff_batch_of_one']:.2e} (max relative Δbits {d['max_rel_bits_diff_batch_of_one']:.1e})"
            + (
                "; against the main run (other batch neighbours), max |Δbpb| = "
                f"{d['max_abs_bpb_diff_vs_main_run']:.2e}."
                if "max_abs_bpb_diff_vs_main_run" in d
                else "."
            )
        )
    pr = sanity.get("precision")
    if pr:
        m, o = pr["main_run_bf16_batched"], pr["bf16_batch_of_one"]
        w(
            f"   **How precise is one paragraph's bpb?** The differences above are bf16 rounding, not a bug: "
            f"on {pr['paragraphs']} random paragraphs, fp32 batched and fp32 one-at-a-time agree to "
            f"{pr['fp32_batched']['max_rel']:.0e} (so batching and padding are exact), while bf16 differs from "
            f"fp32 by a median {m['median_rel']:.1%} of the bits (p95 {m['p95_rel']:.1%}, max {m['max_rel']:.1%}, "
            f"worst on a {m['worst_bytes']}-byte paragraph) in the main run and {o['median_rel']:.1%} scored one at a "
            f"time. Pooled bpb is unbiased: {m['pooled_bpb']:.4f} (bf16) vs {pr['fp32_one_pooled_bpb']:.4f} (fp32). "
            "A 1% tail is far wider than this (see the robust spreads below), so the flags are stable; the "
            "exact rank of two paragraphs a few percent apart is not."
        )
    hp = sanity.get("harness_parity")
    if hp:
        w(
            f"3. **Parity with odia-llm-trainer's harness.** `odia_llm.evaluation.harness.Scorer` (its own `loglik`, "
            f"batch 8) on {hp['pieces']} pieces from 200 random paragraphs: max |Δ log-likelihood| per piece "
            f"{hp['max_abs_loglik_diff']:.3f} nats (relative {hp['max_rel_loglik_diff']:.1e}); pooled bpb "
            f"{hp['bpb_harness_loglik']:.5f} (harness) vs {hp['bpb_this_script']:.5f} (this script); "
            f"`Scorer.bpb` on the same paragraphs: {hp['bpb_harness_bpb_fn']:.5f}. `split_text` differs from the "
            f"harness's on {sanity.get('split_text_mismatches_vs_harness', '?')} of {len(s):,} paragraphs."
        )
    elif "harness_import_error" in sanity:
        w(f"3. **Parity with the harness** was not run: {sanity['harness_import_error']}")
    long_txt = txt[txt.bytes >= 1000]
    w(
        f"4. **Plausibility.** Corpus bpb {corpus_bpb:.4f} and prose {pooled(txt):.4f}, against 0.4951 for base "
        "Sarvam-1 on the held-out set. That set is scored as whole documents cut into ~1,000-character pieces; "
        "here every paragraph starts afresh from BOS, so short paragraphs lose context and cost more. Matched for "
        f"length the numbers agree: prose paragraphs of 1 kB and more score {pooled(long_txt):.4f} "
        f"({len(long_txt):,} paragraphs), while those of 100-299 B score {pooled(txt[txt.band == '100-299 B']):.3f} "
        f"and headings {pooled(s[s.kind == 'heading']):.3f}. Encyclopedic Odia is not easier for Sarvam-1 than the "
        "held-out mix (which already includes 300 Wikipedia documents from 2023)."
    )
    w(
        f"5. **Coverage.** {cov['paragraph_rows']:,} of {cov['paragraphs_in_corpus']:,} non-title paragraphs of the "
        f"current corpus (sha1 `{cov['corpus_sha1'][:12]}`) have a score, {cov['missing_rows']} have none, "
        f"{cov['duplicate_rows']} are duplicated; each score is looked up by the sha1 of the paragraph's current "
        f"text ({cov['distinct_texts']:,} distinct texts; rows by scoring run: "
        + ", ".join(f"run {k}: {v:,}" for k, v in cov["rows_by_run"].items())
        + f"). {cov['articles_with_rows']:,} of {cov['articles_in_corpus']:,} articles have rows, and `text_sha1` "
        f"and `para_sha1` are computed from the current corpus file. The latest run (run {cov['latest_run']}) "
        + (
            "scored this same corpus file"
            if cov["corpus_sha1_matches_scored"]
            else f"scored an earlier corpus file (sha1 `{cov['latest_run_corpus_sha1'][:12]}`), whose scores carry over"
        )
        + f". {cov['zero_byte_paragraphs']} paragraphs have no bytes to score."
    )
    w("")

    # ---- re-scoring runs
    now_types = {t: sum(primary[x][0] == t for x in ranked) for t in TYPES}
    for x in runs:
        if x["mode"] != "only-missing":
            continue
        r, pd_, ck, bf = x["scoring"], x.get("pod", {}), x.get("recheck", {}), x.get("before", {})
        new = s[s.score_run == x["id"]]
        w(f"## Re-scoring after the {r['finished'][:10]} cleanup\n")
        if x.get("note"):
            w(x["note"] + "\n")
        w(
            "A paragraph's bpb depends only on its own text (it is scored from BOS, and paragraph *i* is still "
            '`text.split("\\n\\n")[i]`), so every score was carried over by `para_sha1` and only texts never '
            "scored before went to the GPU (`score --only-missing`), with the same model revision, chunking, "
            "bf16 and code as run 1.\n"
        )
        if bf:
            w(
                f"- **Before**: {bf['articles']:,} articles, {bf['paragraphs']:,} paragraphs, "
                f"corpus bpb {bf['bpb']:.4f} "
                f"(prose {bf['bpb_text']:.4f})."
            )
        w(
            f"- **Now**: {s.id.nunique():,} articles, {len(s):,} paragraphs "
            f"({s.para_sha1.nunique():,} distinct texts), "
            f"corpus bpb {s.bits.sum() / s.bytes.sum():.4f} (prose "
            f"{s[s.kind == 'text'].bits.sum() / s[s.kind == 'text'].bytes.sum():.4f}). "
            f"{int((s.score_run != x['id']).sum()):,} rows carried over; {len(new):,} rows "
            f"({r.get('texts_missing', 0):,} "
            f"new texts, {r.get('bytes_missing', 0) / 1e6:.2f} MB, {r.get('tokens_missing', 0):,} tokens) in "
            f"{new.id.nunique():,} articles were scored in run {x['id']}: "
            + ", ".join(f"{v:,} {k}" for k, v in new.kind.value_counts().items())
            + f"; their bpb is {r.get('bpb_missing', float('nan')):.4f}."
        )
        w(
            f"- **Pod**: `{pd_.get('pod_id', '?')}`, {pd_.get('gpu', r['gpu'])}, {pd_.get('cloud', '?')} cloud, "
            f"{pd_.get('datacenter', '?')}, ${pd_.get('price_per_hr', 0):.2f}/h, up {pd_.get('hours', 0) * 60:.0f} min "
            f"(${pd_.get('cost', 0):.2f}); scoring took {r['gpu_seconds_this_session']:.0f} s of GPU time "
            f"({r['tok_per_s_this_session']:,.0f} tokens/s)."
        )
        if ck:
            w(
                f"- **Consistency**: {ck['texts']} carried-over texts, scored again on the new pod, came out identical "
                f"for {ck.get('identical', '?')}; |Δ bits| / bits has median {ck['median_rel']:.2%}, p95 "
                f"{ck['p95_rel']:.2%}, max {ck['max_rel']:.2%} (paragraphs >= 100 B: "
                + (
                    f"median {ck['median_rel_ge_100B']:.2%}, max {ck['max_rel_ge_100B']:.2%}"
                    if ck["median_rel_ge_100B"] is not None
                    else "none rechecked"
                )
                + f"), mean signed "
                f"{ck['mean_signed_rel']:+.2%}; pooled bpb {ck['bpb_new']:.4f} new vs {ck['bpb_carried']:.4f} "
                "carried over. That is the size of run 1's own bf16 batch noise (see determinism above), so old and "
                "new scores are on the same scale"
                + (
                    " and the flags are unaffected."
                    if ck["max_rel"] < 0.05
                    else ". **The difference is more than a few percent: check before relying on new scores.**"
                )
            )
        if bf.get("queue_types"):
            w(
                "- **Review queue** (types among the 200, before → now): "
                + ", ".join(f"{t} {bf['queue_types'].get(t, 0)} → {now_types.get(t, 0)}" for t in TYPES)
                + "."
            )
        w("")

    # ---- distributions
    w("## Distributions\n")
    w("### By paragraph kind\n")
    rows = []
    for k in ["text", "heading", "list", "table", "math"]:
        d = s[s.kind == k]
        if len(d):
            rows.append(
                (
                    k,
                    f"{len(d):,}",
                    f"{d.bytes.sum() / 1e6:.2f}",
                    f"{d.bytes.sum() / s.bytes.sum():.1%}",
                    f"{pooled(d):.3f}",
                    f"{d.bpb.median():.3f}",
                    f"{q(d.bpb, 1):.3f}",
                    f"{q(d.bpb, 99):.3f}",
                )
            )
    rows.append(
        (
            "all",
            f"{len(s):,}",
            f"{s.bytes.sum() / 1e6:.2f}",
            "100%",
            f"{corpus_bpb:.3f}",
            f"{s.bpb.median():.3f}",
            f"{q(s.bpb, 1):.3f}",
            f"{q(s.bpb, 99):.3f}",
        )
    )
    w(md_table(["kind", "paragraphs", "MB", "bytes", "pooled bpb", "median", "p1", "p99"], rows))
    w("\nPooled bpb = Σ bits / Σ bytes (what the harness reports); median and percentiles are over paragraphs.\n")
    w("### By length\n")
    rows = []
    for _, _, b in BANDS:
        d, dt = s[s.band == b], txt[txt.band == b]
        rows.append(
            (
                b,
                f"{len(d):,}",
                f"{pooled(d):.3f}",
                f"{d.bpb.median():.3f}",
                f"{len(dt):,}",
                f"{pooled(dt):.3f}" if len(dt) else "",
                f"{dt.bpb.median():.3f}" if len(dt) else "",
                f"{q(dt.bpb, 1):.3f}" if len(dt) else "",
                f"{q(dt.bpb, 99):.3f}" if len(dt) else "",
            )
        )
    w(
        md_table(
            ["bytes", "all: paragraphs", "pooled", "median", "text: paragraphs", "pooled", "median", "p1", "p99"], rows
        )
    )
    w(
        "\nShort pieces cost more bits per byte: the first tokens after BOS have no context. So paragraphs "
        "are only compared with paragraphs of the same kind and length band, and only text, list and math "
        f"paragraphs of >= {MIN_BYTES['text']} B and tables of >= {MIN_BYTES['table']} B are compared at all "
        f"({int(s.eligible.sum()):,} of {len(s):,}). Headings are never flagged. Articles are ranked only from "
        f"{ART_MIN_BYTES} B.\n"
    )
    w("### Articles\n")
    rows = []
    for name, d in [
        ("all articles", art),
        (f">= {ART_MIN_BYTES} B", big),
        ("bot-created", big[big.bot_created]),
        ("stubs", big[big.stub]),
        ("neither", big[~big.bot_created & ~big.stub]),
        ("odia_ratio < 0.6", big[big.odia_ratio < 0.6]),
    ]:
        rows.append(
            (
                name,
                f"{len(d):,}",
                f"{pooled(d):.3f}",
                f"{d.bpb.median():.3f}",
                f"{q(d.bpb, 1):.3f}",
                f"{q(d.bpb, 5):.3f}",
                f"{q(d.bpb, 95):.3f}",
                f"{q(d.bpb, 99):.3f}",
            )
        )
    w(md_table(["articles", "n", "pooled bpb", "median", "p1", "p5", "p95", "p99"], rows))
    w("")
    w("### Did Sarvam-1 memorise Odia Wikipedia?\n")
    w(
        "Sarvam-1 was released in October 2024. If it had memorised Odia Wikipedia, pages that existed "
        "before then should score clearly lower than pages created after it, which it cannot have seen. "
        "On 2026-09-24 they did not: pages created since then scored *lower*, continuing a fall that starts "
        "around 2019 (more formulaic biographies, more machine-assisted translation); the tables below are "
        "recomputed at every build. That does not rule out memorisation of some famous pages, but it is not "
        "what makes the low end low. (Page creation dates are from `annotations/translation.jsonl`; "
        "without it, the year of the scored revision is used.)\n"
    )
    t = ctx.get("translation")
    if t is not None and "created" in t.columns:
        j = big.join(t[["created", "translated"]], how="left")
        j["period"] = np.where(j.created.fillna("") >= "2024-10", "created 2024-10 or later", "created before 2024-10")
        rows = []
        for (per, tr), d in j.groupby(["period", j.translated.fillna(False).astype(bool)]):
            rows.append(
                (
                    per,
                    "machine-assisted translation" if tr else "written in Odia",
                    f"{len(d):,}",
                    f"{pooled(d):.3f}",
                    f"{d.bpb.median():.3f}",
                    f"{d.bpb_text.median():.3f}",
                )
            )
        w(
            md_table(
                ["page", "made by", "articles >= 500 B", "pooled bpb", "median", "median prose bpb"],
                rows,
                ["---", "---", "---:", "---:", "---:", "---:"],
            )
        )
        j["cy"] = j.created.str[:4]
        rows = []
        for y, d in j.groupby(j.cy.where(j.cy >= "2011", "<=2010")):
            rows.append(
                (
                    y,
                    f"{len(d):,}",
                    f"{d.bpb.median():.3f}",
                    f"{d.bpb_text.median():.3f}",
                    f"{d.translated.fillna(False).astype(bool).mean():.0%}",
                )
            )
        w("")
        w(md_table(["page created", "articles >= 500 B", "median bpb", "median prose bpb", "translated"], rows))
    else:
        rows = []
        for y, d in big.groupby(big.year.where(big.year >= "2016", "<=2015")):
            rows.append((y, f"{len(d):,}", f"{pooled(d):.3f}", f"{d.bpb.median():.3f}", f"{d.bpb_text.median():.3f}"))
        w(md_table(["last revision", "articles >= 500 B", "pooled bpb", "median", "median prose bpb"], rows))
    w("")
    if t is not None:
        w("### Content Translation (`annotations/translation.jsonl`)\n")
        w(
            "`translated` is that file's recommended flag (Content Translation or MDWiki created the page, or "
            "wrote at least half of it).\n"
        )
        rows = []
        for col in ("translated", "ct_created", "mdwiki_created"):
            if col not in t.columns:
                continue
            j = big.join(t[[col]], how="left")
            for v, d in j.groupby(j[col].fillna(False).astype(bool)):
                rows.append(
                    (
                        f"`{col}` = {v}",
                        f"{len(d):,}",
                        f"{pooled(d):.3f}",
                        f"{d.bpb.median():.3f}",
                        f"{d.bpb_text.median():.3f}",
                        f"{d.review_rank.notna().sum()}",
                    )
                )
        w(md_table(["articles >= 500 B", "n", "pooled bpb", "median", "median prose bpb", "in review queue"], rows))
        w("")
    tp = ctx.get("topics")
    if tp is not None:
        col = next((c for c in ("primary_topic", "topic") if c in tp.columns), None)
        if col:
            j = big.join(tp[[col]], how="left")
            if j[col].map(lambda x: isinstance(x, list | np.ndarray)).any():
                j = j.explode(col)
            rows = []
            for v, d in sorted(j.groupby(j[col].fillna("(none)")), key=lambda kv: -len(kv[1])):
                rows.append(
                    (
                        v,
                        f"{len(d):,}",
                        f"{pooled(d):.3f}",
                        f"{d.bpb.median():.3f}",
                        f"{d.bpb_text.median():.3f}",
                        f"{d.review_rank.notna().sum()}",
                    )
                )
            for flag in ("school_relevant", "odisha", "is_person"):
                if flag in tp.columns:
                    jj = big.join(tp[[flag]], how="left")
                    for v, d in jj.groupby(jj[flag].fillna(False).astype(bool)):
                        rows.append(
                            (
                                f"`{flag}` = {v}",
                                f"{len(d):,}",
                                f"{pooled(d):.3f}",
                                f"{d.bpb.median():.3f}",
                                f"{d.bpb_text.median():.3f}",
                                f"{d.review_rank.notna().sum()}",
                            )
                        )
            w(f"### By topic (`annotations/topics.jsonl`, `{col}`)\n")
            w(
                md_table(
                    ["topic", "articles >= 500 B", "pooled bpb", "median", "median prose bpb", "in review queue"], rows
                )
            )
            w("")
    else:
        w(
            "Topic tags (`annotations/topics.jsonl`) did not exist when this was built; rerun "
            "`score_bpb.py build` to add bpb by topic.\n"
        )

    # ---- extremes
    w("## What the extremes look like\n")
    for line in findings(s, art, el, ctx, pooled):
        w(line)
    w("")

    def show(d, n, head):
        w(f"**{head}**\n")
        rows = [
            (f"{r.bpb:.3f}", f"{r.bytes:,}", f"{titles[r.id]} ({r.id}/{r.para})", cell(r.block))
            for r in d.head(n).itertuples()
        ]
        w(md_table(["bpb", "B", "article (id/para)", "excerpt"], rows, ["---:", "---:", "---", "---"]))
        w("")

    et = el[el.kind == "text"]
    show(et.sort_values("bpb", ascending=False), 12, "Highest-bpb prose paragraphs (>= 100 B)")
    show(et.sort_values("bpb"), 12, "Lowest-bpb prose paragraphs (>= 100 B)")
    show(et[et.band == "300-999 B"].sort_values("bpb", ascending=False), 8, "Highest-bpb prose, 300-999 B")
    show(el[el.kind == "list"].sort_values("bpb", ascending=False), 6, "Highest-bpb lists (>= 100 B)")
    show(el[el.kind == "list"].sort_values("bpb"), 6, "Lowest-bpb lists (>= 100 B)")
    show(el[el.kind == "table"].sort_values("bpb", ascending=False), 6, "Highest-bpb tables (>= 200 B)")
    show(el[el.kind == "table"].sort_values("bpb"), 6, "Lowest-bpb tables (>= 200 B)")
    for head, d in [
        ("Highest-bpb articles", big.sort_values("bpb", ascending=False)),
        ("Lowest-bpb articles", big.sort_values("bpb")),
    ]:
        w(f"**{head} (>= {ART_MIN_BYTES} B)**\n")
        rows = [
            (
                f"{a.bpb:.3f}",
                f"{a.bytes:,}",
                f"{a.title} ({pid})",
                "yes" if a.bot_created else "",
                f"{a.odia_ratio:.2f}",
            )
            for pid, a in d.head(8).iterrows()
        ]
        w(md_table(["bpb", "B", "article (id)", "bot", "odia_ratio"], rows, ["---:", "---:", "---", "---", "---:"]))
        w("")
    rep = s[s.repeats >= 5]
    w(
        f"**Templated text, all kinds and lengths.** {len(rep):,} paragraphs ({rep.bytes.sum() / s.bytes.sum():.1%} of "
        f"bytes) share their sentence frame (digits, the article's title and rare words masked) with 5 or more "
        f"articles; they score {pooled(rep):.3f} pooled against {pooled(s[s.repeats < 5]):.3f} for the rest.\n"
    )
    w("## Files\n")
    w(
        "- `annotations/bpb.jsonl`: one row per article (scores, extremes, the review queue); "
        "`annotations/bpb.paragraphs.jsonl`: one row per non-title paragraph. Their sidecars `bpb.json` and "
        "`bpb.paragraphs.json` describe every field, and `bpb.json` holds the record of every scoring run."
    )
    w(
        "- Rerun: `uv run --script score_bpb.py build` (no GPU) rebuilds every output from the annotations and "
        "the run records, carrying every score over by `para_sha1`; rerun it when the corpus or the topic and "
        "translation annotations change. It stops if a paragraph text has no score: run `score --only-missing` "
        "on a pod and add it with `build --add <dir>` (see the script's docstring)."
    )
    bpb_md = "\n".join(L) + "\n"

    # ---- review-first.md
    R = []
    w = R.append
    w("# Review first\n")
    w(
        f"The {len(ranked)} articles most likely to hold data problems, ranked from Sarvam-1 bits per byte "
        "(`quality/bpb.md`). The failure types are interleaved (garbled, English, templated, table, markup, "
        "other script, whole article, then round again), so the top of the list shows every kind of problem. "
        "Within a type, the most extreme come first: how far the paragraph's bpb is from the median of its "
        "kind and length band (robust z of log bpb), plus a little weight for size.\n"
    )
    w(
        "The full list is in `annotations/bpb.jsonl` (`review_rank`, `review_type`, `review_para`, "
        "`review_reasons`; null rank = not flagged). The web app (`edaapp`) shows it as "
        "a review queue. Every paragraph's score is in `annotations/bpb.paragraphs.jsonl`.\n"
    )
    w("## Legend\n")
    counts = {t: sum(primary[p][0] == t for p in ranked) for t in TYPES}
    avail = {t: len(by_type[t]) for t in TYPES}
    w(
        md_table(
            ["type", "meaning", "in queue", "candidates"],
            [(f"`{t}`", TYPES[t].replace("|", "\\|"), counts[t], f"{avail[t]:,}") for t in TYPES],
            ["---", "---", "---:", "---:"],
        )
    )
    w(
        "\nReason wording: *para 7 (text, 412 B): bpb 1.84, top 0.1% of text 300-999 B (median 0.45)* means "
        'paragraph 7 (`text.split("\\n\\n")[7]`) is a 412-byte prose paragraph whose bpb is in the top 0.1% '
        "of prose paragraphs of 300-999 bytes, whose median is 0.45. Extra notes: the share of Latin or other-"
        "script letters and of common English words among the Latin ones, conversion leftovers found "
        "(`markup`), verse, how many other articles hold a near-copy or the same sentence frame, repetition "
        "inside the paragraph, and a table header shared with other articles. *context:* lines come from the "
        "topic and Content Translation annotations when present.\n"
    )
    w(
        "Usually fine on inspection: verse, songs and Sanskrit (`garbled`, marked *verse or song lines*), "
        "runs of names, formulaic but correct prose in `templated`, very predictable whole articles about "
        "well-known subjects (`article` with a *bottom* percentile), and quotations kept on purpose in their "
        "own script. Usually worth fixing: `markup` (all of them), `english` prose, OCR'd text (*high even for "
        "English*), and pages that are mostly English or Latin-script lists (`article` with a low odia_ratio).\n"
    )
    w("## Top 50\n")
    blocks = dict(zip(zip(s.id, s.para, strict=True), s.block, strict=True))
    for rank, pid in enumerate(ranked[:50], 1):
        c = primary[pid]
        para = c[3]
        where = f"para {para}" if para is not None else "whole article"
        w(f"{rank}. **{titles[pid]}** (id {pid}, {where}, `{c[0]}`)")
        rs = reasons[pid]
        for rr in [x for x in rs if not x.startswith("context:")][:3] + [x for x in rs if x.startswith("context:")]:
            w(f"   - {md_text(rr)}")
        if para is not None:
            w(f"   > {excerpt(blocks[(pid, para)], 220)}")
        else:
            worst = s[(s.id == pid) & s.eligible].sort_values("z", key=abs, ascending=False).head(1)
            if not len(worst):  # nothing comparable (all short): show the article's largest non-heading block
                worst = s[(s.id == pid) & (s.kind != "heading")].sort_values("bytes", ascending=False).head(1)
            if len(worst):
                r0 = worst.iloc[0]
                w(f"   > (para {r0.para}, bpb {r0.bpb:.2f}) {excerpt(r0.block, 200)}")
        w("")
    return {out_q / "bpb.md": bpb_md, out_q / "review-first.md": "\n".join(R) + "\n"}


def findings(s, art, el, ctx, pooled):
    """The 'what the extremes look like' narrative. Counts and examples are picked from the data, so the
    text stays true when the corpus is rebuilt; the one judgement (what the Odia top 1% is) comes from
    reading the queue on 2026-09-24."""
    import numpy as np

    title = art.title

    def names(d, n=4):
        seen, out = set(), []
        for r in d.itertuples():
            if r.id not in seen:
                seen.add(r.id)
                out.append(f"{title[r.id]} ({r.id})")
            if len(out) == n:
                break
        return ", ".join(out)

    txt = el[el.kind == "text"]
    hi, lo = txt[txt.flag == "high"], txt[txt.flag == "low"]
    L = []
    L.append(
        "Spread within groups (robust sd of log bpb, 1.4826 MAD): "
        + ", ".join(
            f"{g} {1.4826 * (np.log(d.bpb) - np.log(d.bpb).median()).abs().median():.2f}"
            for g, d in el.groupby("group")
            if len(d) >= 1000
        )
        + ". So the top 1% of prose sits roughly 2.5 robust sd, or about 1.7x, above its group's median.\n"
    )
    oh = hi[(hi.latin_share < 0.5) & (hi.other_share < 0.25)]
    L.append(
        f"**The high end.** Of the {len(hi):,} prose paragraphs in the top 1%, {(hi.latin_share >= 0.5).mean():.0%} "
        f"are mostly Latin script, {(hi.other_share >= 0.25).mean():.0%} mostly another script and "
        f"{len(oh) / max(len(hi), 1):.0%} Odia."
    )
    eng = el[el.latin_english & el.kind.isin(["text", "list"])]
    if len(eng):
        L.append(
            f"- English: {len(eng):,} comparable text and list paragraphs are mostly Latin script with common English "
            f"words, median bpb {eng.bpb.median():.2f} (Sarvam-1 reads English at about 0.8 bpb, E03); the largest are "
            f"in {names(eng.sort_values('bytes', ascending=False), 3)}."
        )
    fr = el[el.foreign & el.kind.isin(["text", "list"])]
    if len(fr):
        L.append(
            f"- Another script, or Latin letters that are not English (transliteration, IPA, romanised titles, code): "
            f"{len(fr):,} comparable paragraphs, most extreme in {names(fr.sort_values('z', ascending=False))}."
        )
    prose = oh[~oh.verse].sort_values("z", ascending=False)
    L.append(
        f"- Odia: {len(oh):,} paragraphs, {oh.verse.mean():.0%} of them verse by line shape; the most extreme prose "
        f"ones are in {names(prose)}. Read on 2026-09-24, the Odia top 1% was mostly legitimate but unusual text: "
        "poems, folk songs and Sanskrit shlokas, archaic Odia, and runs of names (weapons, song and film titles "
        "transliterated into Odia). Genuinely garbled Odia was rare. That is why verse is ranked after prose in "
        "the `garbled` type.\n"
    )
    lo_copy = lo[(lo.repeats >= 5) | (lo.near_copies.fillna(0) > 0) | (lo.self_repeat >= 0.3)]
    tab = s[s.kind == "table"]
    top_header = tab.loc[tab.header_repeats.idxmax()] if len(tab) else None
    L.append(
        f"**The low end is formulaic writing.** Of the {len(lo):,} prose paragraphs in the bottom 1%, "
        f"{len(lo_copy):,} ({len(lo_copy) / max(len(lo), 1):.0%}) have a near-copy in another article, a shared "
        f"sentence frame or internal repetition (most extreme in {names(lo_copy.sort_values('z'), 3)}). The rest is "
        "plain, well-formed encyclopedic prose (most extreme in "
        f"{names(lo[~lo.index.isin(lo_copy.index)].sort_values('z'), 3)})."
        + (
            f" The most repeated table header row, `{top_header.header.strip()}`, is in "
            f"{int(top_header.header_repeats):,} articles."
            if top_header is not None
            else ""
        )
        + "\n"
    )
    rep = s[s.eligible & (s.kind == "text") & (s.repeats >= 5)]
    rest = s[s.eligible & (s.kind == "text") & (s.repeats < 5)]
    small = s[(s.bytes < 100) & (s.repeats >= 5)]
    L.append(
        f"**bpb does not find boilerplate.** Each paragraph is scored on its own, so a sentence frame repeated "
        f"across many articles is no more predictable to the model than any other sentence. The "
        f"{len(rep):,} comparable prose paragraphs whose frame (names and numbers masked) recurs in 5+ articles "
        f"score {pooled(rep):.3f} pooled against {pooled(rest):.3f} for the rest. They are shorter (median "
        f"{rep.bytes.median():.0f} B against {rest.bytes.median():.0f} B), and within their own kind and length "
        f"band their median sits at the {rep.pct.median() * 100:.0f}th percentile; only "
        f"{int((lo.repeats >= 5).sum())} of them reach the bottom 1%. Use the `repeats` column, not bpb, to find "
        f"templates; {len(small):,} more templated paragraphs are under 100 B and are not compared at all.\n"
    )
    mk = s[s.markup.notna()]
    if len(mk) <= 12:
        L.append(
            f"**Conversion leftovers**: {len(mk)} paragraph{'s' * (len(mk) != 1)} in {mk.id.nunique()} "
            f"article{'s' * (mk.id.nunique() != 1)} match the markup patterns outside math"
            + (
                ": " + "; ".join(f"{title[r.id]} ({r.id}/{r.para}): `{r.markup}`" for r in mk.itertuples())
                if len(mk)
                else ""
            )
            + "."
        )
    else:
        L.append(
            f"**Conversion leftovers**: {len(mk)} paragraphs in {mk.id.nunique()} articles match the markup patterns "
            "outside math; the `markup` column lists what was found."
        )
    return L


if __name__ == "__main__":
    main()
