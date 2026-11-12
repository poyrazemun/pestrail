# Safety model

An AI assistant can misread a request, pick the wrong ID, or follow instructions hidden in
text it reads from TestRail. pestrail layers independent safeguards, so a single mistake or
misconfiguration doesn't cause damage.

| Layer | What it does |
|---|---|
| **API method allowlist** | The HTTP client only calls TestRail methods listed in `READ_METHODS` / `WRITE_METHODS` ([client.py](../src/pestrail/client.py)). Any other method raises before a request is built. Tests fail if a `delete_*`, `close_*`, `update_*`, `move_*` or `copy_*` method is ever added. |
| **Additive writes only** | The only writes are new results and new attachments. Nothing is deleted, edited or closed. |
| **Read-only mode** | `TESTRAIL_READ_ONLY=true` doesn't register write tools at all, and the client also refuses write methods. |
| **Attachment roots** | Write mode refuses to start without `TESTRAIL_ATTACHMENT_ROOTS`, so files can only come from folders you chose. |
| **Write allowlists** | Optional `TESTRAIL_ALLOWED_PROJECT_IDS` / `TESTRAIL_ALLOWED_RUN_IDS` confine writes. |
| **Input limits** | Sizes are capped (see [Limits](#limits)). Bulk calls validate every entry before sending anything. |
| **Secrets** | The API key is never logged, returned or put in URLs, and is redacted from error messages. |
| **Safe configuration** | No implicit `.env` from the working directory, plain `http://` refused, OS trust store for TLS. |

## Limiting where writes can go

With `TESTRAIL_ALLOWED_PROJECT_IDS` and/or `TESTRAIL_ALLOWED_RUN_IDS` set, pestrail traces
every write target to its run and project using read calls before writing. When both are
set, a write must satisfy both.

- **Case and plan attachments** aren't tied to a run. With only a run allowlist they're
  refused; add a project allowlist to allow them.
- **`add_attachment_to_result`:** TestRail has no API to look up a single result.
  - Results created by pestrail in the current session are known automatically.
  - For any other result, pass its `test_id`. pestrail checks the result belongs to that
    test before checking the run.

## Attachment uploads

Files are read from the machine running pestrail. pestrail:

- resolves the path fully (symlinks and `..`) **before** checking it against the roots;
- resolves relative paths against the roots, never against the server's working directory;
- accepts only existing regular files up to TestRail's 256 MB limit;
- in `add_result_with_attachments`, checks every file **before** creating the result, so a
  bad path creates nothing. If an upload fails afterwards, the response lists exactly what
  succeeded; nothing is rolled back, because nothing can be deleted.

## Limits

| Limit | Value |
|---|---|
| Result comment | 20,000 characters |
| Version / elapsed / defects | 250 / 64 / 2,000 characters |
| Custom fields per result | 50 fields, 10,000 characters per value |
| Results per bulk call | 500 |
| Files per `add_result_with_attachments` | 20 |
| Attachment size | 256 MB |
| `fetch_all` | 1,000 items by default, `max_items` up to 5,000 |
| TestRail response size | 20 MB (refused while streaming, never fully loaded) |
| Rate limiting (HTTP 429) | Follows `Retry-After`: up to 3 retries and 30 s in total. A longer requested wait fails right away and says how long. |

## Known limitations

These follow from the design and are worth knowing before you enable writes:

- **Prompt injection is reduced, not removed.** Text stored in TestRail (case titles, steps,
  comments) reaches the assistant and can try to steer it. pestrail guarantees the assistant
  can't delete, edit or close anything, or write outside your allowlists and attachment
  roots. It can't stop a wrong result being added *inside* what you allowed. Keep your MCP
  client's approval prompts on for write tools.
- **Read tools expose what the TestRail user can see** to the assistant, including user
  names and email addresses. Limit the user's project access accordingly.
- **Added results and attachments can't be removed through pestrail.** Remove mistakes in
  TestRail itself.
- **Uploads are read fully into memory** (up to 256 MB per file) before sending.

## Recommended TestRail setup

- Use a dedicated TestRail user whose role can only view and add results, limited to the
  projects it needs. Results it adds show that user as the author, which gives you an audit
  trail.
- Start with `TESTRAIL_READ_ONLY=true`.
- Ask the assistant to show what it will add and wait for your OK before writing.
