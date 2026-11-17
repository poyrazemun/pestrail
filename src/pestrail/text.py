"""Turn rich-text HTML stored in TestRail into lightweight Markdown.

Comments written in TestRail's rich-text editor, or pasted from Word/Jira/the web, are
stored as HTML where every paragraph can carry ~1,000 characters of inline CSS. Shown raw
(and cut to a length limit), the assistant would see only CSS. This module keeps the text
and its structure (paragraphs, line breaks, lists, headings, bold/italic, code, links,
table rows) and drops everything else: attributes, styles, scripts and HTML comments.

Only the standard library is used (``html.parser``), so there is no new dependency.
Text that does not start with an HTML tag (plain text or Markdown from TestRail's
classic editor) is never converted, so a comment such as ``a < b`` stays untouched.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# A comment counts as HTML only if it *starts* with a known tag (or an HTML comment),
# which is how TestRail's rich-text editor and pasted content are stored.
_HTML_START = re.compile(
    r"^\s*(?:<!--|<(?:p|div|span|br|ul|ol|li|table|thead|tbody|tr|td|th|h[1-6]|pre|code|"
    r"strong|b|em|i|u|s|a|img|blockquote|font|hr|section|article|html|body|meta|style|"
    r"o:p)\b)",
    re.IGNORECASE,
)

_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "main", "nav", "aside",
        "blockquote", "table", "thead", "tbody", "tfoot", "dl", "dt", "dd", "figure",
        "figcaption", "address", "form", "fieldset", "center",
    }
)  # fmt: skip
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "template", "noscript"})
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
# Zero-width space, byte-order mark and soft hyphen: invisible noise from pasted text.
_INVISIBLE = str.maketrans({"\u200b": None, "\ufeff": None, "\u00ad": None})
_WS = re.compile(r"\s+")

#: Hard cap on HTML converted per call. Real comments are far smaller; the cap bounds
#: CPU and memory for hostile input (and html.parser's own worst cases).
MAX_HTML_CHARS = 500_000
#: Deepest list indentation emitted; deeper nesting is flattened to this level so
#: thousands of nested lists cannot multiply the output size.
MAX_LIST_INDENT = 5
#: How many pieces after a link start are inspected to build its label (bounded, so
#: thousands of unclosed nested <a> tags cannot make conversion quadratic).
_LINK_LABEL_PIECES = 64


def looks_like_html(text: str) -> bool:
    """True if ``text`` starts with an HTML tag, i.e. it came from a rich-text editor."""
    return bool(_HTML_START.match(text))


def html_to_markdown(html: str) -> str:
    """Convert rich-text HTML to readable Markdown-like text (see module docstring).

    Input beyond MAX_HTML_CHARS is not converted; a marker says how much was skipped.
    """
    skipped = max(len(html) - MAX_HTML_CHARS, 0)
    parser = _Converter()
    parser.feed(html[:MAX_HTML_CHARS] if skipped else html)
    parser.close()
    text = parser.result()
    if skipped:
        text += f"\n\n[... {skipped} more characters of HTML were not converted]"
    return text


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # decodes &nbsp; &lt; &amp; ... in data
        # Pieces are (kind, value): "text" (whitespace collapsed later), "raw" (markers
        # kept as-is), "pre" (preformatted, kept as-is) or "nl" (number of newlines).
        self.pieces: list[tuple[str, str | int]] = []
        self.skip_depth = 0
        self.pre_depth = 0
        self.cell_depth = 0
        self.lists: list[list[str | int]] = []  # stack of [kind, counter]
        self.cells: list[int] = []  # cells seen in each open table row
        self.links: list[tuple[str | None, int]] = []  # (href, index of first piece)
        self.after_marker = False  # just wrote a list marker: suppress block newlines

    # ------------------------------------------------------------------ emit helpers

    def _last_meaningful(self) -> int:
        """Index of the last piece that isn't whitespace-only source text, or -1."""
        for index in range(len(self.pieces) - 1, -1, -1):
            kind, value = self.pieces[index]
            if not (kind == "text" and not str(value).strip()):
                return index
        return -1

    def _nl(self, count: int, *, additive: bool = False) -> None:
        """Request a line break. Block boundaries merge (the larger break wins), so
        </p><p> or </li><li> never stack up blank lines; <br> adds up (<br><br> = blank)."""
        if self.after_marker:
            return
        if self.cell_depth:
            self._space()  # keep table rows on one line
            return
        if self.lists:
            count = min(count, 1)  # no blank lines inside lists
        last = self._last_meaningful()
        if last >= 0 and self.pieces[last][0] == "nl":
            previous = int(self.pieces[last][1])
            del self.pieces[last + 1 :]  # whitespace-only text between breaks is noise
            self.pieces[last] = ("nl", previous + count if additive else max(previous, count))
        else:
            self.pieces.append(("nl", count))

    def _space(self) -> None:
        last = self._last_meaningful()
        if (
            last >= 0
            and self.pieces[last][0] != "nl"
            and not str(self.pieces[last][1]).endswith(" ")
        ):
            self.pieces.append(("raw", " "))

    def _trim_trailing_space(self) -> None:
        while self.pieces and self.pieces[-1][0] in ("text", "raw"):
            kind, value = self.pieces[-1]
            stripped = str(value).rstrip()
            if stripped:
                self.pieces[-1] = (kind, stripped)
                return
            self.pieces.pop()

    def _raw(self, value: str) -> None:
        self.pieces.append(("raw", value))

    # ------------------------------------------------------------------ parser hooks

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        attributes = dict(attrs)
        if tag == "br":
            self._nl(1, additive=True)
        elif tag in _HEADINGS:
            self._nl(2)
            self._raw("#" * _HEADINGS[tag] + " ")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            self._nl(1)
        elif tag == "li":
            self.after_marker = False
            self._nl(1)
            depth = min(max(len(self.lists) - 1, 0), MAX_LIST_INDENT)
            marker = "- "
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] = int(self.lists[-1][1]) + 1
                marker = f"{self.lists[-1][1]}. "
            self._raw("  " * depth + marker)
            self.after_marker = True
        elif tag == "tr":
            self._nl(1)
            self.cells.append(0)
        elif tag in ("td", "th"):
            if self.cells:
                if self.cells[-1]:
                    self._trim_trailing_space()
                    self._raw(" | ")
                self.cells[-1] += 1
            self.cell_depth += 1
        elif tag in ("strong", "b"):
            self._raw("**")
        elif tag in ("em", "i"):
            self._raw("_")
        elif tag == "code" and not self.pre_depth:
            self._raw("`")
        elif tag == "pre":
            self._nl(2)
            self._raw("```\n")
            self.pre_depth += 1
        elif tag == "a":
            self.links.append((attributes.get("href"), len(self.pieces)))
        elif tag == "img":
            alt = (attributes.get("alt") or "").strip()
            self._raw(f"[image: {alt}]" if alt else "[image]")
        elif tag == "hr":
            self._nl(2)
            self._raw("---")
            self._nl(2)
        elif tag in _BLOCK_TAGS:
            self._nl(2)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/>, <img/>, <hr/>: same as a start tag with no end tag.
        self.handle_starttag(tag, attrs)
        if tag.lower() in _SKIP_TAGS:
            self.skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self.skip_depth = max(self.skip_depth - 1, 0)
            return
        if self.skip_depth:
            return
        if tag in _HEADINGS:
            self._nl(2)
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.after_marker = False
            self._nl(2 if not self.lists else 1)
        elif tag == "li":
            self.after_marker = False
        elif tag == "tr":
            if self.cells:
                self.cells.pop()
            self._nl(1)
        elif tag in ("td", "th"):
            self.cell_depth = max(self.cell_depth - 1, 0)
        elif tag in ("strong", "b"):
            self._raw("**")
        elif tag in ("em", "i"):
            self._raw("_")
        elif tag == "code" and not self.pre_depth:
            self._raw("`")
        elif tag == "pre":
            self.pre_depth = max(self.pre_depth - 1, 0)
            self._raw("\n```")
            self._nl(2)
        elif tag == "a" and self.links:
            href, start = self.links.pop()
            window = self.pieces[start : start + _LINK_LABEL_PIECES]
            label = "".join(str(v) for k, v in window if k in ("text", "raw"))
            label = _WS.sub(" ", label).strip()
            href = (href or "").strip()
            if href and not href.lower().startswith(("javascript:", "#")) and href != label:
                self._raw(f" ({href})")
        elif tag in _BLOCK_TAGS:
            self._nl(2)

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data:
            return
        if self.pre_depth:
            self.pieces.append(("pre", data))
        else:
            if data.strip():
                self.after_marker = False
            elif (
                self.pieces and self.pieces[-1][0] == "text" and not str(self.pieces[-1][1]).strip()
            ):
                return  # consecutive whitespace is one space anyway; avoids O(n^2) scans
            self.pieces.append(("text", data))

    # HTML comments (<!-- ... -->), doctypes and processing instructions are dropped by
    # HTMLParser's default no-op handlers.

    # ------------------------------------------------------------------ assembly

    def result(self) -> str:
        out: list[str] = []
        at_line_start = True
        for kind, value in self.pieces:
            if kind == "nl":
                out.append("\n" * int(value))
                at_line_start = True
                continue
            piece = str(value)
            if kind == "text":
                piece = _WS.sub(" ", piece.translate(_INVISIBLE))
                if at_line_start:
                    piece = piece.lstrip()
            if piece:
                out.append(piece)
                at_line_start = piece.endswith("\n")
        text = "".join(out)
        text = text.replace("****", "").replace("**  **", " ")  # empty bold from Word
        lines = [line.rstrip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
