# AGENTS.md

## Commit Discipline

- Keep commits focused on one coherent change.
- Use imperative, descriptive commit subjects, for example `Add per-plugin scheduling state`.
- Prefer subjects under 72 characters.
- Include a body when the commit changes behavior, schema, safety policy, or operator workflow.
- Mention tests or validation in the body when useful.
- Do not mix harness/runtime cleanup with unrelated feature work unless they are required for the same behavior.
