# nginx fails to start: configuration error
Symptoms: container `edge-victim` exits immediately after start; logs show `nginx: [emerg] ... in /etc/nginx/conf.d/default.conf:<line>`
(unexpected token, unknown directive, missing semicolon).
Ignore the entrypoint's `can not modify /etc/nginx/conf.d/default.conf (read-only file system?)` info line: the
config is a host file mounted read-only into the container, and `ops__write_config` changes it on the host.
Rebuilding the container (`recreate_stack`) does not touch that file and does not help.
Steps: 1) `docker logs edge-victim --tail 20` and read the [emerg] line and line number. 2) Read the config file at
`victim/conf.d/default.conf`. 3) Fix the syntax at that line (each directive ends with `;`, braces balanced), write it
back. 4) `docker restart edge-victim`. 5) Verify the health URL returns 200 and `/api/` still proxies.
