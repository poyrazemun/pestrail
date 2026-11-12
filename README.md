# pestrail

**A safety-first [MCP](https://modelcontextprotocol.io) server for TestRail.** Let Claude
Desktop, Claude Code or any MCP client do everyday QA work in TestRail: browse projects,
cases, runs and plans, read tests and results, **add results** and **attach files** (logs,
screenshots, reports).

**pestrail cannot delete anything**, cannot create, edit or move test cases, suites or
sections, and cannot close runs or plans. These operations are missing by design and
blocked in code, not just in prompts. Everything it writes is additive: new results and new
attachments. See [docs/safety.md](https://github.com/poyrazemun/pestrail/blob/main/docs/safety.md).

> Community project, **not affiliated with or endorsed by TestRail, Gurock Software or
> Idera, Inc.** "TestRail" is a trademark of its respective owner.

## Tools

- **Read (29):** projects, suites, sections, cases, case/result fields, statuses, priorities,
  configs, milestones, plans, runs, tests, results, attachment lists, users.
- **Write (9, hidden in read-only mode):** `add_result`, `add_result_for_case`,
  `add_results_for_cases` (report a whole test session in one call),
  `add_result_with_attachments`, and `add_attachment_to_result` / `_run` / `_case` /
  `_plan` / `_plan_entry`.

IDs can be plain numbers or TestRail-style (`C1234`, `R56`, `T678`), and statuses can be
given by name (`"Failed"`, custom statuses too).

## Quick start

1. **Get an API key.** A TestRail admin enables the API once (**Administration > Site
   Settings > API**). Then go to **My Settings > API Keys > Add Key**. Prefer a dedicated
   user with a role that can only view and add results.
2. **Install [uv](https://docs.astral.sh/uv/getting-started/installation/).** pestrail isn't
   on PyPI yet, so it runs from GitHub with `uvx --from git+https://github.com/poyrazemun/pestrail pestrail`.
3. **Add it to your MCP client.** Start read-only and enable writes later.

**Claude Desktop:** open **Settings > Desktop app > Extensions > Manage > Edit Config**, add the block below, then
quit Claude Desktop completely and start it again.

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/poyrazemun/pestrail", "pestrail"],
      "env": {
        "TESTRAIL_URL": "https://your-company.testrail.io",
        "TESTRAIL_USERNAME": "you@your-company.com",
        "TESTRAIL_API_KEY": "your-api-key",
        "TESTRAIL_READ_ONLY": "true",
        "PESTRAIL_ENV_FILE": "none"
      }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add testrail --scope user \
  -e TESTRAIL_URL=https://your-company.testrail.io \
  -e TESTRAIL_USERNAME=you@your-company.com \
  -e TESTRAIL_API_KEY=your-api-key \
  -e TESTRAIL_READ_ONLY=true \
  -e PESTRAIL_ENV_FILE=none \
  -- uvx --from git+https://github.com/poyrazemun/pestrail pestrail
```

(In PowerShell, end lines with a backtick `` ` `` instead of `\`.)

**Enable writes:** remove `TESTRAIL_READ_ONLY` and set `TESTRAIL_ATTACHMENT_ROOTS` to the
folders holding your test artifacts (`;`-separated on Windows, `:` on macOS/Linux). Write
mode won't start without it. Optionally limit writes with `TESTRAIL_ALLOWED_PROJECT_IDS` /
`TESTRAIL_ALLOWED_RUN_IDS`.

## Key settings

| Variable | Purpose |
|---|---|
| `TESTRAIL_URL`, `TESTRAIL_USERNAME`, `TESTRAIL_API_KEY` | Required. `https://` only. |
| `TESTRAIL_READ_ONLY` | `true` registers only the read tools. |
| `TESTRAIL_ATTACHMENT_ROOTS` | Folders attachments may be uploaded from. Required in write mode. |
| `TESTRAIL_ALLOWED_PROJECT_IDS` / `TESTRAIL_ALLOWED_RUN_IDS` | Optional: confine writes to these projects/runs. |

The full reference covers timeouts, `.env` files, proxies and corporate TLS:
[docs/configuration.md](https://github.com/poyrazemun/pestrail/blob/main/docs/configuration.md).

## Example prompts

- *"In run R56, which tests are assigned to me? My TestRail email is me@your-company.com."*
- *"Show me all Failed or Retest tests in run R56 with their latest comment."*
- *"Report my session to run R56: C101 passed, C102 failed (defect UI-55), C103 blocked."*
- *"Add a Failed result to C1234 in R56 and attach `C:\artifacts\login.log`."*

Tip: ask the assistant to show you what it will add and wait for your OK. pestrail can't
remove a result once it's added.

## More

- [Configuration](https://github.com/poyrazemun/pestrail/blob/main/docs/configuration.md): all settings, `.env` files, proxies/TLS, more
  client examples
- [Safety model and limits](https://github.com/poyrazemun/pestrail/blob/main/docs/safety.md)
- [Troubleshooting](https://github.com/poyrazemun/pestrail/blob/main/docs/troubleshooting.md)
- [Contributing](https://github.com/poyrazemun/pestrail/blob/main/CONTRIBUTING.md) · [Security policy](https://github.com/poyrazemun/pestrail/blob/main/SECURITY.md)

## License

[MIT](https://github.com/poyrazemun/pestrail/blob/main/LICENSE)
