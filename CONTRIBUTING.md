# Contributing

Contributions are welcome.

## Development setup

```bash
git clone https://github.com/poyrazemun/pestrail
cd pestrail
uv sync                # runtime + dev dependencies
uv run pytest          # unit tests (HTTP fully mocked) + stdio smoke tests
uv run ruff check .    # lint
uv run ruff format .   # format
```

The tests never contact a real TestRail instance. All HTTP is mocked with
[respx](https://lundberg.github.io/respx/), and the stdio smoke tests use a reserved
`.invalid` hostname with dummy credentials. Please keep it that way, and never commit real
URLs or credentials.

To try your checkout:

- With an MCP client: point it at
  `uv --directory /path/to/pestrail run pestrail` (see [configuration](docs/configuration.md)).
- Interactively: run `npx @modelcontextprotocol/inspector uv run pestrail`.

## Ground rules

pestrail's value is its safety model ([docs/safety.md](docs/safety.md)), so:

- PRs that add delete, close, or case/suite/section editing capabilities won't be accepted.
- New write tools must only *add* data. They must be listed in `WRITE_METHODS`, go through
  the write-scope check (`scope.py`), and stay hidden in read-only mode.
- New API methods must exist in the real TestRail API. Test fixtures should mirror real
  response shapes.
- Add tests for every behavior change, and make sure `pytest`, `ruff check` and
  `ruff format --check` pass.
