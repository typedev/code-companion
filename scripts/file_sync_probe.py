#!/usr/bin/env python3
"""LAN file-sync core probe — validate transport + orchestration between two
machines over the REAL network, without the GTK UI.

It isolates the file-sync core: a fixed project id ("probe") and a pre-shared
token, so no pairing / project-registry / git-identity setup is needed.

On the SERVING machine (the one that HAS the files to share):

    uv run python scripts/file_sync_probe.py serve --project /path/to/project

  -> prints HOST / PORT / TOKEN. Leave it running (Ctrl-C to stop).

On the RECEIVING machine (copy the printed HOST/PORT/TOKEN):

    uv run python scripts/file_sync_probe.py preview --project /path/to/dest \
        --host HOST --port PORT --token TOKEN

    uv run python scripts/file_sync_probe.py get --project /path/to/dest \
        --host HOST --port PORT --token TOKEN --yes

Only what is inside `.shared` / `shared/` (and not git-tracked) is served. `get`
mirrors the peer's shared set into the destination; anything it would overwrite
or remove locally is first moved to `<dest>/.deleted/` (recoverable).
"""

import argparse
import sys
import time
from pathlib import Path

# Make 'src' importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services import file_sync_service as svc  # noqa: E402
from src.services.dispatch_broker import DispatchBroker  # noqa: E402
from src.services.dispatch_discovery import primary_ipv4  # noqa: E402
from src.services.paired_devices import PairedDevices  # noqa: E402

PROJECT_ID = "probe"


async def _deny(_id, _name):  # pair_prompt is unused (token is pre-shared)
    return False


def cmd_serve(args):
    project = str(Path(args.project).resolve())
    if not Path(project).is_dir():
        sys.exit(f"not a directory: {project}")
    paired = PairedDevices(path=Path(args.token_store))
    token = paired.token_for("probe-client") or paired.add("probe-client", "probe")

    broker = DispatchBroker(
        args.port, _deny, paired=paired,
        resolve_project=lambda pid: project if pid == PROJECT_ID else None,
    )
    broker.start()
    host = primary_ipv4()
    print(f"serving: {project}")
    print("-" * 60)
    print(f"HOST={host}  PORT={args.port}  TOKEN={token}")
    print("-" * 60)
    print("On the other machine:")
    print(f"  uv run python scripts/file_sync_probe.py preview --project <dest> "
          f"--host {host} --port {args.port} --token {token}")
    print("Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping…")
        broker.stop()


def _peer(args):
    return svc.Peer("probe", "peer", args.host, args.port, args.token)


def cmd_preview(args):
    dest = str(Path(args.project).resolve())
    p = svc.build_preview(dest, PROJECT_ID, _peer(args))
    print(f"remote shared files: {len(p.remote)}   local shared files: {len(p.local)}")
    print(f"GET  <-  fetch {len(p.get.fetch)}   remove {len(p.get.remove)}   "
          f"(DESTRUCTIVE locally: {p.get.destructive_count})")
    print(f"GIVE ->  send {len(p.give.fetch)}   peer-remove {len(p.give.remove)}")


def cmd_get(args):
    dest = str(Path(args.project).resolve())
    preview = svc.build_preview(dest, PROJECT_ID, _peer(args))
    if preview.diff.identical:
        print("already in sync — nothing to do.")
        return
    d = preview.get.destructive_count
    print(f"GET will fetch {len(preview.get.fetch)} file(s); "
          f"{d} local file(s) will be overwritten/removed (-> {dest}/.deleted/).")
    if d and not args.yes:
        sys.exit("refusing to overwrite/remove without --yes")

    def prog(done, total, rel):
        print(f"  [{done}/{total}] {rel}")

    r = svc.run_get(dest, PROJECT_ID, _peer(args), progress=prog)
    print(f"done: fetched {r.fetched}, removed {r.removed}, overwritten {r.overwritten}")
    if r.removed or r.overwritten:
        print(f"recover replaced/removed files under: {dest}/.deleted/")


def main():
    ap = argparse.ArgumentParser(description="LAN file-sync core probe")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="serve a project's shared set over the LAN")
    s.add_argument("--project", required=True)
    s.add_argument("--port", type=int, default=47100)
    s.add_argument("--token-store", default="/tmp/cc-filesync-probe.json")
    s.set_defaults(fn=cmd_serve)

    for name, fn, doc in (
        ("preview", cmd_preview, "show what a sync would do (no changes)"),
        ("get", cmd_get, "mirror the peer's shared set into --project"),
    ):
        c = sub.add_parser(name, help=doc)
        c.add_argument("--project", required=True)
        c.add_argument("--host", required=True)
        c.add_argument("--port", type=int, required=True)
        c.add_argument("--token", required=True)
        if name == "get":
            c.add_argument("--yes", action="store_true", help="apply destructive changes")
        c.set_defaults(fn=fn)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
