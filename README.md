# pestrail

**A safety-first [MCP](https://modelcontextprotocol.io) server for TestRail.** It lets an AI
assistant such as Claude Desktop or Claude Code do the everyday TestRail work of a QA engineer:
browse projects, suites, cases, runs and plans, read tests and results, **add results** to
existing tests, and **attach files** (logs, screenshots, reports).

> **pestrail cannot delete anything.** This is a design decision, not a missing feature.

> pestrail is a community project. It is **not affiliated with, endorsed by, or supported by
> TestRail, Gurock Software or Idera, Inc.** "TestRail" is a trademark of its respective owner.

---

## What it intentionally cannot do

An AI assistant can misread a request, pick the wrong ID, or act on a hallucinated
instruction. In a test management system some mistakes can't be undone, so pestrail leaves
those operations out entirely:

| Not possible | Why |
|---|---|
| **Delete anything** (cases, runs, plans, results, attachments, sections, suites, projects...) | Deletion in TestRail is permanent and wipes history. |
| **Create, edit or move test cases, sections or suites** | Your test repository is curated by people; an assistant should not rewrite it. |
| **Close runs or plans** | Closing is irreversible in TestRail. Closed runs become read-only. |
| **Create or edit runs/plans/milestones** | Out of scope for v0.1; may be added later as opt-in tools. |

This is enforced in code, not just in prompts:

- The HTTP client only calls TestRail API methods on an explicit **allowlist**
  (`READ_METHODS` / `WRITE_METHODS` in [`client.py`](src/pestrail/client.py)). Any other
  method raises an error *before* a request is built. There are no `delete_*`, `close_*`,
  `update_*`, `move_*` or `copy_*` methods anywhere in the code.
- Unit tests fail if a destructive method is ever added to the allowlist.
- In **read-only mode** the write tools are not registered at all, and the client also
  refuses write methods as a second safeguard.

What it *can* write is purely additive: new results (TestRail keeps every result in a test's
history) and new attachments.

---

## Tools

**Read tools** (always available):

| Area | Tools |
|---|---|
| Projects & suites | `get_projects`, `get_project`, `get_suites`, `get_suite`, `get_sections` |
| Cases | `get_cases` (filters: suite, section, title text, priority, type, milestone, refs, updated dates), `get_case` |
| Metadata | `get_case_fields`, `get_result_fields`, `get_statuses`, `get_priorities`, `get_configs` |
| Milestones | `get_milestones`, `get_milestone` |
| Plans & runs | `get_plans`, `get_plan`, `get_runs`, `get_run` |
| Tests & results | `get_tests` (status filter), `get_test`, `get_results`, `get_results_for_case`, `get_results_for_run` |
| Attachments | `get_attachments_for_case`, `get_attachments_for_test`, `get_attachments_for_run`, `get_attachments_for_plan` |
| Users | `get_users`, `get_user_by_email` |

**Write tools** (not registered when `TESTRAIL_READ_ONLY=true`):

| Tool | What it does |
|---|---|
| `add_result` | Add a result to a test (by test ID). |
| `add_result_for_case` | Add a result for a case in a run (run ID + case ID). |
| `add_results_for_cases` | Bulk: add results for many cases in one run. **The main tool for reporting a test session.** Every entry is validated before anything is sent. |
| `add_attachment_to_result` / `_run` / `_case` / `_plan` / `_plan_entry` | Upload a local file as an attachment. |
| `add_result_with_attachments` | Add a result, then attach one or more files to it. Files are checked *before* the result is created. If an upload fails afterwards, the response says exactly which files succeeded and which failed (nothing is rolled back, because nothing can be deleted). |

Conveniences:

- **IDs** can be numbers or TestRail-style prefixed IDs: `C1234` (case), `R56` (run),
  `T678` (test), `P9` (plan/project), `S3` (suite), `M2` (milestone). A wrong prefix (e.g.
  `R56` as a case ID) is rejected rather than silently used.
- **Statuses** can be given by ID or name (`"Passed"`, `"failed"`, custom statuses too),
  resolved via `get_statuses` and cached. `Untested` can't be set as a result, so pestrail
  rejects it up front.
- **Custom result fields** go in `custom_fields`, e.g. `{"custom_environment": "staging"}`.
  Use `get_result_fields` to discover them.
- **Paging**: list tools take `limit`/`offset` and return a `paging` block with
  `has_more`/`next_offset`. Many also accept `fetch_all=true`, which follows TestRail's
  pagination up to a safety cap (`max_items`, default 1000). Both the paginated responses of
  TestRail 6.7+ and the bare lists of older versions are handled.
- **Compact output**: list tools return only the fields that matter, and timestamps are
  converted to ISO-8601 UTC. Use `get_case` / `get_test` / `get_run` for full details.
- **Rate limits**: on HTTP 429, pestrail waits for `Retry-After` and retries up to 3 times,
  waiting at most 30 seconds in total so the call doesn't outlast your MCP client's timeout.
  If TestRail asks for a longer wait, the tool fails right away and says how long to wait.

---

## Setup

### 1. Enable the TestRail API and get an API key

1. A TestRail **administrator** must enable the API: **Administration > Site Settings > API**
   and tick **Enable API**.
2. Sign in as the user pestrail should act as, open **My Settings > API Keys**, click
   **Add Key**, name it (e.g. "pestrail") and save. Copy the key; you'll use it as
   `TESTRAIL_API_KEY`. You can also use the account password, but a dedicated API key is
   strongly recommended because you can revoke it on its own.

### 2. Use a user with minimum permissions

pestrail acts with the permissions of the TestRail user you configure. Recommended:

- Create a **dedicated TestRail user** for the assistant, for example "QA Assistant".
- Give it a role that can only **view** and **add results**, such as the built-in *Tester*
  role or a custom role. It does not need permission to delete, to edit cases, or to manage
  runs/plans.
- Restrict it to the projects it needs.
- For pure browsing, use a read-only role **and** set `TESTRAIL_READ_ONLY=true`.

Results added through pestrail appear in TestRail as created by this user, so the audit trail
shows what the assistant did.

### 3. Install `uv`

pestrail is run with [`uv`](https://docs.astral.sh/uv/). Install it once:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then run it directly:

```bash
uvx pestrail --version
```

> Until pestrail is published on PyPI, run it from GitHub instead:
> `uvx --from git+https://github.com/poyrazemun/pestrail pestrail`
> (use the same `--from ...` arguments in the configs below).

---

## Configure Claude Desktop

Edit the Claude Desktop config file (Claude Desktop: **Settings > Developer > Edit Config**):

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

### Windows

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uvx",
      "args": ["pestrail"],
      "env": {
        "TESTRAIL_URL": "https://your-company.testrail.io",
        "TESTRAIL_USERNAME": "qa-assistant@your-company.com",
        "TESTRAIL_API_KEY": "your-api-key",
        "TESTRAIL_ATTACHMENT_ROOTS": "C:\\Users\\you\\test-artifacts;D:\\ci-logs"
      }
    }
  }
}
```

On Windows, separate multiple attachment roots with `;` and escape backslashes in JSON
(`\\`). If Claude Desktop can't find `uvx`, use its full path, which you can get in
PowerShell:

```powershell
(Get-Command uvx).Source
# e.g. C:\Users\you\.local\bin\uvx.exe -> "command": "C:\\Users\\you\\.local\\bin\\uvx.exe"
```

To open the config file from PowerShell:

```powershell
notepad "$env:APPDATA\Claude\claude_desktop_config.json"
```

### macOS / Linux

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uvx",
      "args": ["pestrail"],
      "env": {
        "TESTRAIL_URL": "https://your-company.testrail.io",
        "TESTRAIL_USERNAME": "qa-assistant@your-company.com",
        "TESTRAIL_API_KEY": "your-api-key",
        "TESTRAIL_ATTACHMENT_ROOTS": "/home/you/test-artifacts:/var/ci-logs"
      }
    }
  }
}
```

On macOS/Linux, separate attachment roots with `:`. If `uvx` is not found, use the output of
`which uvx` as `command`.

Restart Claude Desktop after editing the file. The pestrail tools then appear in the tools menu.

> The config file stores your API key in plain text. Keep it private, and revoke the key in
> TestRail (**My Settings > API Keys**) if it leaks.

---

## Configure Claude Code

```bash
# macOS / Linux
claude mcp add testrail --scope user \
  -e TESTRAIL_URL=https://your-company.testrail.io \
  -e TESTRAIL_USERNAME=qa-assistant@your-company.com \
  -e TESTRAIL_API_KEY=your-api-key \
  -- uvx pestrail
```

```powershell
# Windows (PowerShell)
claude mcp add testrail --scope user `
  -e TESTRAIL_URL=https://your-company.testrail.io `
  -e TESTRAIL_USERNAME=qa-assistant@your-company.com `
  -e TESTRAIL_API_KEY=your-api-key `
  -e "TESTRAIL_ATTACHMENT_ROOTS=C:\Users\you\test-artifacts;D:\ci-logs" `
  -- uvx pestrail
```

Add `-e TESTRAIL_READ_ONLY=true` for a browse-only setup. Check it with `claude mcp list`, or
use `/mcp` inside Claude Code.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TESTRAIL_URL` | yes | Your instance URL, e.g. `https://your-company.testrail.io`. Trailing slashes and a pasted `/index.php?/api/v2` are stripped. Must not contain credentials. Must be `https://` (plain `http://` is only accepted for `localhost` or with `TESTRAIL_ALLOW_HTTP`). |
| `TESTRAIL_USERNAME` | yes | TestRail login (email) of the user pestrail acts as. |
| `TESTRAIL_API_KEY` | yes | API key from **My Settings > API Keys**. Sent only via HTTP Basic auth; never logged, printed, returned or put in URLs. |
| `TESTRAIL_READ_ONLY` | no | `true` to register only read tools. Default `false`. |
| `TESTRAIL_ATTACHMENT_ROOTS` | write mode | Directories that attachments may be uploaded from, separated by `;` on Windows and `:` on macOS/Linux. **Required when write tools are enabled**, unless `TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH=true`. Not required in read-only mode. |
| `TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH` | no | `true` to allow uploading any readable file when `TESTRAIL_ATTACHMENT_ROOTS` is unset. **Your assistant could then exfiltrate secrets** (e.g. `~/.ssh/id_rsa`) to TestRail. Default `false`. |
| `TESTRAIL_ALLOWED_PROJECT_IDS` | no | Comma-separated project IDs (e.g. `1,P3`). When set, write tools may only target these projects. |
| `TESTRAIL_ALLOWED_RUN_IDS` | no | Comma-separated run IDs (e.g. `R56,78`). When set, write tools may only target these runs (or tests/results inside them). Case/plan uploads need `TESTRAIL_ALLOWED_PROJECT_IDS` as well if you use a run allowlist. |
| `TESTRAIL_TIMEOUT` | no | HTTP timeout in seconds. Default `30`. |
| `TESTRAIL_UPLOAD_TIMEOUT` | no | Timeout in seconds for sending an attachment and waiting for TestRail to process it. Default `300`. Raise it for very large files or slow links. |
| `TESTRAIL_CA_BUNDLE` | no | Path to a PEM file with the CA certificate(s) to trust for TestRail. Only needed if the CA is not installed in your OS certificate store (see [Corporate networks](#corporate-networks-proxies-and-tls)). |
| `TESTRAIL_ALLOW_HTTP` | no | `true` to allow a plain `http://` URL for a non-local host. **Your API key is then sent unencrypted.** Default `false`. |
| `PESTRAIL_LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` (default) or `ERROR`. Logs go to stderr only. |
| `PESTRAIL_ENV_FILE` | no | Path to a `.env` file to load, or `none` to disable `.env` loading. By default the per-user file is loaded if it exists; a `.env` in the working directory is never loaded implicitly (see [.env files](#env-files)). |

If a required variable is missing or invalid, pestrail exits immediately with a clear message
on stderr, which Claude Desktop shows in its MCP logs.

### .env files

The recommended setup is to put the variables in your MCP client config (the `env` block in
the examples above). No `.env` file is needed.

If you prefer a file, pestrail reads **one per-user `.env` file** from a fixed location:

| OS | Location |
|---|---|
| Windows | `%APPDATA%\pestrail\.env` (e.g. `C:\Users\you\AppData\Roaming\pestrail\.env`) |
| macOS / Linux | `$XDG_CONFIG_HOME/pestrail/.env`, by default `~/.config/pestrail/.env` |

```powershell
# Windows (PowerShell): create the folder and copy the template
New-Item -ItemType Directory -Force "$env:APPDATA\pestrail" | Out-Null
Copy-Item .env.example "$env:APPDATA\pestrail\.env"
notepad "$env:APPDATA\pestrail\.env"
```

```bash
# macOS / Linux
mkdir -p ~/.config/pestrail && cp .env.example ~/.config/pestrail/.env
```

A `.env` in the **working directory is never loaded automatically**. MCP clients such as
Claude Code may start pestrail inside any folder, including a repository you just cloned,
and that repository's `.env` could otherwise point pestrail at someone else's server. To
use a specific file anyway, name it explicitly with `PESTRAIL_ENV_FILE`, e.g.
`PESTRAIL_ENV_FILE=.env` when developing from source.

Whichever file is used, pestrail is strict about it:

- Real environment variables (e.g. from your MCP client config) always win over the file.
- Only `TESTRAIL_*` and `PESTRAIL_*` keys are read. Anything else, like `HTTPS_PROXY`,
  `SSL_CERT_FILE` or `PATH`, is ignored with a warning.
- If `TESTRAIL_API_KEY` comes from the real environment, the file may **not** set
  `TESTRAIL_URL`, `TESTRAIL_CA_BUNDLE` or `TESTRAIL_ALLOW_HTTP`. pestrail refuses to start
  instead, because otherwise a `.env` file could decide where your API key is sent.

To ignore `.env` files completely, set `PESTRAIL_ENV_FILE=none` in your MCP client's `env`
block, next to your other `TESTRAIL_*` variables. Setting it inside a `.env` file has no
effect.

### Corporate networks: proxies and TLS

- **TLS certificates**: pestrail uses your operating system's certificate store (via
  [truststore](https://pypi.org/project/truststore/)). If your company inspects TLS traffic
  or runs TestRail with an internal CA, it usually works as soon as that CA is installed on
  the machine, which IT normally does. Otherwise point `TESTRAIL_CA_BUNDLE` at the CA
  certificate (PEM). Certificate verification is never disabled.
- **Proxies**: the standard `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` environment variables
  are honored. Set them in the MCP client's `env` block if needed.
- **Connection problems** are reported with the underlying cause (DNS, TLS, proxy, refused
  connection) and a hint about what to check.

### Read-only mode

With `TESTRAIL_READ_ONLY=true`, the 9 write tools are **not registered**: the assistant
doesn't even see them. Use this for exploration, reporting, triage, or when you share the
setup with people who should only browse.

### Limiting where writes can go

Even with a least-privilege TestRail user, an assistant can still pick the wrong run or
project. Optionally set:

- `TESTRAIL_ALLOWED_PROJECT_IDS` — writes only in those projects
- `TESTRAIL_ALLOWED_RUN_IDS` — writes only for those runs (and tests/results inside them)

Empty means unrestricted (aside from TestRail permissions). When both are set, a
run-scoped write must satisfy both. Case/plan attachment tools are not tied to a run: if
you only set `TESTRAIL_ALLOWED_RUN_IDS`, those tools are refused unless you also set
`TESTRAIL_ALLOWED_PROJECT_IDS`.

### Restricting attachment uploads

Attachment tools read files **from the machine running pestrail**. In write mode, pestrail
**refuses to start** unless you set `TESTRAIL_ATTACHMENT_ROOTS` (or explicitly opt in with
`TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH=true`). That stops an assistant from uploading arbitrary
files such as `~/.ssh/id_rsa` by default. With roots configured, pestrail:

- resolves the requested path fully (following symlinks and `..`) **before** checking it, so
  a symlink inside an allowed directory that points outside it is rejected;
- resolves **relative paths** (e.g. `logs/run-42.log`) against the attachment roots, in
  order, never against the server's working directory (MCP clients start servers in
  unpredictable folders). Relative paths are always rejected when roots are unset;
- only accepts regular files that exist, and rejects anything over TestRail's **256 MB** limit;
- in `add_result_with_attachments`, checks all files **before** creating the result, so a bad
  path creates nothing.

---

## Example prompts

- *"List the open test runs in project P3 and show me which have failed tests."*
- *"In run R56, show me all tests that are Failed or Retest, with the latest result comment."*
- *"Add a Failed result with this log to case C1234 in run R56:
  `C:\Users\me\test-artifacts\login-2024-06-01.log`. Link defect JIRA-812 and set the version
  to 3.2.0."*
- *"Here's my test session summary. Report it to run R56: C101 passed, C102 passed, C103
  failed (button not clickable, defect UI-55), C104 blocked (env down). Elapsed 5m each."*
  The assistant should use `add_results_for_cases` for this.
- *"Attach `reports/summary.html` to test plan P9."*
- *"What custom result fields do we have, and which are required?"*
- *"Show me the history of results for case C1234 in run R56."*

Tip: ask the assistant to **show you the results it is about to add and wait for your OK**.
pestrail can't remove a result once it has been added.

---

## Run from source

```bash
git clone https://github.com/poyrazemun/pestrail
cd pestrail
uv sync
uv run pestrail --version
```

Point Claude Desktop at your checkout:

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\pestrail", "run", "pestrail"],
      "env": { "TESTRAIL_URL": "...", "TESTRAIL_USERNAME": "...", "TESTRAIL_API_KEY": "..." }
    }
  }
}
```

No `.env` file is needed with this config. For local development without an MCP client, you
can copy `.env.example` to `.env` in the repo (it is git-ignored) and opt in explicitly:

```powershell
$env:PESTRAIL_ENV_FILE = ".env"; uv run pestrail
```

### Development

```bash
uv sync                       # installs runtime + dev dependencies
uv run pytest                 # unit tests (HTTP fully mocked with respx) + stdio smoke test
uv run ruff check .           # lint
uv run ruff format .          # format
```

The tests never contact a real TestRail instance: all HTTP is mocked, and the stdio smoke
test uses a reserved `.invalid` hostname with dummy credentials. To inspect the server
interactively, use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector uv run pestrail
```

### Contributing

Contributions are welcome. Please keep the safety model intact: PRs that add delete,
close or case-editing capabilities will not be accepted. New write tools must only *add*
data, must be added to `WRITE_METHODS`, and must be hidden in read-only mode.

## License

[MIT](LICENSE)
