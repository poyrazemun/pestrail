# Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Won't start: `TESTRAIL_ATTACHMENT_ROOTS is required...` | Write mode needs attachment roots. Set them, or use `TESTRAIL_READ_ONLY=true`. |
| Won't start: `Missing required environment variable(s)` | A value is missing from the `env` block. A `.env` in the working directory is never loaded (see [configuration](configuration.md#env-files)). |
| Won't start: `... entry does not exist` | A path in `TESTRAIL_ATTACHMENT_ROOTS` doesn't exist. Placeholder paths copied from `.env.example` cause this too. |
| `401 Authentication failed` | Wrong email or API key, or the API isn't enabled (**Administration > Site Settings > API**). |
| `403 Permission denied` | The user's role lacks the permission or project access. For user lookups, give the assistant your user ID instead. |
| `TLS certificate verification failed` | A company CA isn't trusted. Install it in the OS store or set `TESTRAIL_CA_BUNDLE`. |
| `does not support this API method` | Your TestRail version lacks that endpoint. |
| `... is outside TESTRAIL_ALLOWED_RUN_IDS / _PROJECT_IDS` | The write targets a run or project you didn't allow. This is the allowlist working as intended. |
| `Cannot check result ... no API to look up a single result` | Pass the result's `test_id` too (see [safety](safety.md#limiting-where-writes-can-go)). |
| Tools don't show up in Claude Desktop | Quit Claude Desktop completely and restart it. Check that the config you edited is the one opened via **Settings > Desktop app > Extensions > Manage > Edit Config**, then check the server log (below). |
| `uv sync` fails with "Access is denied" (Windows) | A running MCP client or editor extension is using the project's `.venv`. Close it and retry. |

## Logs

- **Claude Desktop:** `mcp-server-testrail.log` in its logs folder. That's
  `%APPDATA%\Claude\logs` on Windows and `~/Library/Logs/Claude` on macOS. The Microsoft
  Store version uses its own app-data folder.
- **Claude Code:** run `claude --debug`.
- For more detail, set `PESTRAIL_LOG_LEVEL=DEBUG`. Logs never contain your API key.
