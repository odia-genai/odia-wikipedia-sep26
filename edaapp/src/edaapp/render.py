"""Server-side Markdown rendering: CommonMark + GFM tables + $math$, raw HTML disabled.

Articles are rendered one paragraph (text.split("\\n\\n")[i]) at a time, so each block can be
wrapped, shaded and toggled on its own. That gives the same result as rendering the whole text
because the corpus has no constructs that span a blank line (no loose lists, fenced code or
link reference definitions); a heading, list block or table is always one paragraph.

Documents (METHODOLOGY.md, quality/*.md, README.md, LEARNINGS.md) are rendered whole by
`render_doc`, which adds heading ids and a table of contents, uses stricter math delimiters
(hand-written text has unescaped dollar amounts), and turns references into app links
(`id 12345`, `quality/x.md`, `annotations/x.jsonl`, `excluded.jsonl`, `README.md`, ...), see
`LinkContext`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote, urlencode

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin


def _math_inline_double(self, tokens, idx, options, env) -> str:
    # $$...$$ inside a paragraph: display math, but in a <span> so the paragraph stays valid HTML
    return f'<span class="math display">{escapeHtml(tokens[idx].content.strip())}</span>'


def _safe_link(url: str) -> bool:
    u = url.strip().lower()
    return not (u.startswith("javascript:") or u.startswith("vbscript:") or u.startswith("data:"))


@lru_cache(maxsize=1)
def md() -> MarkdownIt:
    m = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    m.enable("table")
    # $...$ inline and $$...$$ display math, rendered as <span class="math inline"> / <div
    # class="math block"> holding the escaped TeX; the browser typesets them with KaTeX.
    # The corpus escapes literal dollars (\$), so every unescaped $ pair is math.
    m.use(dollarmath_plugin, allow_space=True, allow_digits=True, double_inline=True)
    m.add_render_rule("math_inline_double", _math_inline_double)
    m.validateLink = _safe_link
    return m


def render(text: str) -> str:
    return md().render(text)


# ---- documents -------------------------------------------------------------------------------


@dataclass
class LinkContext:
    """What references in a document can link to (only targets that exist get a link)."""

    dataset: str
    ids: frozenset[int] = frozenset()
    reports: frozenset[str] = frozenset()  # file names in quality/
    annotations: frozenset[str] = frozenset()  # annotation keys: "bpb", "bpb.paragraphs"
    docs: frozenset[str] = frozenset()  # README.md, LEARNINGS.md, METHODOLOGY.md present
    review_first: str | None = None  # the report shown as the Review first page
    side: frozenset[str] = frozenset()  # excluded.jsonl, removed-blocks.jsonl present
    extra: dict = field(default_factory=dict)

    def base(self) -> str:
        return f"#/d/{quote(self.dataset, safe='')}"

    def article(self, id: int) -> str | None:
        return f"{self.base()}/a/{id}" if id in self.ids else None

    def report(self, name: str) -> str | None:
        if name not in self.reports:
            return None
        if name == self.review_first:
            return f"{self.base()}/review-first"
        return f"{self.base()}/reports?{urlencode({'name': name, 'group': 'quality'})}"

    def annotation(self, stem: str) -> str | None:
        # annotations/<name>.jsonl (.jsonl.gz, .parquet), <name>.paragraphs.jsonl, <name>.json sidecars
        key = stem
        if key not in self.annotations and key.endswith(".json"):
            key = key[:-5]
        if key not in self.annotations:
            return None
        return f"{self.base()}?{urlencode({'ann': key})}"

    def side_file(self, name: str) -> str | None:
        """excluded.jsonl -> the Excluded view; removed-blocks.jsonl -> its Removed blocks tab."""
        if name not in self.side:
            return None
        return f"{self.base()}/excluded" + ("?tab=blocks" if name.startswith("removed-blocks") else "")

    def doc(self, name: str) -> str | None:
        if name not in self.docs:
            return None
        if name == "METHODOLOGY.md":
            return f"{self.base()}/methodology"
        return f"{self.base()}/reports?{urlencode({'name': name, 'group': 'docs'})}"


def _ref_regex(dataset: str) -> re.Pattern:
    pre = rf"(?:data/{re.escape(dataset)}/)?"
    return re.compile(
        # never inside a longer path or word ("translations/README.md", "valid 12")
        rf"(?<![\w/.-])(?:"
        rf"(?P<id>(?:[Pp]age )?id (?P<num>\d+))(?![\d.]\d)"
        rf"|(?P<quality>{pre}quality/(?P<qname>[\w.-]+\.md))"
        rf"|(?P<ann>{pre}annotations/(?P<aname>[\w.-]+?)\.(?:parquet|jsonl\.gz|jsonl|json))"
        rf"|(?P<doc>{pre}(?P<dname>README|LEARNINGS|METHODOLOGY)\.md)"
        rf"|(?P<side>{pre}(?P<sname>excluded|removed-blocks)\.jsonl)"
        rf")(?![\w/-])"
    )


def _target(m: re.Match, ctx: LinkContext) -> str | None:
    if m.group("id"):
        return ctx.article(int(m.group("num")))
    if m.group("quality"):
        return ctx.report(m.group("qname"))
    if m.group("ann"):
        return ctx.annotation(m.group("aname"))
    if m.group("doc"):
        return ctx.doc(m.group("dname") + ".md")
    if m.group("side"):
        return ctx.side_file(m.group("sname") + ".jsonl")
    return None


def _link_tokens(href: str, inner: list[Token]) -> list[Token]:
    op = Token("link_open", "a", 1, attrs={"href": href, "class": "ref"})
    return [op, *inner, Token("link_close", "a", -1)]


def _autolink_rule(state) -> None:
    """Core rule: turn references in text and code spans into links (not inside existing links)."""
    ctx: LinkContext | None = state.env.get("links")
    if ctx is None:
        return
    rx = _ref_regex(ctx.dataset)
    for tok in state.tokens:
        if tok.type != "inline" or not tok.children:
            continue
        out: list[Token] = []
        depth = 0
        for ch in tok.children:
            if ch.type == "link_open":
                depth += 1
            elif ch.type == "link_close":
                depth -= 1
            if depth or ch.type not in ("text", "code_inline"):
                out.append(ch)
                continue
            if ch.type == "code_inline":
                m = rx.fullmatch(ch.content.strip())
                href = _target(m, ctx) if m else None
                out.extend(_link_tokens(href, [ch]) if href else [ch])
                continue
            pos = 0
            for m in rx.finditer(ch.content):
                href = _target(m, ctx)
                if not href:
                    continue
                if m.start() > pos:
                    out.append(Token("text", "", 0, content=ch.content[pos : m.start()]))
                out.extend(_link_tokens(href, [Token("text", "", 0, content=m.group(0))]))
                pos = m.end()
            if pos == 0:
                out.append(ch)
            elif pos < len(ch.content):
                out.append(Token("text", "", 0, content=ch.content[pos:]))
        tok.children = out


def slugify(text: str) -> str:
    """A heading id that keeps every script (Odia letters and vowel signs included)."""
    s = text.strip().lower()  # no Unicode normalisation (repo rule for Odia text)
    s = re.sub(r"[\s_]+", "-", s)
    s = "".join(c for c in s if c == "-" or unicodedata.category(c)[0] in "LMN")
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "section"


def _heading_rule(state) -> None:
    """Core rule: give every heading a unique id and collect the table of contents."""
    toc = state.env.setdefault("toc", [])
    seen: dict[str, int] = state.env.setdefault("_slugs", {})
    toks = state.tokens
    for i, tok in enumerate(toks):
        if tok.type != "heading_open":
            continue
        inline = toks[i + 1]
        text = "".join(c.content for c in (inline.children or []) if c.type in ("text", "code_inline"))
        base = slugify(text)
        n = seen.get(base, 0)
        seen[base] = n + 1
        hid = base if n == 0 else f"{base}-{n}"
        tok.attrSet("id", hid)
        toc.append({"level": int(tok.tag[1]), "id": hid, "text": text.strip()})


@lru_cache(maxsize=1)
def doc_md() -> MarkdownIt:
    m = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    m.enable(["table", "strikethrough"])
    # pandoc's rules: no space just inside the dollars and no digit right after the closing one,
    # so "$0.53/h ... costing $0.26" stays text while "$x^2$" is math
    m.use(dollarmath_plugin, allow_space=False, allow_digits=False, double_inline=True)
    m.add_render_rule("math_inline_double", _math_inline_double)
    m.core.ruler.push("edaapp_headings", _heading_rule)
    m.core.ruler.push("edaapp_refs", _autolink_rule)
    m.validateLink = _safe_link
    return m


def render_doc(text: str, links: LinkContext | None = None) -> tuple[str, list[dict]]:
    """(html, toc) for a whole document; toc is [{level, id, text}] in document order."""
    env: dict = {"links": links}
    html = doc_md().render(text, env)
    return html, env.get("toc", [])
