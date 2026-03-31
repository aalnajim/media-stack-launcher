from __future__ import annotations

import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

APP_NAME = "Media Stack Launcher"
CONFIG_PATH = Path.home() / ".media_stack_launcher.json"


@dataclass
class ServiceConfig:
    key: str
    name: str
    port: Optional[int]
    urls: List[str]
    candidates: List[str]
    args: List[str] = field(default_factory=list)
    enabled_by_default: bool = True
    open_page_by_default: bool = True
    cleanup_patterns: List[str] = field(default_factory=list)
    app_mode: str = "direct"  # direct | open_app | web_only
    auto_opens_browser: bool = False
    preserve_on_clean_start: bool = False


SERVICES: Dict[str, ServiceConfig] = {
    "jackett": ServiceConfig(
        key="jackett",
        name="Jackett",
        port=9117,
        urls=["http://127.0.0.1:9117"],
        candidates=[
            "jackett",
            "/opt/homebrew/bin/jackett",
            "/usr/local/bin/jackett",
            "/Applications/Jackett.app/Contents/MacOS/Jackett",
        ],
        cleanup_patterns=["/jackett", "Jackett"],
        app_mode="direct",
    ),
    "bazarr": ServiceConfig(
        key="bazarr",
        name="Bazarr",
        port=6767,
        urls=["http://127.0.0.1:6767"],
        candidates=["/Applications/bazarr/run_bazarr.sh"],
        cleanup_patterns=["bazarr.py", "/bazarr", "run_bazarr.sh"],
        app_mode="direct",
    ),
    "sonarr": ServiceConfig(
        key="sonarr",
        name="Sonarr",
        port=8989,
        urls=["http://127.0.0.1:8989"],
        candidates=[
            "/Applications/Sonarr.app/Contents/MacOS/Sonarr",
            "/Applications/Sonarr.app",
        ],
        cleanup_patterns=["/Applications/Sonarr.app", "Sonarr"],
        app_mode="open_app",
        auto_opens_browser=True,
    ),
    "radarr": ServiceConfig(
        key="radarr",
        name="Radarr",
        port=7878,
        urls=["http://127.0.0.1:7878"],
        candidates=[
            "/Applications/Radarr.app/Contents/MacOS/Radarr",
            "/Applications/Radarr.app",
        ],
        cleanup_patterns=["/Applications/Radarr.app", "Radarr"],
        app_mode="open_app",
        auto_opens_browser=True,
    ),
    "qbittorrent": ServiceConfig(
        key="qbittorrent",
        name="qBittorrent",
        port=8080,
        urls=["http://127.0.0.1:8080"],
        candidates=[
            "/Applications/qBittorrent.app",
            "/Applications/qBittorrent.app/Contents/MacOS/qBittorrent",
            "qbittorrent",
            "/opt/homebrew/bin/qbittorrent",
            "/usr/local/bin/qbittorrent",
        ],
        cleanup_patterns=["qBittorrent", "/Applications/qBittorrent.app"],
        enabled_by_default=False,
        open_page_by_default=True,
        app_mode="open_app",
        preserve_on_clean_start=True,
    ),
}


WORKFLOWS = {
    "Full media workflow": {
        "start": ["jackett", "bazarr", "sonarr", "radarr"],
        "open": ["jackett", "bazarr", "sonarr", "radarr"],
    },
    "Search + subtitles": {
        "start": ["jackett", "bazarr"],
        "open": ["jackett", "bazarr"],
    },
    "Arr stack": {
        "start": ["sonarr", "radarr", "bazarr"],
        "open": ["sonarr", "radarr", "bazarr"],
    },
    "qBittorrent + Jackett": {
        "start": ["jackett", "qbittorrent"],
        "open": ["jackett", "qbittorrent"],
    },
    "Custom": None,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def find_executable(candidates: List[str]) -> Optional[str]:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).exists():
            return candidate
    return None


def is_port_open(port: Optional[int], host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    if not port:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def run_quiet(command: List[str], timeout: Optional[float] = None) -> bool:
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return True
    except Exception:
        return False


def quit_mac_app(app_name: str, timeout: float = 1.5) -> bool:
    script = f'''
    tell application "System Events"
        set isRunning to exists process "{app_name}"
    end tell
    if isRunning then
        tell application "{app_name}" to quit
    end if
    '''
    try:
        subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return True
    except Exception:
        return False


def pids_on_port(port: int) -> List[str]:
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if not out:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def kill_pids(pids: List[str]) -> None:
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass


def pid_names_on_port(port: int) -> List[str]:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-i", f":{port}"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if not out:
            return []
        lines = out.splitlines()[1:]
        names = []
        for line in lines:
            parts = line.split()
            if parts:
                names.append(parts[0])
        return sorted(set(names))
    except subprocess.CalledProcessError:
        return []


def http_health_status(url: str, timeout: float = 2.0) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} (reachable)"
    except Exception as exc:
        return f"Unavailable ({type(exc).__name__})"


def executable_exists(path: str) -> bool:
    if not path:
        return False
    return Path(path).exists() or shutil.which(path) is not None


def get_default_browser_bundle_id() -> str:
    plist_path = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    try:
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
        for handler in data.get("LSHandlers", []):
            if handler.get("LSHandlerURLScheme") in {"http", "https"}:
                bundle_id = handler.get("LSHandlerRoleAll")
                if bundle_id:
                    return bundle_id
    except Exception:
        pass
    return "com.apple.Safari"


def get_default_browser_kind() -> str:
    bundle_id = get_default_browser_bundle_id().lower()
    if "firefox" in bundle_id:
        return "firefox"
    if "safari" in bundle_id:
        return "safari"
    return "default"


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def tab_match_prefix(url: str) -> str:
    parsed = urllib.parse.urlsplit(normalize_url(url))
    return f"{parsed.scheme}://{parsed.netloc}"


def browser_open_tab(url: str) -> None:
    normalized = normalize_url(url)
    prefix = tab_match_prefix(url)
    browser_kind = get_default_browser_kind()

    if browser_kind == "safari":
        safari_script = f'''
        tell application "Safari"
            if not (exists window 1) then
                make new document
            end if
            activate
            repeat with w in windows
                repeat with t in tabs of w
                    if (URL of t starts with "{prefix}") then
                        set current tab of w to t
                        set index of w to 1
                        return
                    end if
                end repeat
            end repeat
            tell window 1
                set current tab to (make new tab with properties {{URL:"{normalized}"}})
            end tell
        end tell
        '''
        run_quiet(["osascript", "-e", safari_script])
        return

    if browser_kind == "firefox":
        firefox_script = f'''
        tell application "Firefox"
            activate
            open location "{normalized}"
        end tell
        '''
        run_quiet(["osascript", "-e", firefox_script])
        return

    run_quiet(["open", normalized])


def browser_close_tab(url: str, title_hint: str | None = None) -> None:
    parsed = urllib.parse.urlsplit(normalize_url(url))
    host_port = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
    browser_kind = get_default_browser_kind()

    if browser_kind == "safari":
        title_check = ""
        if title_hint:
            title_check = f' or (name of t contains "{title_hint}")'

        safari_script = f'''
        tell application "Safari"
            if (count of windows) is 0 then return
            repeat with w in windows
                repeat with i from (count of tabs of w) to 1 by -1
                    set t to tab i of w
                    if ((URL of t contains "{host_port}"){title_check}) then
                        close t
                    end if
                end repeat
            end repeat
        end tell
        '''
        run_quiet(["osascript", "-e", safari_script])


def wait_for_http_ready(
    url: str,
    timeout: float = 20.0,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if should_stop and should_stop():
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


class ServiceManager:
    def __init__(self) -> None:
        self.state_lock = threading.RLock()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.running_state: Dict[str, str] = {key: "Stopped" for key in SERVICES}
        self.stop_requested = False
        self.shutting_down = False
        self.opened_urls: set[str] = set()
        self.launched_this_run: set[str] = set()
        self.current_run_services: List[str] = []
        self.current_run_open_keys: set[str] = set()
        self.current_run_paths: Dict[str, str] = {}
        self.current_run_clean_start = False

    def set_running_state(self, key: str, state: str) -> None:
        with self.state_lock:
            self.running_state[key] = state

    def get_running_state(self, key: str) -> str:
        with self.state_lock:
            return self.running_state.get(key, "Stopped")

    def snapshot_rows(self) -> List[tuple[str, str, str | int]]:
        with self.state_lock:
            return [
                (svc.name, self.running_state.get(key, "Stopped"), svc.port if svc.port else "-")
                for key, svc in SERVICES.items()
            ]

    def clear_runtime_tracking(self) -> None:
        with self.state_lock:
            self.processes.clear()
            self.launched_this_run.clear()
            self.current_run_services.clear()
            self.current_run_open_keys.clear()
            self.current_run_paths.clear()
            self.current_run_clean_start = False

    def detect_path(self, key: str) -> Optional[str]:
        return find_executable(SERVICES[key].candidates)

    def detect_all_paths(self) -> Dict[str, str]:
        detected: Dict[str, str] = {}
        for key in SERVICES:
            exe = self.detect_path(key)
            if exe:
                detected[key] = exe
        return detected

    def collect_diagnostics(
        self,
        workflow_name: str,
        clean_start: bool,
        path_map: Dict[str, str],
        start_map: Dict[str, bool],
        open_map: Dict[str, bool],
    ) -> str:
        lines: List[str] = []
        lines.append(f"{APP_NAME} diagnostics")
        lines.append("=" * 60)
        lines.append(f"Workflow: {workflow_name}")
        lines.append(f"Clean start: {clean_start}")
        lines.append("")

        for key, svc in SERVICES.items():
            configured_path = path_map.get(key, "").strip()
            selected = start_map.get(key, False)
            open_page = open_map.get(key, False)
            port_open = is_port_open(svc.port) if svc.port else False
            pids = pids_on_port(svc.port) if svc.port else []
            pid_names = pid_names_on_port(svc.port) if svc.port else []
            http_status = http_health_status(svc.urls[0]) if svc.urls else "No URL"
            detected = find_executable(svc.candidates)

            lines.append(f"[{svc.name}]")
            lines.append(f"Selected: {selected}")
            lines.append(f"Open page: {open_page}")
            lines.append(f"State: {self.get_running_state(key)}")
            lines.append(f"Configured path: {configured_path or '(empty)'}")
            lines.append(f"Configured path exists: {executable_exists(configured_path)}")
            lines.append(f"Detected executable: {detected or '(not found)'}")
            lines.append(f"Port: {svc.port if svc.port else '-'}")
            lines.append(f"Port open: {port_open}")
            lines.append(f"PIDs on port: {', '.join(pids) if pids else '(none)'}")
            lines.append(f"Processes on port: {', '.join(pid_names) if pid_names else '(none)'}")
            lines.append(f"HTTP health: {http_status}")
            lines.append(f"App mode: {svc.app_mode}")
            lines.append(f"Auto opens browser: {svc.auto_opens_browser}")
            lines.append("-" * 60)

        return "\n".join(lines)

    def refresh_port_based_states(self) -> None:
        for key, svc in SERVICES.items():
            if svc.port and is_port_open(svc.port):
                self.set_running_state(key, "Running or occupied")
            else:
                self.set_running_state(key, "Stopped")

    def open_selected_pages(self, service_keys: List[str], open_enabled: Dict[str, bool]) -> int:
        if self.stop_requested or self.shutting_down:
            return 0

        opened = 0
        for key in service_keys:
            svc = SERVICES[key]
            if not open_enabled.get(key, False):
                continue

            for url in svc.urls:
                with self.state_lock:
                    self.opened_urls.add(normalize_url(url))
                    launched_this_run = key in self.launched_this_run

                if svc.auto_opens_browser and launched_this_run:
                    continue

                browser_open_tab(url)
                opened += 1
        return opened

    def prepare_run(
        self,
        run_services: List[str],
        run_open_keys: set[str],
        run_paths: Dict[str, str],
        clean_start: bool,
    ) -> None:
        self.stop_requested = False
        self.shutting_down = False
        with self.state_lock:
            self.launched_this_run.clear()
            self.current_run_services = list(run_services)
            self.current_run_open_keys = set(run_open_keys)
            self.current_run_paths = dict(run_paths)
            self.current_run_clean_start = clean_start

    def _track_process(self, key: str, proc: subprocess.Popen) -> None:
        with self.state_lock:
            self.processes[key] = proc

    def force_cleanup(
        self,
        target_keys: List[str],
        for_clean_start: bool = False,
    ) -> None:
        for key in reversed(target_keys):
            svc = SERVICES[key]

            if svc.app_mode == "web_only":
                continue
            if for_clean_start and svc.preserve_on_clean_start:
                continue

            if svc.app_mode == "open_app":
                quit_mac_app(svc.name, timeout=1.2)
                time.sleep(0.4)

            if svc.name in ["Sonarr", "Radarr", "qBittorrent"]:
                run_quiet(["pkill", "-TERM", "-x", svc.name])
                run_quiet(["pkill", "-TERM", "-f", svc.name])
                time.sleep(0.4)
                run_quiet(["pkill", "-KILL", "-x", svc.name])
                run_quiet(["pkill", "-KILL", "-f", svc.name])

            for pattern in svc.cleanup_patterns:
                run_quiet(["pkill", "-TERM", "-f", pattern])

            time.sleep(0.3)

            for pattern in svc.cleanup_patterns:
                run_quiet(["pkill", "-KILL", "-f", pattern])

            if svc.port:
                kill_pids(pids_on_port(svc.port))

    def _start_one(self, key: str, configured_path: str, schedule: Callable[[Callable[[], None]], None], refresh_cb: Callable[[], None]) -> None:
        if self.stop_requested or self.shutting_down:
            return

        svc = SERVICES[key]

        if svc.app_mode == "web_only":
            if svc.port and is_port_open(svc.port):
                self.set_running_state(key, "Running (Web UI)")
            else:
                self.set_running_state(key, "Web UI not reachable")
            schedule(refresh_cb)
            return

        if svc.port and is_port_open(svc.port):
            pids = pids_on_port(svc.port)
            with self.state_lock:
                clean_start = self.current_run_clean_start
            if clean_start and pids and not svc.preserve_on_clean_start:
                kill_pids(pids)
                time.sleep(1)

        if svc.port and is_port_open(svc.port):
            self.set_running_state(key, "Already running")
            schedule(refresh_cb)
            return

        exe = configured_path or find_executable(svc.candidates)
        if not exe:
            self.set_running_state(key, "Executable not found")
            schedule(refresh_cb)
            return

        try:
            def spawn(command: List[str]) -> subprocess.Popen:
                if os.name != "nt":
                    return subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                    )
                return subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            if svc.app_mode == "open_app":
                if Path(exe).exists() and "/Contents/MacOS/" in exe:
                    app_bundle = str(Path(exe).parents[2])
                    if app_bundle.endswith(".app") and Path(app_bundle).exists():
                        spawn(["open", app_bundle])
                    else:
                        self._track_process(key, spawn([exe]))
                elif exe.endswith(".app"):
                    spawn(["open", exe])
                else:
                    spawn(["open", "-a", svc.name])

                with self.state_lock:
                    self.launched_this_run.add(key)
                self.set_running_state(key, "Starting app...")
                schedule(refresh_cb)
            else:
                self._track_process(key, spawn([exe, *svc.args]))
                with self.state_lock:
                    self.launched_this_run.add(key)
                self.set_running_state(key, "Starting...")
                schedule(refresh_cb)

            if svc.key == "qbittorrent" and svc.app_mode == "open_app":
                self.set_running_state(key, "Running")
                schedule(refresh_cb)
                return

            if svc.port:
                start_time = time.time()
                while time.time() - start_time < 45:
                    if self.stop_requested or self.shutting_down:
                        break
                    if is_port_open(svc.port):
                        self.set_running_state(key, "Running")
                        schedule(refresh_cb)
                        return
                    time.sleep(0.5)
                self.set_running_state(key, "Started (port not detected)")
            else:
                self.set_running_state(key, "Running")
        except Exception as exc:
            self.set_running_state(key, f"Error: {type(exc).__name__}: {exc}")

        schedule(refresh_cb)

    def start_prepared(
        self,
        log_cb: Callable[[str], None],
        refresh_cb: Callable[[], None],
        schedule: Callable[[Callable[[], None]], None],
        open_pages_cb: Callable[[List[str]], None],
    ) -> None:
        with self.state_lock:
            run_services = list(self.current_run_services)
            run_open_keys = set(self.current_run_open_keys)
            clean_start = self.current_run_clean_start
            run_paths = dict(self.current_run_paths)

        if clean_start:
            schedule(lambda: log_cb("Cleaning previous processes and ports..."))
            self.force_cleanup(run_services, for_clean_start=True)
            time.sleep(2)

        for key in run_services:
            if self.stop_requested or self.shutting_down:
                return
            schedule(lambda k=key: log_cb(f"Starting {SERVICES[k].name}..."))
            self._start_one(key, run_paths.get(key, ""), schedule, refresh_cb)

        for key in run_services:
            if self.stop_requested or self.shutting_down:
                return
            svc = SERVICES[key]
            with self.state_lock:
                launched_this_run = key in self.launched_this_run
            if launched_this_run and svc.port and key in run_open_keys:
                wait_for_http_ready(
                    svc.urls[0],
                    timeout=20,
                    should_stop=lambda: self.stop_requested or self.shutting_down,
                )

        if self.stop_requested or self.shutting_down:
            return

        schedule(refresh_cb)
        schedule(lambda: open_pages_cb([key for key in run_services if key in run_open_keys]))
        schedule(lambda: log_cb("Done. Keep this app open while using the services."))

    def stop_started(self, selected_fallback_keys: List[str]) -> None:
        self.stop_requested = True
        self.shutting_down = True

        with self.state_lock:
            run_services = list(self.current_run_services) if self.current_run_services else list(selected_fallback_keys)
            tracked_processes = list(self.processes.items())

        for _, proc in tracked_processes:
            try:
                if proc.poll() is None:
                    if os.name != "nt":
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    else:
                        proc.terminate()
            except Exception:
                pass

        with self.state_lock:
            self.processes.clear()

        self.force_cleanup(run_services)
        time.sleep(2.5)

        for key in SERVICES:
            if key in run_services:
                if SERVICES[key].port and is_port_open(SERVICES[key].port):
                    self.set_running_state(key, "Still running")
                else:
                    self.set_running_state(key, "Stopped")

        with self.state_lock:
            opened_urls = list(self.opened_urls)

        for url in opened_urls:
            browser_close_tab(url)
        for key in run_services:
            svc = SERVICES[key]
            for url in svc.urls:
                browser_close_tab(url, svc.name)

        self.clear_runtime_tracking()
        self.shutting_down = False

