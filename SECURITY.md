# Security Policy

codex-bg is an early experimental project. It runs AI-generated workflows around
GitHub repositories and can post comments or edit issue metadata when configured
for real runs. Treat it as unsafe until you have reviewed the code, tested it in
dry-run mode, and constrained its credentials.

There are no guarantees of correctness, availability, safety, or fitness for
production use. Use it at your own risk.

## Reporting Security Issues

Please do not file public issues for vulnerabilities or leaked credentials.
Report security concerns through the GitHub private vulnerability reporting
feature if it is enabled for this repository, or contact the maintainers through
the normal rsyslog project security channel.

Feedback is welcome, especially around prompt-injection risks, credential
scoping, unsafe defaults, and operational failure modes.

## Operational Guidance

- Start with `dry_run = true`.
- Use a dedicated GitHub account or token with the smallest practical
  repository permissions.
- Do not run with broad organization-wide credentials.
- Keep debug logs private; they can contain issue contents, prompts, model
  replies, local paths, and operational decisions.
- Keep SQLite databases, workdirs, artifacts, and real scheduler configs out of
  source control.
- Review AI-generated issue comments before enabling write behavior on important
  repositories.
- Treat shared repository checkouts as read-only context. Code changes should
  be made only in separate worktrees.
