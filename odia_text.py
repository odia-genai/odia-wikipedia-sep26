"""Odia text helpers the build scripts share: Odia script checks, normalisation, numbers, jsonl io.

A verbatim copy of `src/odia_llm/text.py` from odia-llm-trainer, the project this dataset was built
in (copied 2026-10-01, updated 2026-10-02), so this repository needs nothing outside itself. Keep the two in step:
`normalize_odia`, `odia_words` and `ODIA_DIGITS` decide what every article looks like.
"""

import gzip
import json
import re
from pathlib import Path

# Odia block plus the danda / double danda (U+0964/5), which Odia uses as its full stop.
ODIA_CHAR = re.compile(r"[\u0B00-\u0B7F\u0964\u0965]")
# Other Brahmic blocks (Devanagari..Malayalam, minus Odia). Their presence inside Odia
# text is a tell-tale of LLM translators drifting across scripts (e.g. a Kannada ಡ in an Odia word).
OTHER_INDIC_CHAR = re.compile(r"[\u0900-\u0963\u0966-\u0AFF\u0B80-\u0DFF]")
LATIN_CHAR = re.compile(r"[A-Za-z]")
ODIA_DIGITS = str.maketrans("୦୧୨୩୪୫୬୭୮୯", "0123456789")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
REPEAT_RUN = re.compile(r"([\u0B00-\u0B7F]{1,3})\1{3,}")  # an Odia 1-3 char unit repeated 4+ times: "ଦଦଦଦ"
WORD = re.compile(r"[\u0B00-\u0B7F]+")


YYA_DECOMPOSED = "\u0b2f\u0b3c"  # ଯ + nukta


def normalize_odia(text: str) -> str:
    """Spell ୟ the way native Odia text does.

    Machine-translated corpora (Sangraha's IndicTrans2 Wikipedia) write ୟ as ଯ + nukta
    (U+0B2F U+0B3C); native web text uses the precomposed U+0B5F (104k vs 0 and 1 vs 48k
    occurrences in E01). U+0B5F has no canonical decomposition, so no Unicode normal form
    unifies them, and Sarvam-1 spends 4 tokens instead of 1 on ବ୍ୟକ୍ତି in the decomposed
    spelling. ଡ଼/ଢ଼ need nothing: every corpus writes them decomposed and the tokenizers
    treat both spellings alike. Deliberately *not* NFC (see odia_ratio).
    """
    return text.replace(YYA_DECOMPOSED, "\u0b5f")


def _non_space(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def odia_ratio(text: str) -> float:
    """Fraction of non-space characters in the Odia Unicode block.

    Deliberately no NFC normalisation anywhere in this package: U+0B5C/U+0B5D
    (ଡ଼ ଢ଼) are Unicode composition exclusions, so NFC silently rewrites them
    into two code points and shifts the text away from what tokenizers saw.
    """
    n = _non_space(text)
    return len(ODIA_CHAR.findall(text)) / n if n else 0.0


def other_indic_ratio(text: str) -> float:
    n = _non_space(text)
    return len(OTHER_INDIC_CHAR.findall(text)) / n if n else 0.0


def latin_ratio(text: str) -> float:
    n = _non_space(text)
    return len(LATIN_CHAR.findall(text)) / n if n else 0.0


def numbers(text: str) -> list[str]:
    """Numbers in the text, Odia digits mapped to ASCII, separators removed."""
    return [re.sub(r"[.,]", "", m) for m in NUMBER.findall(text.translate(ODIA_DIGITS))]


def odia_words(text: str) -> list[str]:
    return WORD.findall(text)


def read_jsonl(path):
    """The rows of a JSON lines file; a path ending in .gz is read through gzip."""
    with (gzip.open if str(path).endswith(".gz") else open)(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows, mode="w"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def parse_arc_options(text):
    """Split tripathysagar/odia-arc's one-line options "A: ... B: ... C: ..." into (labels, options).

    Returns None unless the labels come out as a clean A,B,C.. or 1,2,3.. sequence.
    """
    parts = re.split(r"(?:^|\s)([A-E1-5]):\s*", text)
    labels, opts = parts[1::2], [p.strip() for p in parts[2::2]]
    if len(labels) >= 3 and labels in (list("ABCDE"[: len(labels)]), list("12345"[: len(labels)])) and all(opts):
        return labels, opts
    return None
