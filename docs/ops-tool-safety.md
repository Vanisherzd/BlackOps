# Rollback fix with the existing recovery interface

Recovery compatibility takes priority on this branch. The additional diagnostic
restrictions introduced in d51733a have been withdrawn. `docker_exec`, its tool
description, shell wrapper, and the ops skill are restored to their original
14a8f3e behavior. Existing curl redirects, headers, POST, file output, config
options, environment wrappers, custom nginx test options and tail commands are
not newly blocked. The original container allowlist, shell-character checks,
runbook discipline and action reviewer remain as before. The rollback fix does
not alter inference or Thor launch settings; existing main updates are preserved
during integration.

The functional fix retained is `restore_config(source="previous")`: read the
rollback source before rotating `previous.conf`. Previously rotation overwrote
the source, silently keeping the broken configuration. The existing active file
is written in place because nginx bind-mounts that file; replacing its inode
could leave a running container reading the old configuration.

Run the targeted tests from the repository root:

```bash
python -B -m unittest tests.agent.test_host_safety -v
```

Tests use mocked diagnostics and in-memory files. An inert SDK import rejects
accidental LLM calls if OpenAI is not installed. They never call Docker, send
HTTP requests, inject faults, or delete files. They cover rollback, failure
handling, dry-run behavior, and representative existing command forms.

The diagnosis and repair implementation was compared against 14a8f3e: after
removing the rollback helper import and substituting the original two rollback
lines, scripts/agent.py is identical to that baseline. The ops skill is identical
as well. No diagnostic restrictions, schemas, prompts or other repair actions
changed relative to that baseline.

Mock tests and source comparison do not measure end-to-end repair success.
There is no new fault-drill result for this correction. Any future diagnostic
hardening must be evaluated on the original fault cases before being adopted.
API authentication, incident isolation, and Thor validation remain separate work.
