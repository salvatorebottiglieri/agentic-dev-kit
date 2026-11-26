"""The workflow window's CLI: `desk.py serve [--port N] [--root DIR ...] [--workflow-dir DIR]`."""
from __future__ import annotations

import argparse
import http.client
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

if "server" not in sys.modules:  # run as a script or loaded by path: siblings are importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
import traces

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8765
EDGE_WINDOWS = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
STOP_WAIT = 10.0
OPEN_WAIT = 15.0
STATE_NAME = "agentic-dev-kit"   # same literal in wf.py and report_store.py; sync_kit.py rewrites it to "agentic-dev-kit" in the public kit (Decision 1)
EDGE = Path("/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / STATE_NAME


def server_answers(port: int, timeout: float = 1.0) -> bool | None:
    """True: our server; None: something else holds the port; False: nothing answers."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        resp = conn.getresponse()
        resp.read()
        ours = resp.status == 200 and resp.getheader("Content-Security-Policy") == server.CSP
        return True if ours else None
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def start_server(port: int, roots: tuple[Path, ...], workflow_dir: Path, state_dir: Path,
                 spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
                 remote_host: str | None = None, jira_url: str | None = None) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(Path(__file__).resolve()), "serve", "--port", str(port)]
    for root in roots:
        argv += ["--root", str(root)]
    argv += ["--workflow-dir", str(workflow_dir), "--state-dir", str(state_dir)]
    if remote_host:
        argv += ["--remote-host", remote_host]
    if jira_url:
        argv += ["--jira-url", jira_url]
    with open(state_dir / "desk.log", "ab") as log:
        proc = spawn(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True)
    (state_dir / f"desk-{port}.pid").write_text(f"{proc.pid}\n")
    return proc.pid


def wait_until_answering(port: int, deadline: float) -> bool:
    """Poll until our server answers or time.monotonic() reaches `deadline`."""
    while True:
        if server_answers(port) is True:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def open_window(port: int, edge: Path,
                spawn: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
    spawn([str(edge), f"--app=http://localhost:{port}/"], stdin=subprocess.DEVNULL,
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def is_our_serve(pid: int, port: int, proc_root: Path) -> bool:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    argv = raw.decode(errors="replace").split("\0")
    return (any(Path(a).name == "desk.py" for a in argv) and "serve" in argv
            and any(a == "--port" and argv[i + 1:i + 2] == [str(port)]
                    for i, a in enumerate(argv)))


def stop_server(port: int, state_dir: Path, proc_root: Path = Path("/proc")) -> bool:
    pid_file = state_dir / f"desk-{port}.pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False
    if not is_our_serve(pid, port, proc_root):
        pid_file.unlink(missing_ok=True)
        return False
    os.kill(pid, signal.SIGTERM)
    end = time.monotonic() + STOP_WAIT
    while time.monotonic() < end and is_our_serve(pid, port, proc_root) and not reaped(pid):
        time.sleep(0.05)
    pid_file.unlink(missing_ok=True)
    return True


def reaped(pid: int) -> bool:
    """True when `pid` is a zombie of ours or gone (a detached serve is not our child)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return stat.rpartition(")")[2].split()[0] == "Z"


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def write_shortcut(desk_py: Path, port: int, distro: str, desktop: str | None,
                   run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                   remote_host: str | None = None, jira_url: str | None = None) -> str:
    folder = ps_quote(desktop) if desktop else "[Environment]::GetFolderPath('Desktop')"
    arguments = f"-d {distro} -- python3 {desk_py} open --port {port}"
    if remote_host:
        arguments += f" --remote-host {remote_host}"
    if jira_url:
        arguments += f" --jira-url {jira_url}"
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$path = Join-Path {folder} 'Workflow.lnk'",
        "$link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)",
        "$link.TargetPath = 'C:\\Windows\\System32\\wsl.exe'",
        f"$link.Arguments = {ps_quote(arguments)}",
        "$link.WindowStyle = 7",
        f"$link.IconLocation = {ps_quote(EDGE_WINDOWS)}",
        "$link.Save()",
        "Write-Output $path",
    ])
    done = run(["powershell.exe", "-NoProfile", "-Command", script],
               capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"powershell exited {done.returncode}")
    return done.stdout.strip()


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="desk.py", description="The workflow desktop window.")
    sub = top.add_subparsers(dest="command", required=True)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--remote-host", default=None,
                        help="also accept this exact Host (e.g. the tailscale serve name:port)")
    shared.add_argument("--jira-url", default=None,
                        help="base URL that turns a Jira key in `tracker:` into a link")
    serve = sub.add_parser("serve", parents=[shared], help="serve the window on 127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--root", action="append", type=Path, default=None,
                       help="directory whose children are scanned for repos (default $HOME)")
    serve.add_argument("--workflow-dir", type=Path, default=APP_DIR.parent)
    serve.add_argument("--state-dir", type=Path, default=None,
                       help="where the phase cache file lives (default as open and stop)")
    serve.add_argument("--skills-dir", type=Path, default=None,
                       help="skills store the workflow map reads (default $AGENT_SKILLS_DIR or ~/.agents/skills)")
    opened = sub.add_parser("open", parents=[shared], help="start the server if needed, then open the window")
    opened.add_argument("--port", type=int, default=DEFAULT_PORT)
    opened.add_argument("--root", action="append", type=Path, default=None)
    opened.add_argument("--workflow-dir", type=Path, default=APP_DIR.parent)
    opened.add_argument("--edge", type=Path, default=EDGE)
    opened.add_argument("--state-dir", type=Path, default=None)
    stop = sub.add_parser("stop", help="stop the server that open started")
    stop.add_argument("--port", type=int, default=DEFAULT_PORT)
    stop.add_argument("--state-dir", type=Path, default=None)
    short = sub.add_parser("shortcut", parents=[shared], help="write Workflow.lnk on the Windows desktop")
    short.add_argument("--port", type=int, default=DEFAULT_PORT)
    short.add_argument("--desktop", default=None)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "open":
        return run_open(args)
    if args.command == "stop":
        return run_stop(args)
    if args.command == "shortcut":
        return run_shortcut(args)
    return run_serve(args)


def run_open(args: argparse.Namespace) -> int:
    port = args.port
    state_dir = args.state_dir or default_state_dir()
    answering = server_answers(port)
    if answering is None:
        print(f"desk: port {port} held by another program", file=sys.stderr)
        return 2
    if not answering:
        roots = tuple(args.root) if args.root else (Path.home(),)
        try:
            start_server(port, roots, args.workflow_dir, state_dir,
                         remote_host=args.remote_host, jira_url=args.jira_url)
        except OSError as error:
            print(f"desk: cannot start the server ({error})", file=sys.stderr)
            return 2
        if not wait_until_answering(port, time.monotonic() + OPEN_WAIT):
            print(f"desk: the server did not answer in time; see {state_dir / 'desk.log'}",
                  file=sys.stderr)
            return 1
    try:
        open_window(port, args.edge)
    except OSError:
        print(f"desk: Edge not found at {args.edge}", file=sys.stderr)
        return 2
    return 0


def run_stop(args: argparse.Namespace) -> int:
    try:
        stopped = stop_server(args.port, args.state_dir or default_state_dir())
    except OSError as error:
        print(f"desk: cannot stop the server ({error})", file=sys.stderr)
        return 2
    if not stopped:
        print(f"desk: nothing running on port {args.port}", file=sys.stderr)
        return 1
    return 0


def run_shortcut(args: argparse.Namespace) -> int:
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    try:
        path = write_shortcut(Path(__file__).resolve(), args.port, distro, args.desktop,
                              remote_host=args.remote_host, jira_url=args.jira_url)
    except (RuntimeError, OSError) as error:
        print(f"desk: cannot write the shortcut ({error})", file=sys.stderr)
        return 2
    print(path)
    return 0


def run_serve(args: argparse.Namespace) -> int:
    roots = tuple(args.root) if args.root else (Path.home(),)
    skills_dir = args.skills_dir or Path(os.environ.get("AGENT_SKILLS_DIR") or Path.home() / ".agents" / "skills")
    # the handler goes in before the port opens: a stop sent as soon as the port answers must end in exit 0
    previous = signal.signal(signal.SIGTERM, raise_interrupt)
    try:
        httpd = server.build_server(args.port, roots, args.workflow_dir, APP_DIR / "static",
                                    remote_host=args.remote_host, jira_url=args.jira_url,
                                    distro=os.environ.get("WSL_DISTRO_NAME", ""),
                                    skills_dir=skills_dir,
                                    cache_file=(args.state_dir or default_state_dir()) / "phase-cache.json",
                                    store=traces.load_report_store().default_store_path(os.environ, Path.home()))
    except OSError as error:
        signal.signal(signal.SIGTERM, previous)
        print(f"desk: port {args.port} in use ({error})", file=sys.stderr)
        return 2
    except KeyboardInterrupt:   # stopped while the server was being built: nothing to close
        signal.signal(signal.SIGTERM, previous)
        return 0
    print(f"desk: http://localhost:{args.port}/  (Ctrl-C stops)", flush=True)
    threading.Thread(target=httpd.warm_up, name="warm-up", daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        httpd.server_close()
    return 0


def raise_interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    sys.exit(main())
