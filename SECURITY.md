# Security policy

## Reporting a vulnerability

Please **don't** report security issues in public GitHub issues. Use GitHub's private
reporting instead: **Security > Report a vulnerability** on this repository. Include steps
to reproduce and the affected version or commit.

You'll get an acknowledgement as soon as possible. Fixes are released before the details are
made public.

## Scope

The most relevant areas are anything that could let an assistant or a crafted TestRail
response:

- call TestRail API methods outside the allowlist, or delete, edit or close data;
- upload local files from outside `TESTRAIL_ATTACHMENT_ROOTS`;
- bypass `TESTRAIL_READ_ONLY` or the project/run write allowlists;
- leak the API key (logs, error messages, URLs, tool output);
- make pestrail send credentials to an unintended host.

The design is described in [docs/safety.md](docs/safety.md).
