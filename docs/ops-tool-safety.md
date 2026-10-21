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
