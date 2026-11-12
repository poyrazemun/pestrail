# Configuration

All settings are environment variables, normally set in your MCP client's `env` block.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TESTRAIL_URL` | yes | Instance URL, e.g. `https://your-company.testrail.io`. A trailing `/` or a pasted `/index.php?/api/v2` is stripped. Must not contain credentials. Must be `https://` (`http://` only for `localhost` or with `TESTRAIL_ALLOW_HTTP`). |
| `TESTRAIL_USERNAME` | yes | Email of the TestRail user pestrail acts as. |
| `TESTRAIL_API_KEY` | yes | API key from **My Settings > API Keys**. Only ever sent via HTTP Basic auth. pestrail never logs, prints, returns or puts it in URLs. |
| `TESTRAIL_READ_ONLY` | no | `true` registers only the read tools. Default `false`. |
| `TESTRAIL_ATTACHMENT_ROOTS` | write mode | Folders attachments may be uploaded from (`;`-separated on Windows, `:` on macOS/Linux). Required in write mode unless `TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH=true`. |
| `TESTRAIL_ALLOW_ANY_ATTACHMENT_PATH` | no | `true` allows uploading any readable file when no roots are set. **The assistant could then upload secrets** such as `~/.ssh/id_rsa`. Default `false`. |
| `TESTRAIL_ALLOWED_PROJECT_IDS` | no | Comma-separated project IDs (e.g. `1,P3`). Writes may only target these projects. |
| `TESTRAIL_ALLOWED_RUN_IDS` | no | Comma-separated run IDs (e.g. `R56,78`). Writes may only target these runs. |
| `TESTRAIL_TIMEOUT` | no | HTTP timeout in seconds. Default `30`, max `600`. |
| `TESTRAIL_UPLOAD_TIMEOUT` | no | Timeout for attachment uploads. Default `300`, max `3600`. |
| `TESTRAIL_CA_BUNDLE` | no | PEM file with CA certificates to trust (see [Corporate networks](#corporate-networks-proxies-and-tls)). |
| `TESTRAIL_ALLOW_HTTP` | no | `true` allows plain `http://` for a non-local host. **Your API key is then sent unencrypted.** |
| `PESTRAIL_LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` (default) or `ERROR`. Logs go to stderr only. |
| `PESTRAIL_ENV_FILE` | no | Path to a `.env` file to load, or `none` to disable `.env` loading. |

If a required value is missing or invalid, pestrail exits right away with a clear message in
your MCP client's server log.

## Client examples

### Claude Desktop with write tools

Open the config with **Settings > Desktop app > Extensions > Manage > Edit Config**, because its location differs
between installers. The Microsoft Store version keeps it in its own app-data folder.

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/poyrazemun/pestrail", "pestrail"],
      "env": {
        "TESTRAIL_URL": "https://your-company.testrail.io",
        "TESTRAIL_USERNAME": "qa-assistant@your-company.com",
        "TESTRAIL_API_KEY": "your-api-key",
        "TESTRAIL_ATTACHMENT_ROOTS": "C:\\Users\\you\\test-artifacts;D:\\ci-logs",
        "TESTRAIL_ALLOWED_PROJECT_IDS": "3",
        "PESTRAIL_ENV_FILE": "none"
      }
    }
  }
}
```

- In JSON, backslashes must be doubled (`\\`). On macOS/Linux, use e.g.
  `"/Users/you/test-artifacts:/var/ci-logs"`.
- If `uvx` isn't found, use its full path as `command`. Find it with
  `(Get-Command uvx).Source` in PowerShell or `which uvx` on macOS/Linux.
- The config file stores your API key in plain text. If it leaks, revoke the key in
  TestRail.

### Claude Code (PowerShell)

```powershell
claude mcp add testrail --scope user `
  -e TESTRAIL_URL=https://your-company.testrail.io `
  -e TESTRAIL_USERNAME=qa-assistant@your-company.com `
  -e TESTRAIL_API_KEY=your-api-key `
  -e "TESTRAIL_ATTACHMENT_ROOTS=C:\Users\you\test-artifacts;D:\ci-logs" `
  -e PESTRAIL_ENV_FILE=none `
  -- uvx --from git+https://github.com/poyrazemun/pestrail pestrail
```

Check it with `claude mcp list`, or `/mcp` in a new session. Claude Code stores the values in
`~/.claude.json`.

### From a local checkout

```json
"command": "uv",
"args": ["--directory", "C:\\path\\to\\pestrail", "run", "pestrail"]
```

## .env files

Putting everything in the MCP client config is recommended, and then no `.env` file is
needed. If you prefer a file, pestrail reads **one per-user file** from a fixed location:

| OS | Location |
|---|---|
| Windows | `%APPDATA%\pestrail\.env` |
| macOS / Linux | `$XDG_CONFIG_HOME/pestrail/.env`, by default `~/.config/pestrail/.env` |

Copy [`.env.example`](../.env.example) there and fill it in. Rules:

- A `.env` in the **working directory is never loaded automatically**. Otherwise a cloned
  repository's `.env` could point pestrail at someone else's server. To use a specific file,
  name it in `PESTRAIL_ENV_FILE`.
- Real environment variables always win over the file.
- Only `TESTRAIL_*` and `PESTRAIL_*` keys are read. Anything else (`HTTPS_PROXY`, `PATH`...)
  is ignored.
- If the API key comes from the real environment, the file may not set `TESTRAIL_URL`,
  `TESTRAIL_CA_BUNDLE` or `TESTRAIL_ALLOW_HTTP`, so a file can never decide where your key
  is sent.
- `PESTRAIL_ENV_FILE=none` in the MCP client's `env` block disables `.env` loading. Setting
  it inside a `.env` file has no effect.

## Corporate networks: proxies and TLS

- **TLS:** pestrail uses the operating system's certificate store (via
  [truststore](https://pypi.org/project/truststore/)), so a company CA that IT installed
  works without setup. Otherwise set `TESTRAIL_CA_BUNDLE` to the CA's PEM file.
  Certificate verification is never disabled.
- **Proxies:** `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` are honored. Set them in the MCP
  client's `env` block.
