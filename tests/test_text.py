"""HTML-to-Markdown cleaning of rich-text comments, and the line-per-item serializer."""

from __future__ import annotations

import json

import pytest

from pestrail.serialize import compact, to_json_text
from pestrail.shaping import clean_text, shape_comment
from pestrail.text import html_to_markdown, looks_like_html

#: Inline CSS like Word/Jira/web pastes put on every paragraph (~1,000 characters).
STYLE = (
    "margin: 0px 0px 12px; padding: 0px; border: 0px; font-style: normal; "
    "font-variant-ligatures: normal; font-variant-caps: normal; "
) * 8


def styled(*paragraphs: str) -> str:
    return "".join(f'<p data-pasted="true" style="{STYLE}">{p}</p>' for p in paragraphs)


# ----------------------------------------------------------------- detection


@pytest.mark.parametrize(
    "text",
    [
        "<p>x</p>",
        "  <div>x</div>",
        "<P STYLE='a'>x</P>",
        "<br>x",
        "<!-- c --><p>x</p>",
        "<span>x</span>",
        "<ul><li>x</li></ul>",
        "<o:p></o:p>x",
    ],
)
def test_looks_like_html(text):
    assert looks_like_html(text)


@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        "a < b and c > d",
        "**bold** and _italic_",
        "1. step one\n2. step two",
        "<not a tag> text",
        "Text then <p>tag</p>",  # must *start* with a tag
        "",
    ],
)
def test_plain_and_markdown_are_not_html(text):
    assert not looks_like_html(text)


# ----------------------------------------------------------------- conversion


def test_styled_paragraph_becomes_its_text():
    html = styled("Real text")
    assert len(html) > 1000
    assert html_to_markdown(html) == "Real text"


def test_paragraphs_and_line_breaks():
    assert html_to_markdown("<p>one<br>two</p><p>three</p>") == "one\ntwo\n\nthree"
    assert html_to_markdown("<p>a<br><br>b</p>") == "a\n\nb"
    assert html_to_markdown("<p>a</p>\n\n\n<p>b</p>") == "a\n\nb"


def test_entities_and_whitespace():
    assert html_to_markdown("<p>a&nbsp;&nbsp;b &lt;tag&gt; &amp; &quot;q&quot;</p>") == (
        'a b <tag> & "q"'
    )
    assert html_to_markdown("<p>  lots \n\t of   space  </p>") == "lots of space"
    assert html_to_markdown("<p>zero​width﻿</p>") == "zerowidth"


def test_lists():
    assert html_to_markdown("<ul><li>a</li><li>b</li></ul>") == "- a\n- b"
    assert html_to_markdown("<ol><li>a</li><li>b</li></ol>") == "1. a\n2. b"
    assert html_to_markdown("<ol><li><p>a</p></li><li><p>b</p></li></ol>") == "1. a\n2. b"
    nested = "<ul><li>a<ul><li>a1</li><li>a2</li></ul></li><li>b</li></ul><p>after</p>"
    assert html_to_markdown(nested) == "- a\n  - a1\n  - a2\n- b\n\nafter"


def test_headings_bold_italic_code():
    html = "<h2>Title</h2><p><strong>bold</strong>, <em>it</em>, <code>x = 1</code></p>"
    assert html_to_markdown(html) == "## Title\n\n**bold**, _it_, `x = 1`"


def test_preformatted_text_keeps_whitespace():
    html = "<p>Log:</p><pre>  indented\n    more  spaces</pre><p>end</p>"
    assert html_to_markdown(html) == "Log:\n\n```\n  indented\n    more  spaces\n```\n\nend"


def test_links():
    assert html_to_markdown('<p><a href="https://j.example/UI-5">UI-5</a></p>') == (
        "UI-5 (https://j.example/UI-5)"
    )
    # Link text equal to the URL is not repeated.
    assert html_to_markdown('<a href="https://x.example">https://x.example</a>') == (
        "https://x.example"
    )
    # Script and in-page anchors are dropped, the text is kept.
    assert html_to_markdown('<p><a href="javascript:alert(1)">click</a></p>') == "click"
    assert html_to_markdown('<p><a href="#top">top</a></p>') == "top"


def test_tables_become_rows():
    html = (
        "<table><tr><th>Env</th><th>Result</th></tr>"
        "<tr><td><p>Chrome</p></td><td><p>Pass</p></td></tr></table><p>x</p>"
    )
    assert html_to_markdown(html) == "Env | Result\nChrome | Pass\n\nx"


def test_images():
    assert html_to_markdown('<p>see <img src="a.png" alt="screenshot"></p>') == (
        "see [image: screenshot]"
    )
    assert html_to_markdown('<p><img src="a.png"/></p>') == "[image]"


def test_hidden_content_is_dropped():
    html = "<!-- note to self --><style>p{color:red}</style><script>alert(1)</script><p>visible</p>"
    assert html_to_markdown(html) == "visible"


def test_malformed_html_does_not_crash():
    assert html_to_markdown("<p>unclosed <strong>bold <em>x") == "unclosed **bold _x"
    assert html_to_markdown("<p>a</div></li></ul>b") == "a\n\nb"
    assert html_to_markdown("") == ""


def test_word_paste_example():
    html = (
        styled(
            "Tested on <strong>build 4.2</strong>&nbsp;in staging.",
            "Steps:",
        )
        + "<ol><li>Open login</li><li>Enter &lt;user&gt;</li></ol>"
    )
    assert html_to_markdown(html) == (
        "Tested on **build 4.2** in staging.\n\nSteps:\n\n1. Open login\n2. Enter <user>"
    )


# ----------------------------------------------------------------- shape_comment


def test_clean_text_leaves_non_html_alone():
    assert clean_text("a < b **bold**") == "a < b **bold**"
    assert clean_text(None) is None
    assert clean_text(5) == 5


def test_comment_is_cleaned_before_truncating():
    real = "word " * 1000  # 5,000 characters of real text
    out = shape_comment(styled(real), max_chars=100)
    cleaned = real.strip()
    assert "style" not in out and "margin" not in out
    assert out == cleaned[:100] + f"... [truncated, {len(cleaned)} chars total]"


def test_full_comment_and_raw_format():
    html = styled("x" * 3000)
    assert shape_comment(html, max_chars=100, full=True) == "x" * 3000
    raw = shape_comment(html, max_chars=1000, fmt="raw")
    assert raw == html[:1000] + f"... [truncated, {len(html)} chars total]"


# ----------------------------------------------------------------- serializer


def test_to_json_text_puts_list_items_on_own_lines():
    value = {
        "results": [{"id": 1, "comment": "line1\nline2"}, {"id": 2, "comment": "Ödeme"}],
        "paging": {"offset": 0, "has_more": False},
    }
    text = to_json_text(value)
    assert json.loads(text) == value
    lines = text.splitlines()
    assert compact({"id": 1, "comment": "line1\nline2"}) + "," in lines
    assert compact({"id": 2, "comment": "Ödeme"}) in lines
    assert "Ödeme" in text  # not \u-escaped


def test_to_json_text_simple_values_stay_compact():
    assert (
        to_json_text({"a": [], "b": [1, 2], "c": {"d": 1}}) == '{"a":[],\n"b":[1,2],\n"c":{"d":1}}'
    )
    assert to_json_text([1, 2]) == "[1,2]"
