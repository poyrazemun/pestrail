# Changelog

## 0.2.0 (unreleased)

Readable results: comments pasted from Word/Jira/the web were unreadable, and large result
lists could exceed the MCP client's size limit.

### Changed
- Result comments stored as rich-text HTML are converted to readable Markdown (paragraphs,
  line breaks, lists, headings, bold/italic, code, links, table rows) **before** being cut.
  Previously the raw HTML was cut at 1,000 characters, often before the first real word.
  Plain-text and Markdown comments are unchanged. Use `comment_format: "raw"` for the old
  behavior.
- The default comment length is now 2,000 characters of cleaned text (was 1,000 of raw).
- List responses are kept within an output size budget (default 40,000 characters,
  `PESTRAIL_MAX_OUTPUT_CHARS`). Larger pages are cut and `paging.next_offset` points to
  the first left-out item, so nothing is skipped.
- Tool output is still compact JSON, but each list item is on its own line, so a saved
  result can be read in parts.
- `fetch_all` responses now also include a `paging` block.
- `custom_*` result fields stored as HTML are converted the same way (not truncated).

### Added
- `get_results`, `get_results_for_case`, `get_results_for_run`: `include_comments`,
  `comment_max_chars` (100–50,000), `full_comment`, `comment_format` (`markdown` / `raw`).
- `get_results`: `result_id` to return a single result (TestRail has no single-result API,
  so it is looked up within the test's results).

### Fixed
- The `get_results_for_run` description said `get_results` returns full comments; it did not.
- uv now rebuilds the project's metadata when `__init__.py` (the version source) changes.

## 0.1.1
- PyPI page refreshed with the current README. No code changes.

## 0.1.0
- First public release.
