# Changelog

## 0.3.0 (2026-11-20)

Smaller, stricter and easier to ask: "my tests" in one call, readable test cases, about 28%
fewer tokens spent on tool definitions, and releases published without an API token.

### Added

- `get_current_user` tool, and an `assigned_to` filter on `get_tests` (user IDs, `"me"`,
  `"unassigned"`): "my failed tests in R650" is one call. The result is correct even on
  TestRail versions that ignore the assignee parameter.
- `get_case` / `get_test`: `text_format` (`markdown` / `raw`).

### Changed

- `get_case` / `get_test` return rich-text fields (description, steps, expected results,
  preconditions, separated steps) as readable Markdown and leave out empty fields. A real
  50,752-character case is now 2,276.
- Result fields that are empty (`null`, `""`, `[]`, e.g. `elapsed`, `defects`, `version`,
  `attachment_ids`) are left out, making results about a third smaller: 250 results
  without comments now fit in one response (was 185).
- Tool definitions are about 28% smaller (about 3.5k fewer tokens per conversation in
  read-write mode), with the same parameters and validation.

### Fixed

- Unknown tool arguments are rejected with a clear error ("did you mean 'status'?")
  instead of being silently ignored. Before, `get_tests(status_id=...)` returned every
  test, and `add_result(status_id=5)` added a result without a status.
- When a results page is cut to the size budget, the note no longer suggests
  `include_comments=false` if comments are already off, and the hint no longer suggests
  `fetch_all=true` (it has the same budget).

### Security

- A single case or test larger than the output budget has its longest text fields cut
  (with a truncation marker) instead of being returned whole.
- Releases are built, tested and published by GitHub Actions with PyPI Trusted Publishing
  (no API token) and signed attestations; see SECURITY.md.

### Internal

- Type checking with mypy (strict) in CI and before every release.

## 0.2.0 (2026-11-17)

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

### Security

- HTML conversion is bounded against hostile comments: nested links and whitespace in
  table cells no longer cause quadratic slowdowns, list indentation is capped so nesting
  can't multiply the output, and at most 500,000 characters of HTML are converted.
- `get_user_by_email` checks the length before the email regex, which was quadratic on
  long input (a 64 KB string blocked the server for seconds); the parameter is capped at
  254 characters.
- Attachments are read with a hard 256 MB limit, so a file that grows after validation
  (e.g. a live log) can't be loaded into memory past TestRail's limit.

### Fixed

- The `get_results_for_run` description said `get_results` returns full comments; it did not.
- uv now rebuilds the project's metadata when `__init__.py` (the version source) changes.
- Very long numbers or Unicode digits in IDs, dates, statuses or a `Content-Length` header
  now give a clear error instead of an internal `ValueError`; error messages no longer echo
  huge inputs back.
- `get_results` with `result_id` says when the result wasn't among the newest 1,000 instead
  of reporting it as not found, and stops fetching as soon as the result is found.
- `fetch_all` stops requesting pages once the output budget is full, instead of fetching up
  to `max_items` and discarding most of it.

### Internal

- Removed unused code (`to_compact_json`, `truncate_comment`, a redundant wrapper).
- Source files are ASCII-only (escapes for invisible characters); CI actions are pinned to
  commit SHAs.

## 0.1.1 (2026-11-12)

- PyPI page refreshed with the current README. No code changes.

## 0.1.0 (2026-11-12)

- First public release.
