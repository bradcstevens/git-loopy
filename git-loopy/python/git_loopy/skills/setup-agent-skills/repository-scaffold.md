# GitHub-Ready Repository Baseline

Apply this baseline additively in `PROJECT_ROOT`. The manifest below is the single
source of truth for what this skill creates.

## Repository initialization

Run `git -C "$PROJECT_ROOT" rev-parse --show-toplevel`. If it fails, initialize the
target with:

```bash
git -C "$PROJECT_ROOT" init -b main
```

When `PROJECT_ROOT` is already inside a worktree, use that worktree rather than
creating a nested repository. Remote publication remains a separate,
project-specific decision.

## Additive write rule

Create every missing parent directory and file in the manifest. When a path already
exists, preserve it as the project's replacement for the generic baseline. Derive
`PROJECT_NAME` from the target directory name for a new `README.md`.

Create policy- and stack-specific files only when the project supplies the missing
facts. This includes `LICENSE`, `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`,
`.github/FUNDING.yml`, Dependabot configuration, and CI workflows.

## Manifest

- `.gitignore`
- `.gitattributes`
- `.editorconfig`
- `README.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/pull_request_template.md`

## File contents

### `.gitignore`

```gitignore
# Operating systems
.DS_Store
Thumbs.db
Desktop.ini

# Editors
.idea/
.vscode/
*.swp
*.swo
*~

# Environment and secrets
.env
.env.*
!.env.example

# Logs
*.log
logs/

# Dependencies and virtual environments
node_modules/
.venv/
venv/

# Build and coverage output
build/
dist/
out/
target/
coverage/
htmlcov/

# Language and tool caches
__pycache__/
*.py[cod]
.cache/
.mypy_cache/
.pytest_cache/
.ruff_cache/

# Temporary files
.tmp/
tmp/
temp/
```

### `.gitattributes`

```gitattributes
* text=auto

*.sh text eol=lf
*.bash text eol=lf
*.bat text eol=crlf
*.cmd text eol=crlf

*.gif binary
*.ico binary
*.jpg binary
*.jpeg binary
*.pdf binary
*.png binary
*.webp binary
```

### `.editorconfig`

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.md]
trim_trailing_whitespace = false
```

### `README.md`

Replace `<PROJECT_NAME>` with the target directory name.

```markdown
# <PROJECT_NAME>

<!-- setup-agent-skills:overview:start -->
## Overview

Project documentation is ready for an overview.
<!-- setup-agent-skills:overview:end -->

## Development

Document prerequisites, setup, and validation commands here.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md).
```

### `CONTRIBUTING.md`

```markdown
# Contributing

## Before starting

- Check existing issues and pull requests for related work.
- Open or reference an issue for substantial changes.
- Keep each change focused on one outcome.

## Making changes

- Follow the repository instructions in `AGENTS.md` or `CLAUDE.md`.
- Add or update tests and documentation with behavior changes.
- Run the repository's relevant validation commands before opening a pull request.

## Pull requests

- Explain the problem and the chosen solution.
- Link related issues.
- Include the validation performed.
- Call out follow-up work or known limitations.
```

### `SECURITY.md`

```markdown
# Security Policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting or a private security advisory for
this repository. If neither is available, contact the maintainers through a private
channel listed on the repository owner's profile.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Keep vulnerability details private until the maintainers coordinate a
fix and disclosure.
```

### `.github/ISSUE_TEMPLATE/bug_report.yml`

```yaml
name: Bug report
description: Report a reproducible problem
title: "[Bug]: "
labels: []
assignees: []
body:
  - type: markdown
    attributes:
      value: Thanks for helping improve the project.
  - type: textarea
    id: description
    attributes:
      label: Description
      description: What happened, and what did you expect?
    validations:
      required: true
  - type: textarea
    id: reproduction
    attributes:
      label: Reproduction
      description: Provide the smallest reliable set of steps.
      placeholder: |
        1. ...
        2. ...
        3. ...
    validations:
      required: true
  - type: textarea
    id: environment
    attributes:
      label: Environment
      description: Include relevant versions, operating system, and configuration.
    validations:
      required: true
  - type: textarea
    id: context
    attributes:
      label: Additional context
      description: Add logs, screenshots, or other useful details.
```

### `.github/ISSUE_TEMPLATE/feature_request.yml`

```yaml
name: Feature request
description: Propose an improvement or new capability
title: "[Feature]: "
labels: []
assignees: []
body:
  - type: textarea
    id: problem
    attributes:
      label: Problem
      description: What need or limitation should this address?
    validations:
      required: true
  - type: textarea
    id: outcome
    attributes:
      label: Desired outcome
      description: Describe the result rather than prescribing implementation.
    validations:
      required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: What workarounds or other approaches have you tried?
  - type: textarea
    id: context
    attributes:
      label: Additional context
      description: Add examples, constraints, or supporting material.
```

### `.github/ISSUE_TEMPLATE/config.yml`

```yaml
blank_issues_enabled: true
contact_links: []
```

### `.github/pull_request_template.md`

```markdown
## Summary

Describe the problem and the solution.

## Related issues

Link the issue or explain why one is not needed.

## Validation

List the checks performed.

## Checklist

- [ ] The change is focused and documented.
- [ ] Relevant tests or checks pass.
- [ ] User-facing behavior changes are reflected in documentation.
```

The baseline is complete when every manifest path is present and no pre-existing
file was overwritten.
