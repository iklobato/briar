# `briar dashboard`

## What it does

`briar dashboard` starts a small web page that shows the health of a
machine that runs briar. The page is read-only: you can look, not
change anything. It is built again on every page load.

The page shows:

| Card | Where the data comes from |
|---|---|
| System | Host name, uptime, disk use for `--disk-path`, load, memory, CPU count. Uptime, load and memory come from `/proc`, so they show as unknown or 0 on macOS. |
| Deploy | The git commit, branch, author and `origin` URL of `--repo-path`. A token in the URL is removed. |
| Scheduler process | Whether a `briar runbook serve` process is running (found with `pgrep`). |
| Schedules | Every company and task in the runbook files in `--examples`, with its `every:` value, its extractors and a next run time. That time leaves out the small offset `runbook serve` adds (see [runbook.md](runbook.md)). A file that fails to load shows as `(parse error)`. |
| Recent cycles, log tail, GitHub quota | Lines read from the log file at `--log-file`. |
| Connectivity | A TCP connect test to `api.github.com`, `github.com` and `sts.amazonaws.com` on port 443 (2 second limit). |
| Dashboard | The page's own process ID, uptime and request count. |

It also answers `/healthz` with `ok`, for uptime checks. Every other
path returns 404.

## Use it when

- You run `briar runbook serve` on a server and want a quick "is it healthy?" view.
- You want to see when each runbook task will run next.
- You need a one-time status page to attach to an incident note.

## Examples

```bash
# Start the page on this machine only, at http://127.0.0.1:8080
briar dashboard
```

You get a web server on port 8080. Open `http://127.0.0.1:8080` in your browser. Stop it with Ctrl-C.

```bash
# Show the schedules from your runbooks folder.
briar dashboard --examples runbooks/
```

You get the Schedules card filled from `runbooks/*.yaml`.

```bash
# Use another port, and read the scheduler log from a custom path.
briar dashboard --port 9090 --log-file /var/log/briar/serve.log
```

You get the page on port 9090, with the log cards read from `/var/log/briar/serve.log`.

```bash
# Write the page once to a file, with no server.
briar dashboard --once --examples runbooks/ > status.html
```

You get a static `status.html` file. The command ends right away.

```bash
# Check that a running dashboard is up.
curl http://127.0.0.1:8080/healthz
```

You get `ok`.

## All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--host` | The address to listen on. `127.0.0.1` means only this machine can open the page. `--host 0.0.0.0` opens it to the network, so check your firewall first: the page has no login. | `127.0.0.1` | `--host 0.0.0.0` |
| `--port` | The port to listen on. | `8080` | `--port 9090` |
| `--examples` | The folder with your runbook YAML files, for the Schedules card. | `./examples` | `--examples runbooks/` |
| `--log-file` | The scheduler log file to read for the log, cycles and GitHub quota cards. | `/var/log/briar/scheduler.log` | `--log-file /var/log/briar/serve.log` |
| `--disk-path` | The folder whose disk use the System card shows. | `/` | `--disk-path /var/lib/briar` |
| `--repo-path` | The git checkout the Deploy card reads. | `.` (the current folder) | `--repo-path /opt/briar` |
| `--once` | Print the page HTML to stdout once and exit, with no server. `--host` and `--port` are not used. | off | `--once` |

## Good to know

- `briar runbook serve` writes its log to stderr, not to a file. To
  fill the log cards, send that output to the file you pass in
  `--log-file`, for example with `>> /var/log/briar/scheduler.log 2>&1`.
- A missing log file is not an error. The log cards are just empty.
- If one card fails, briar logs the error and shows that card empty.
  The rest of the page still works. Run with `--verbose` to see why.
- The Recent cycles card looks for an older log line format. It may
  stay empty with logs from `runbook serve`.
- `address already in use` means another program uses that port. Pick
  another with `--port`.
- The page has no login. Keep the default `127.0.0.1`, or put a proxy
  with a login in front of it.

## Run it with Docker

Inside a container, listen on `0.0.0.0` and publish the port. Publish
it on `127.0.0.1` so only your machine can reach it.

```bash
docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD":/work -w /work \
    iklob1/briar dashboard --host 0.0.0.0 --examples runbooks/
```

You get the page at `http://127.0.0.1:8080`, with the schedules from the `runbooks/` folder in your current folder.
Since 1.1.75 the image has `git`, so the Deploy card can show the last
commit of the mounted folder. In 1.1.75 to 1.1.78, on Linux, git
refused a folder owned by another uid than 1000, and the card had empty
fields (see [setup.md](setup.md)); 1.1.79 fixes that. Before 1.1.75
that card stayed empty in Docker.
