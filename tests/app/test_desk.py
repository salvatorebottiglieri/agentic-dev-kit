"""Tests for the #27 subcommands of app/desk.py: open, stop, shortcut.

Seams: `desk.main` with injected spawn/run (through monkeypatched wrappers) and
tmp state dirs; real `serve` subprocesses on ephemeral ports for open/stop.
Never port 8765, never the real state dir, never Edge, never the real desktop.
"""
from __future__ import annotations

import http.server
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from tests.app.test_server import APP, ROOT, desk  # modules loaded by path

DESK_PY = APP / "desk.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, up: bool = True, seconds: float = 15) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with socket.socket() as s:
            s.settimeout(0.2)
            if (s.connect_ex(("127.0.0.1", port)) == 0) == up:
                return True
        time.sleep(0.05)
    return False


@pytest.fixture
def serve_proc(tmp_path):
    """A real `desk.py serve` on an ephemeral port: (proc, port)."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(DESK_PY), "serve", "--port", str(port), "--root", str(tmp_path),
         "--workflow-dir", str(ROOT), "--state-dir", str(tmp_path / "serve-state")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_port(port)
        yield proc, port
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)


class Recorder:
    """Stands in for subprocess.Popen: records calls, starts nothing."""

    def __init__(self, pid: int = 4242, error: OSError | None = None):
        self.calls: list[tuple[list, dict]] = []
        self.pid, self.error = pid, error

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(pid=self.pid)


def patch_spawn(monkeypatch, spawn):
    """Route desk.main's start_server/open_window through `spawn`."""
    real_start, real_open = desk.start_server, desk.open_window
    monkeypatch.setattr(desk, "start_server",
                        lambda *a, **k: real_start(*a, **k, spawn=spawn))
    monkeypatch.setattr(desk, "open_window",
                        lambda *a, **k: real_open(*a, **k, spawn=spawn))


class Windows:
    """Spawn double that tells the Edge launch from the server launch."""

    def __init__(self):
        self.edge = Recorder()
        self.serve = Recorder()

    def __call__(self, argv, **kwargs):
        target = self.serve if "serve" in argv else self.edge
        return target(argv, **kwargs)


# --- open -------------------------------------------------------------------

def test_open_reuses_a_running_server_and_only_opens_the_window(
        monkeypatch, tmp_path, serve_proc):
    """Witness: a live server on an ephemeral port - distinguishes reuse (one
    Edge launch, zero serve spawns) from always spawning a server."""
    _, port = serve_proc
    spawn = Windows()
    patch_spawn(monkeypatch, spawn)
    code = desk.main(["open", "--port", str(port), "--state-dir", str(tmp_path / "st"),
                      "--edge", "/fake/msedge.exe"])
    assert code == 0
    assert spawn.serve.calls == []
    assert [c[0] for c in spawn.edge.calls] == [
        ["/fake/msedge.exe", f"--app=http://localhost:{port}/"]]
    assert not (tmp_path / "st").exists()


def test_open_starts_a_detached_serve_then_the_window(monkeypatch, tmp_path):
    """Witness: nothing answers on the port; a real serve is spawned by the
    injected Popen wrapper - distinguishes a detached session, pid file, log
    and the wait (the window opens only after the server answers) from a
    plain child or an immediate window."""
    port = free_port()
    state = tmp_path / "st"
    real_popen = subprocess.Popen
    started: list[subprocess.Popen] = []
    order: list[str] = []
    seen: dict = {}

    def spawn(argv, **kwargs):
        if "serve" in argv:
            seen["argv"], seen["kwargs"] = list(argv), kwargs
            proc = real_popen(argv, **kwargs)
            started.append(proc)
            order.append("serve")
            return proc
        order.append("edge:up" if not wait_port(port, up=False, seconds=0) else "edge:down")
        seen["edge"] = list(argv)
        return SimpleNamespace(pid=1)

    patch_spawn(monkeypatch, spawn)
    try:
        code = desk.main(["open", "--port", str(port), "--root", str(tmp_path),
                          "--workflow-dir", str(ROOT), "--state-dir", str(state),
                          "--edge", "/fake/msedge.exe"])
        assert code == 0
        assert seen["kwargs"]["start_new_session"] is True
        assert seen["argv"] == [sys.executable, str(DESK_PY), "serve", "--port", str(port),
                                "--root", str(tmp_path), "--workflow-dir", str(ROOT),
                                "--state-dir", str(state)]
        assert (state / f"desk-{port}.pid").read_text().strip() == str(started[0].pid)
        assert (state / "desk.log").exists()
        assert order == ["serve", "edge:up"]
        assert seen["edge"] == ["/fake/msedge.exe", f"--app=http://localhost:{port}/"]
    finally:
        for proc in started:
            proc.kill()
            proc.wait(10)


def foreign_server():
    """An HTTP server that answers 200 on / without this app's CSP."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_open_refuses_a_port_held_by_a_foreign_http_server(monkeypatch, tmp_path, capsys):
    """Witness: HTTP 200 on / without our CSP - distinguishes 'something else
    holds the port' (exit 2, no spawn) from 'any 200 is our server'."""
    httpd = foreign_server()
    try:
        port = httpd.server_address[1]
        assert desk.server_answers(port) is None
        spawn = Windows()
        patch_spawn(monkeypatch, spawn)
        code = desk.main(["open", "--port", str(port), "--state-dir", str(tmp_path / "st"),
                          "--edge", "/fake/msedge.exe"])
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert code == 2
    assert f"port {port} held by another program" in capsys.readouterr().err
    assert spawn.serve.calls == [] and spawn.edge.calls == []


def test_server_answers_is_false_when_nothing_listens():
    """Witness: a just-released port - distinguishes False (free) from None (foreign)."""
    assert desk.server_answers(free_port()) is False


def test_server_answers_is_true_for_our_server(serve_proc):
    """Witness: a real serve - distinguishes True from None/False."""
    assert desk.server_answers(serve_proc[1]) is True


def test_open_reports_the_log_when_the_server_never_answers(monkeypatch, tmp_path, capsys):
    """Witness: spawn double that starts nothing, deadline 0 - distinguishes
    exit 1 with the log path from exit 0 / a window opened on a dead server."""
    port = free_port()
    state = tmp_path / "st"
    spawn = Windows()
    patch_spawn(monkeypatch, spawn)
    real_wait = desk.wait_until_answering
    monkeypatch.setattr(desk, "wait_until_answering",
                        lambda p, deadline: real_wait(p, time.monotonic() + 0.3))
    code = desk.main(["open", "--port", str(port), "--state-dir", str(state),
                      "--edge", "/fake/msedge.exe"])
    assert code == 1
    assert str(state / "desk.log") in capsys.readouterr().err
    assert spawn.edge.calls == []


def test_open_reports_a_missing_edge_and_leaves_the_server(monkeypatch, tmp_path, capsys,
                                                           serve_proc):
    """Witness: Popen raising FileNotFoundError for Edge - distinguishes exit 2
    with 'Edge not found at <path>' from a traceback; the server keeps running."""
    proc, port = serve_proc
    spawn = Recorder(error=FileNotFoundError("nope"))
    patch_spawn(monkeypatch, spawn)
    code = desk.main(["open", "--port", str(port), "--state-dir", str(tmp_path / "st"),
                      "--edge", "/no/such/msedge.exe"])
    assert code == 2
    assert "Edge not found at /no/such/msedge.exe" in capsys.readouterr().err
    assert proc.poll() is None


def test_open_reports_a_state_dir_that_cannot_be_written(monkeypatch, tmp_path, capsys):
    """Witness: state dir under a regular file - distinguishes exit 2 with a
    message from an OSError traceback."""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    spawn = Windows()
    patch_spawn(monkeypatch, spawn)
    code = desk.main(["open", "--port", str(free_port()), "--state-dir", str(blocker / "st"),
                      "--edge", "/fake/msedge.exe"])
    assert code == 2
    assert "desk:" in capsys.readouterr().err
    assert spawn.edge.calls == []


def test_default_state_dir_follows_xdg_state_home(monkeypatch, tmp_path):
    """Witness: XDG_STATE_HOME set vs unset - distinguishes the XDG path from ~/.local/state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert desk.default_state_dir() == tmp_path / "xdg" / "agentic-dev-kit"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert desk.default_state_dir() == tmp_path / "home" / ".local/state/agentic-dev-kit"


# --- serve / stop -----------------------------------------------------------

def test_serve_exits_0_on_sigterm(serve_proc):
    """Witness: SIGTERM to a real serve - distinguishes exit 0 from -15."""
    proc, _ = serve_proc
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(10) == 0


def test_stop_terminates_our_serve_and_removes_the_pid_file(tmp_path, serve_proc, capsys):
    """Witness: a real serve with its pid file - distinguishes exit 0 + process
    gone + pid file removed from a no-op."""
    proc, port = serve_proc
    pid_file = tmp_path / f"desk-{port}.pid"
    pid_file.write_text(str(proc.pid))
    assert desk.main(["stop", "--port", str(port), "--state-dir", str(tmp_path)]) == 0
    assert proc.wait(10) == 0
    assert not pid_file.exists()


def test_stop_does_not_signal_a_process_that_is_not_our_serve(tmp_path, capsys):
    """Witness: a `sleep` named in the pid file - distinguishes 'stale: remove
    the file, exit 1, process untouched' from signalling whatever the pid is."""
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        pid_file = tmp_path / "desk-9999.pid"
        pid_file.write_text(str(sleeper.pid))
        assert desk.main(["stop", "--port", "9999", "--state-dir", str(tmp_path)]) == 1
        assert sleeper.poll() is None
        assert not pid_file.exists()
    finally:
        sleeper.kill()
        sleeper.wait(10)


def test_stop_does_not_signal_our_serve_on_another_port(tmp_path, serve_proc):
    """Witness: pid of a live serve on port P asked to stop port Q - distinguishes
    the exact `--port` match from any serve."""
    proc, port = serve_proc
    other = port + 1 if port < 65000 else port - 1
    (tmp_path / f"desk-{other}.pid").write_text(str(proc.pid))
    assert desk.stop_server(other, tmp_path) is False
    assert proc.poll() is None


def test_stop_without_a_pid_file_is_exit_1(tmp_path, capsys):
    """Witness: empty state dir - distinguishes exit 1 'nothing running' from a crash."""
    assert desk.main(["stop", "--port", "9999", "--state-dir", str(tmp_path)]) == 1
    assert "nothing running" in capsys.readouterr().err


def test_stop_reports_a_permission_error(monkeypatch, tmp_path, capsys, serve_proc):
    """Witness: stop_server raising PermissionError - distinguishes exit 2 with a
    message from a traceback."""
    proc, port = serve_proc
    (tmp_path / f"desk-{port}.pid").write_text(str(proc.pid))

    def deny(*args, **kwargs):
        raise PermissionError("not yours")

    monkeypatch.setattr(desk, "stop_server", deny)
    assert desk.main(["stop", "--port", str(port), "--state-dir", str(tmp_path)]) == 2
    assert "not yours" in capsys.readouterr().err


# --- shortcut ---------------------------------------------------------------

class FakeRun:
    def __init__(self, returncode=0, stdout="C:\\Users\\x\\Desktop\\Workflow.lnk\r\n", stderr=""):
        self.calls: list[tuple[list, dict]] = []
        self.result = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return self.result


def patch_run(monkeypatch, run):
    real = desk.write_shortcut
    monkeypatch.setattr(desk, "write_shortcut", lambda *a, **k: real(*a, **k, run=run))


def test_shortcut_builds_the_powershell_script(monkeypatch, capsys):
    """Witness: the script handed to powershell.exe - distinguishes a lnk on the
    desktop to wsl.exe -> this desk.py open from any other target, and a
    minimized window from a normal one."""
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-Test")
    run = FakeRun()
    patch_run(monkeypatch, run)
    assert desk.main(["shortcut", "--port", "8123"]) == 0
    (argv, kwargs), = run.calls
    assert argv[:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert kwargs == {"capture_output": True, "text": True}
    script = argv[3]
    assert "Workflow.lnk" in script
    assert "[Environment]::GetFolderPath('Desktop')" in script
    assert "C:\\Windows\\System32\\wsl.exe" in script
    assert f"-d Ubuntu-Test -- python3 {DESK_PY} open --port 8123" in script
    assert "WindowStyle = 7" in script
    assert "IconLocation" in script and "msedge.exe" in script
    assert ".Save()" in script
    assert capsys.readouterr().out.strip() == "C:\\Users\\x\\Desktop\\Workflow.lnk"


def test_shortcut_uses_the_given_desktop_and_doubles_single_quotes(monkeypatch):
    """Witness: --desktop with a quote - distinguishes the doubled quote ('')
    and the explicit folder from an injectable/unused default."""
    monkeypatch.setenv("WSL_DISTRO_NAME", "D")
    run = FakeRun()
    patch_run(monkeypatch, run)
    desk.main(["shortcut", "--desktop", "C:\\Users\\O'Neil\\Desktop"])
    script = run.calls[0][0][3]
    assert "'C:\\Users\\O''Neil\\Desktop'" in script
    assert "GetFolderPath" not in script


def test_shortcut_reports_a_powershell_failure(monkeypatch, capsys):
    """Witness: PowerShell exit 1 with a message - distinguishes exit 2 with
    that message from exit 0 / a traceback."""
    monkeypatch.setenv("WSL_DISTRO_NAME", "D")
    patch_run(monkeypatch, FakeRun(returncode=1, stdout="", stderr="boom from powershell"))
    assert desk.main(["shortcut"]) == 2
    assert "boom from powershell" in capsys.readouterr().err


# --- remote host (#37) ------------------------------------------------------

REMOTE = "desk.example.ts.net:8443"


def test_open_passes_the_remote_host_to_the_serve_it_starts(monkeypatch, tmp_path):
    """Witness `--remote-host desk.example.ts.net:8443` on open, then a run
    without it - distinguishes forwarding the flag to the detached serve from
    dropping it, and an unchanged argv when absent."""
    port = free_port()
    spawn = Windows()
    patch_spawn(monkeypatch, spawn)
    monkeypatch.setattr(desk, "wait_until_answering", lambda *a, **k: True)
    common = ["--port", str(port), "--root", str(tmp_path), "--workflow-dir", str(ROOT),
              "--state-dir", str(tmp_path / "st"), "--edge", "/fake/msedge.exe"]
    assert desk.main(["open", *common, "--remote-host", REMOTE]) == 0
    assert desk.main(["open", *common]) == 0
    (with_flag, _), (without, _) = spawn.serve.calls
    assert with_flag == [sys.executable, str(DESK_PY), "serve", "--port", str(port),
                         "--root", str(tmp_path), "--workflow-dir", str(ROOT),
                         "--state-dir", str(tmp_path / "st"), "--remote-host", REMOTE]
    assert "--remote-host" not in without


def test_serve_takes_the_remote_host_flag():
    """Witness `serve --remote-host H` parsed, default None - distinguishes a
    flag the detached serve accepts from an argparse error (exit 2)."""
    assert desk.parser().parse_args(["serve", "--remote-host", REMOTE]).remote_host == REMOTE
    assert desk.parser().parse_args(["serve"]).remote_host is None


def test_the_shortcut_carries_the_remote_host(monkeypatch):
    """Witness `open --port 8123 --remote-host H` in the .lnk arguments, and none
    without the flag - distinguishes a shortcut that starts a phone-ready
    server from one that starts a loopback-only one."""
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-Test")
    run = FakeRun()
    patch_run(monkeypatch, run)
    assert desk.main(["shortcut", "--port", "8123", "--remote-host", REMOTE]) == 0
    assert desk.main(["shortcut", "--port", "8123"]) == 0
    (with_flag, _), (without, _) = run.calls
    assert f"-d Ubuntu-Test -- python3 {DESK_PY} open --port 8123 --remote-host {REMOTE}" \
        in with_flag[3]
    assert "--remote-host" not in without[3]


# --- jira url (#26) ---------------------------------------------------------

JIRA = "https://example.atlassian.net"


def test_open_and_shortcut_carry_the_jira_url(monkeypatch, tmp_path):
    """Witness `--jira-url U` on open and on shortcut, then a run without it - distinguishes
    forwarding the flag to the detached serve and the .lnk (a link-less restart of the window)
    from dropping it, and an unchanged argv when absent; the flag follows --remote-host."""
    port = free_port()
    spawn = Windows()
    patch_spawn(monkeypatch, spawn)
    monkeypatch.setattr(desk, "wait_until_answering", lambda *a, **k: True)
    common = ["--port", str(port), "--root", str(tmp_path), "--workflow-dir", str(ROOT),
              "--state-dir", str(tmp_path / "st"), "--edge", "/fake/msedge.exe"]
    assert desk.main(["open", *common, "--remote-host", REMOTE, "--jira-url", JIRA]) == 0
    assert desk.main(["open", *common]) == 0
    (with_flag, _), (without, _) = spawn.serve.calls
    assert with_flag == [sys.executable, str(DESK_PY), "serve", "--port", str(port),
                         "--root", str(tmp_path), "--workflow-dir", str(ROOT),
                         "--state-dir", str(tmp_path / "st"),
                         "--remote-host", REMOTE, "--jira-url", JIRA]
    assert "--jira-url" not in without
    assert desk.parser().parse_args(["serve", "--jira-url", JIRA]).jira_url == JIRA
    assert desk.parser().parse_args(["serve"]).jira_url is None
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-Test")
    run = FakeRun()
    patch_run(monkeypatch, run)
    assert desk.main(["shortcut", "--port", "8123", "--jira-url", JIRA]) == 0
    assert desk.main(["shortcut", "--port", "8123"]) == 0
    (lnk, _), (plain, _) = run.calls
    assert f"-d Ubuntu-Test -- python3 {DESK_PY} open --port 8123 --jira-url {JIRA}" in lnk[3]
    assert "--jira-url" not in plain[3]


# --- #66: the state dir reaches serve, the cache file and the warm-up ---------

def test_open_passes_its_state_dir_to_serve(monkeypatch, tmp_path):
    """Witness `open --state-dir D` - distinguishes a detached serve told the directory open
    keeps its pid file and log in (so the phase cache lives beside them) from a serve that falls
    back to its own default."""
    spawn = Windows()
    patch_spawn(monkeypatch, spawn)
    monkeypatch.setattr(desk, "wait_until_answering", lambda *a, **k: True)
    state = tmp_path / "st"
    assert desk.main(["open", "--port", str(free_port()), "--root", str(tmp_path),
                      "--workflow-dir", str(ROOT), "--state-dir", str(state),
                      "--edge", "/fake/msedge.exe"]) == 0
    (argv, _), = spawn.serve.calls
    assert argv[argv.index("--state-dir") + 1] == str(state)


class WarmHttpd:
    """A fake server: serve_forever ends at once, warm_up records the thread that runs it."""

    def __init__(self) -> None:
        self.ran = threading.Event()
        self.thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        pass

    def warm_up(self) -> None:
        self.thread = threading.current_thread()
        self.ran.set()


def test_serve_starts_the_warm_up_with_the_cache_in_the_state_dir(monkeypatch, tmp_path):
    """Witness `serve --state-dir D` over a fake server, then `serve` with XDG_STATE_HOME set -
    distinguishes a cache file `<state dir>/phase-cache.json` (the flag, else the default state
    dir) and a `warm_up` run in a daemon thread named warm-up from a cache left in memory or a
    warm-up run in the main thread."""
    seen: list[dict] = []
    fake = WarmHttpd()

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen.append({"cache_file": cache_file})
        return fake

    monkeypatch.setattr(desk.server, "build_server", build)
    assert desk.main(["serve", "--state-dir", str(tmp_path / "st")]) == 0
    assert fake.ran.wait(5)
    assert fake.thread.name == "warm-up" and fake.thread.daemon
    assert fake.thread is not threading.main_thread()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert desk.main(["serve"]) == 0
    assert seen == [{"cache_file": tmp_path / "st" / "phase-cache.json"},
                    {"cache_file": tmp_path / "xdg" / "agentic-dev-kit" / "phase-cache.json"}]


def test_serve_handles_sigterm_before_the_port_is_open_and_restores_the_handler(monkeypatch, tmp_path):
    """Witness `serve` over a fake `build_server` that records the SIGTERM handler at the moment the port would
    open, then a `build_server` that fails with OSError - distinguishes a handler installed before the bind (a stop
    sent as soon as the port answers ends in exit 0) and restored on both exits, from one installed after the bind
    (a stop in that window kills the process with -15) or left behind after a failed start."""
    seen: list[object] = []
    before = signal.getsignal(signal.SIGTERM)

    def build(*args, **kwargs):
        seen.append(signal.getsignal(signal.SIGTERM))
        return WarmHttpd()

    monkeypatch.setattr(desk.server, "build_server", build)
    assert desk.main(["serve", "--state-dir", str(tmp_path / "st")]) == 0
    assert seen == [desk.raise_interrupt]
    assert signal.getsignal(signal.SIGTERM) is before

    def busy(*args, **kwargs):
        raise OSError("address in use")

    monkeypatch.setattr(desk.server, "build_server", busy)
    assert desk.main(["serve", "--state-dir", str(tmp_path / "st")]) == 2
    assert signal.getsignal(signal.SIGTERM) is before

    def stopped_while_building(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(desk.server, "build_server", stopped_while_building)
    try:
        code = desk.main(["serve", "--state-dir", str(tmp_path / "st")])
    except KeyboardInterrupt:   # caught here so that an escape fails this test instead of stopping pytest
        pytest.fail("a stop during the build escaped serve as KeyboardInterrupt")
    assert code == 0
    assert signal.getsignal(signal.SIGTERM) is before
