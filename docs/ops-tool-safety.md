# Host tool safety fixes

The branch fixes two host-tool defects before Thor deployment. It does not change
the inference engine, prompts, running services, or fault-injection cases.

`restore_config(source="previous")` now reads the rollback source before rotating
`previous.conf`. Previously that rotation overwrote the source and the operation
silently kept the broken configuration. The existing active file is written in
place because nginx bind-mounts this individual file; replacing its inode could
leave an existing container reading the old configuration.

`docker_exec` now validates exact diagnostic program names and calls Docker with
an argument array, without `sh -c`. Existing `nginx -t/-T`, file inspection,
`df`/`du`, process listing, and quoted grep patterns remain available. `env` may
only print its environment, and `tail` follow mode is rejected.

Read-only network diagnostics also allow `ip` address, route, link, and neighbor
show/list forms with at most one device name; `ss` with only the documented
status option letters; network-only `getent`; and one-name `nslookup`, `dig`, or
`host` lookups. Mutating forms, filters, alternate DNS servers, and lookup
options remain denied.

Container `curl` diagnostics accept one GET/HEAD request to localhost, the two
stack container names, or a private IP literal. Curl configuration files, URL
globbing, environment proxies, and redirects are disabled. Options for writes,
uploads, request bodies, alternate targets, and arbitrary headers are rejected.
Use `http_check` for other permitted host-side diagnostics; mutations must use
the action tools and their existing guards. This restricts HTTP methods; the
application itself must still implement GET/HEAD without side effects.

Run the targeted regression tests from the repository root:

```bash
python -B -m unittest tests.agent.test_host_safety -v
```

Tests mock command execution and config files. They do not call Docker, connect
to monitored services, inject faults, or delete files. The host-dispatch tests
require the project's OpenAI dependency; policy and rollback tests use only the
standard library.

Operational API authentication, per-incident isolation, the host-side
`http_check` redirect policy, and Thor runtime validation remain separate work.
No live service restart or fault drill is required to review this branch.

---

## Earlier note (PR #4): Rollback fix with the existing recovery interface

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
