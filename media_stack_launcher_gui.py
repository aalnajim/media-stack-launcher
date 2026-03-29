#!/usr/bin/env python3
from __future__ import annotations

import json
import plistlib
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

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
    preserve_on_clean_start: bool = False  # If True, skip killing this service during clean_start (user-managed GUI apps)


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
        candidates=[
            "/Applications/bazarr/run_bazarr.sh",
        ],
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
    except subprocess.TimeoutExpired:
        return False
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
        # HTTPError still means the service is reachable (for example auth required).
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
        subprocess.run(["osascript", "-e", safari_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if browser_kind == "firefox":
        firefox_script = f'''
        tell application "Firefox"
            activate
            open location "{normalized}"
        end tell
        '''
        subprocess.run(["osascript", "-e", firefox_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    subprocess.run(["open", normalized], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        subprocess.run(["osascript", "-e", safari_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if browser_kind == "firefox":
        return


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


class LauncherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1080x720")
        self.root.minsize(1000, 640)

        self.config = load_config()
        self.state_lock = threading.RLock()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.running_state: Dict[str, str] = {key: "Stopped" for key in SERVICES}
        self.worker_thread: Optional[threading.Thread] = None
        self.clean_thread: Optional[threading.Thread] = None
        self.stop_requested = False
        self.opened_urls: set[str] = set()
        self.launched_this_run: set[str] = set()
        self.current_run_services: List[str] = []
        self.current_run_open_keys: set[str] = set()
        self.current_run_paths: Dict[str, str] = {}
        self.current_run_clean_start = False
        self.shutting_down = False

        self.workflow_var = tk.StringVar(value=self.config.get("workflow", "Full media workflow"))
        self.clean_start_var = tk.BooleanVar(value=self.config.get("clean_start", True))

        self.service_vars: Dict[str, tk.BooleanVar] = {}
        self.open_vars: Dict[str, tk.BooleanVar] = {}
        self.path_vars: Dict[str, tk.StringVar] = {}
        self.diagnostics_text: tk.Text | None = None

        self._build_ui()
        self._load_config_or_defaults()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _set_running_state(self, key: str, state: str) -> None:
        with self.state_lock:
            self.running_state[key] = state

    def _get_running_state(self, key: str) -> str:
        with self.state_lock:
            return self.running_state.get(key, "Stopped")

    def _refresh_status_table_async(self) -> None:
        self.root.after(0, self.refresh_status_table)

    def _track_process(self, key: str, proc: subprocess.Popen) -> None:
        with self.state_lock:
            self.processes[key] = proc

    def _clear_runtime_tracking(self) -> None:
        with self.state_lock:
            self.processes.clear()
            self.launched_this_run.clear()
            self.current_run_services.clear()
            self.current_run_open_keys.clear()
            self.current_run_paths.clear()
            self.current_run_clean_start = False

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text=APP_NAME, font=("SF Pro", 18, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            outer,
            text="Temporary launcher for Jackett, Bazarr, Sonarr, Radarr, and qBittorrent.",
        )
        subtitle.pack(anchor="w", pady=(2, 10))

        workflow_box = ttk.LabelFrame(outer, text="Workflow preset")
        workflow_box.pack(fill="x", pady=(0, 10))

        workflow_row = ttk.Frame(workflow_box, padding=10)
        workflow_row.pack(fill="x")

        ttk.Label(workflow_row, text="Preset:").pack(side="left")
        self.workflow_combo = ttk.Combobox(
            workflow_row,
            textvariable=self.workflow_var,
            values=list(WORKFLOWS.keys()),
            state="readonly",
            width=26,
        )
        self.workflow_combo.pack(side="left", padx=(8, 10))
        self.workflow_combo.bind("<<ComboboxSelected>>", self.on_workflow_change)

        ttk.Button(workflow_row, text="Apply preset", command=self.apply_workflow).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(workflow_row, text="Clean start before launch", variable=self.clean_start_var).pack(side="left")
        ttk.Button(workflow_row, text="Save preferences", command=self.save_preferences).pack(side="right")

        main = ttk.PanedWindow(outer, orient="horizontal")
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(right, weight=2)

        services_box = ttk.LabelFrame(left, text="Services")
        services_box.pack(fill="both", expand=True, padx=(0, 6))

        services_canvas = tk.Canvas(services_box, highlightthickness=0)
        services_scroll = ttk.Scrollbar(services_box, orient="vertical", command=services_canvas.yview)
        services_inner = ttk.Frame(services_canvas)

        services_inner.bind(
            "<Configure>",
            lambda e: services_canvas.configure(scrollregion=services_canvas.bbox("all"))
        )

        services_canvas.create_window((0, 0), window=services_inner, anchor="nw")
        services_canvas.configure(yscrollcommand=services_scroll.set)

        services_canvas.pack(side="left", fill="both", expand=True)
        services_scroll.pack(side="right", fill="y")

        for key, svc in SERVICES.items():
            card = ttk.LabelFrame(services_inner, text=svc.name)
            card.pack(fill="x", padx=10, pady=8)

            self.service_vars[key] = tk.BooleanVar(value=svc.enabled_by_default)
            self.open_vars[key] = tk.BooleanVar(value=svc.open_page_by_default)
            self.path_vars[key] = tk.StringVar(value="")

            row1 = ttk.Frame(card, padding=(10, 8))
            row1.pack(fill="x")
            ttk.Checkbutton(row1, text="Start this service", variable=self.service_vars[key]).pack(side="left")
            ttk.Checkbutton(row1, text="Open page after start", variable=self.open_vars[key]).pack(side="left", padx=(14, 0))

            row2 = ttk.Frame(card, padding=(10, 0, 10, 8))
            row2.pack(fill="x")
            ttk.Label(row2, text="Executable path:").pack(anchor="w")
            ttk.Entry(row2, textvariable=self.path_vars[key]).pack(side="left", fill="x", expand=True, pady=(4, 0))
            ttk.Button(row2, text="Detect", command=lambda k=key: self.detect_one(k)).pack(side="left", padx=6)
            ttk.Button(row2, text="Browse", command=lambda k=key: self.browse_one(k)).pack(side="left")

        right_top = ttk.LabelFrame(right, text="Actions")
        right_top.pack(fill="x", pady=(0, 8))

        actions = ttk.Frame(right_top, padding=10)
        actions.pack(fill="x")

        self.start_btn = ttk.Button(actions, text="Start selected", command=self.start_selected)
        self.start_btn.pack(fill="x")

        self.restart_btn = ttk.Button(actions, text="Restart selected", command=self.restart_selected)
        self.restart_btn.pack(fill="x", pady=6)

        self.stop_btn = ttk.Button(actions, text="Stop started", command=self.stop_started, state="disabled")
        self.stop_btn.pack(fill="x")

        ttk.Button(actions, text="Open selected pages", command=self.open_selected_pages).pack(fill="x")
        ttk.Button(actions, text="Detect all paths", command=self.detect_all).pack(fill="x", pady=(6, 0))
        self.clean_btn = ttk.Button(actions, text="Clean ports/processes now", command=self.clean_now)
        self.clean_btn.pack(fill="x", pady=(6, 0))
        ttk.Button(actions, text="Run diagnostics", command=self.show_diagnostics).pack(fill="x", pady=(6, 0))

        right_mid = ttk.LabelFrame(right, text="Status")
        right_mid.pack(fill="both", expand=True, pady=(0, 8))

        cols = ("service", "state", "port")
        self.tree = ttk.Treeview(right_mid, columns=cols, show="headings", height=8)
        self.tree.heading("service", text="Service")
        self.tree.heading("state", text="State")
        self.tree.heading("port", text="Port")
        self.tree.column("service", width=130, anchor="w")
        self.tree.column("state", width=220, anchor="w")
        self.tree.column("port", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)

        right_bottom = ttk.LabelFrame(right, text="Log")
        right_bottom.pack(fill="x")

        self.log_var = tk.StringVar(value="Ready.")
        ttk.Label(right_bottom, textvariable=self.log_var, padding=10, wraplength=340).pack(fill="x")

        self.refresh_status_table()

    def _load_config_or_defaults(self) -> None:
        saved_paths = self.config.get("paths", {})
        saved_start = self.config.get("start", {})
        saved_open = self.config.get("open", {})

        for key in SERVICES:
            if key in saved_paths:
                self.path_vars[key].set(saved_paths[key])
            if key in saved_start:
                self.service_vars[key].set(bool(saved_start[key]))
            if key in saved_open:
                self.open_vars[key].set(bool(saved_open[key]))

        if not saved_start and not saved_open:
            self.apply_workflow()

    def log(self, message: str) -> None:
        self.log_var.set(message)
        self.root.update_idletasks()

    def refresh_status_table(self) -> None:
        with self.state_lock:
            rows = [
                (svc.name, self.running_state.get(key, "Stopped"), svc.port if svc.port else "-")
                for key, svc in SERVICES.items()
            ]

        for item in self.tree.get_children():
            self.tree.delete(item)

        for service_name, state, port in rows:
            self.tree.insert(
                "",
                "end",
                values=(service_name, state, port),
            )

    def browse_one(self, key: str) -> None:
        path = filedialog.askopenfilename(title=f"Choose executable for {SERVICES[key].name}")
        if path:
            self.path_vars[key].set(path)

    def detect_one(self, key: str) -> None:
        exe = find_executable(SERVICES[key].candidates)
        if exe:
            self.path_vars[key].set(exe)
            self.log(f"Detected {SERVICES[key].name}: {exe}")
        else:
            self.log(f"Could not detect path for {SERVICES[key].name}")

    def detect_all(self) -> None:
        detected = 0
        for key in SERVICES:
            exe = find_executable(SERVICES[key].candidates)
            if exe:
                self.path_vars[key].set(exe)
                detected += 1
        self.log(f"Detected {detected} executable(s).")

    def collect_diagnostics(self) -> str:
        lines: List[str] = []
        lines.append(f"{APP_NAME} diagnostics")
        lines.append("=" * 60)
        lines.append(f"Workflow: {self.workflow_var.get()}")
        lines.append(f"Clean start: {self.clean_start_var.get()}")
        lines.append("")

        for key, svc in SERVICES.items():
            configured_path = self.path_vars[key].get().strip()
            selected = self.service_vars[key].get()
            open_page = self.open_vars[key].get()
            port_open = is_port_open(svc.port) if svc.port else False
            pids = pids_on_port(svc.port) if svc.port else []
            pid_names = pid_names_on_port(svc.port) if svc.port else []
            http_status = http_health_status(svc.urls[0]) if svc.urls else "No URL"
            detected = find_executable(svc.candidates)

            lines.append(f"[{svc.name}]")
            lines.append(f"Selected: {selected}")
            lines.append(f"Open page: {open_page}")
            lines.append(f"State: {self._get_running_state(key)}")
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

    def show_diagnostics(self) -> None:
        report = self.collect_diagnostics()

        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} Diagnostics")
        win.geometry("900x620")

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        text = tk.Text(frame, wrap="word")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)

        text.insert("1.0", report)
        text.configure(state="disabled")

        btns = ttk.Frame(win, padding=(10, 0, 10, 10))
        btns.pack(fill="x")

        def refresh_report() -> None:
            new_report = self.collect_diagnostics()
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", new_report)
            text.configure(state="disabled")

        def copy_report() -> None:
            win.clipboard_clear()
            win.clipboard_append(self.collect_diagnostics())
            self.log("Copied diagnostics to clipboard.")

        ttk.Button(btns, text="Refresh", command=refresh_report).pack(side="left")
        ttk.Button(btns, text="Copy diagnostics", command=copy_report).pack(side="left", padx=8)

    def on_workflow_change(self, _event=None) -> None:
        self.apply_workflow()

    def apply_workflow(self) -> None:
        preset_name = self.workflow_var.get()
        preset = WORKFLOWS.get(preset_name)
        if not preset:
            return

        start_set = set(preset["start"])
        open_set = set(preset["open"])

        for key in SERVICES:
            self.service_vars[key].set(key in start_set)
            self.open_vars[key].set(key in open_set)

        self.log(f"Applied preset: {preset_name}")

    def save_preferences(self) -> None:
        data = {
            "workflow": self.workflow_var.get(),
            "clean_start": self.clean_start_var.get(),
            "start": {k: v.get() for k, v in self.service_vars.items()},
            "open": {k: v.get() for k, v in self.open_vars.items()},
            "paths": {k: v.get().strip() for k, v in self.path_vars.items() if v.get().strip()},
        }
        save_config(data)
        self.log(f"Saved preferences to {CONFIG_PATH}")

    def selected_services(self) -> List[str]:
        return [key for key, var in self.service_vars.items() if var.get()]

    def open_selected_pages(self, service_keys: Optional[List[str]] = None) -> None:
        if self.stop_requested or self.shutting_down:
            return

        keys = service_keys if service_keys is not None else self.selected_services()
        opened = 0
        for key in keys:
            svc = SERVICES[key]
            if not self.open_vars[key].get():
                continue

            for url in svc.urls:
                with self.state_lock:
                    self.opened_urls.add(normalize_url(url))

                # Sonarr/Radarr can auto-open browser tabs on app launch.
                with self.state_lock:
                    launched_this_run = key in self.launched_this_run
                if svc.auto_opens_browser and launched_this_run:
                    continue

                browser_open_tab(url)
                opened += 1

        self.log(f"Opened or focused {opened} page(s).")

    def clean_now(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(APP_NAME, "Please stop current start operation before cleaning.")
            return
        if self.clean_thread and self.clean_thread.is_alive():
            messagebox.showinfo(APP_NAME, "Cleanup is already running.")
            return

        self.clean_btn.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.restart_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")

        # Cancel any pending open-page/startup callbacks while cleaning.
        self.stop_requested = True
        self.shutting_down = True
        self.log("Cleaning all known ports/processes...")

        self.clean_thread = threading.Thread(target=self._clean_worker, daemon=True)
        self.clean_thread.start()

    def _clean_worker(self) -> None:
        try:
            self.force_cleanup(selected_only=False)
            time.sleep(0.8)
            self._clear_runtime_tracking()
            self.stop_requested = False
            self.shutting_down = False
            self.root.after(0, self.refresh_port_based_states)
            self.root.after(0, lambda: self.log("Finished cleaning known ports/processes."))
        finally:
            self.root.after(0, lambda: self.clean_btn.config(state="normal"))
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.restart_btn.config(state="normal"))
            self.root.after(0, lambda: self.stop_btn.config(state="disabled"))
            self.clean_thread = None

    def refresh_port_based_states(self) -> None:
        for key, svc in SERVICES.items():
            if svc.port and is_port_open(svc.port):
                self._set_running_state(key, "Running or occupied")
            else:
                self._set_running_state(key, "Stopped")
        self.refresh_status_table()

    def force_cleanup(self, selected_only: bool = True, keys: Optional[List[str]] = None, for_clean_start: bool = False) -> None:
        if keys is not None:
            target_keys = keys
        else:
            target_keys = self.selected_services() if selected_only else list(SERVICES.keys())
        ordered = list(reversed(target_keys))

        for key in ordered:
            svc = SERVICES[key]

            # For web_only services (like qBittorrent Web UI), avoid killing externally managed daemons.
            if svc.app_mode == "web_only":
                continue

            # During clean_start, skip user-managed GUI apps to avoid force-killing a running instance.
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

    def start_selected(self) -> None:
        if self.clean_thread and self.clean_thread.is_alive():
            messagebox.showinfo(APP_NAME, "Cleanup is in progress. Please wait.")
            return

        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(APP_NAME, "Start operation is already running.")
            return

        run_services = self.selected_services()
        if not run_services:
            messagebox.showinfo(APP_NAME, "Please select at least one service.")
            return

        self.stop_requested = False
        self.shutting_down = False
        with self.state_lock:
            self.launched_this_run.clear()
            self.current_run_services = run_services
            self.current_run_open_keys = {
                key for key in run_services
                if self.open_vars[key].get()
            }
            self.current_run_paths = {key: self.path_vars[key].get().strip() for key in run_services}
            self.current_run_clean_start = bool(self.clean_start_var.get())
        self.start_btn.config(state="disabled")
        self.restart_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.log("Starting selected services...")

        self.worker_thread = threading.Thread(target=self._start_worker, daemon=True)
        self.worker_thread.start()

    def restart_selected(self) -> None:
        if self.clean_thread and self.clean_thread.is_alive():
            messagebox.showinfo(APP_NAME, "Cleanup is in progress. Please wait.")
            return

        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(APP_NAME, "Another operation is already running.")
            return

        self.log("Restarting selected services...")
        self.stop_started()
        time.sleep(1)
        self.start_selected()

    def _start_worker(self) -> None:
        with self.state_lock:
            run_services = list(self.current_run_services)
            run_open_keys = set(self.current_run_open_keys)
            clean_start = self.current_run_clean_start
        try:
            if clean_start:
                self.root.after(0, lambda: self.log("Cleaning previous processes and ports..."))
                self.force_cleanup(selected_only=True, keys=run_services, for_clean_start=True)
                time.sleep(2)

            for key in run_services:
                if self.stop_requested or self.shutting_down:
                    return
                self.root.after(0, lambda k=key: self.log(f"Starting {SERVICES[k].name}..."))
                self._start_one(key, self.current_run_paths.get(key, ""))

            for key in run_services:
                if self.stop_requested or self.shutting_down:
                    return
                svc = SERVICES[key]
                with self.state_lock:
                    launched_this_run = key in self.launched_this_run
                if not launched_this_run:
                    continue
                if svc.port and key in run_open_keys:
                    wait_for_http_ready(
                        svc.urls[0],
                        timeout=20,
                        should_stop=lambda: self.stop_requested or self.shutting_down,
                    )

            if self.stop_requested or self.shutting_down:
                return

            self.root.after(0, self.refresh_status_table)
            self.root.after(0, lambda: self.open_selected_pages([k for k in run_services if k in run_open_keys]))
            self.root.after(0, lambda: self.log("Done. Keep this app open while using the services."))
        finally:
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.restart_btn.config(state="normal"))
            if self.stop_requested or self.shutting_down:
                self.root.after(0, lambda: self.stop_btn.config(state="disabled"))
            else:
                self.root.after(0, lambda: self.stop_btn.config(state="normal"))
            self.worker_thread = None

    def _start_one(self, key: str, configured_path: str = "") -> None:
        if self.stop_requested or self.shutting_down:
            return

        svc = SERVICES[key]

        if svc.app_mode == "web_only":
            if svc.port and is_port_open(svc.port):
                self._set_running_state(key, "Running (Web UI)")
            else:
                self._set_running_state(key, "Web UI not reachable")
            self._refresh_status_table_async()
            return

        if svc.port and is_port_open(svc.port):
            pids = pids_on_port(svc.port)
            with self.state_lock:
                clean_start = self.current_run_clean_start
            if clean_start and pids and not svc.preserve_on_clean_start:
                kill_pids(pids)
                time.sleep(1)

        if svc.port and is_port_open(svc.port):
            self._set_running_state(key, "Already running")
            self._refresh_status_table_async()
            return

        exe = configured_path or find_executable(svc.candidates)
        if not exe:
            self._set_running_state(key, "Executable not found")
            self._refresh_status_table_async()
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
                app_target = svc.name

                # Prefer launching macOS apps via `open` to avoid single-instance quirks.
                if Path(exe).exists() and "/Contents/MacOS/" in exe:
                    app_bundle = str(Path(exe).parents[2])
                    if app_bundle.endswith(".app") and Path(app_bundle).exists():
                        spawn(["open", app_bundle])
                    else:
                        proc = spawn([exe])
                        self._track_process(key, proc)
                elif exe.endswith(".app"):
                    spawn(["open", exe])
                else:
                    spawn(["open", "-a", app_target])
                with self.state_lock:
                    self.launched_this_run.add(key)
                self._set_running_state(key, "Starting app...")
                self._refresh_status_table_async()
            else:
                proc = spawn([exe, *svc.args])
                self._track_process(key, proc)
                with self.state_lock:
                    self.launched_this_run.add(key)
                self._set_running_state(key, "Starting...")
                self._refresh_status_table_async()

            if svc.key == "qbittorrent" and svc.app_mode == "open_app":
                self._set_running_state(key, "Running")
                self._refresh_status_table_async()
                return

            if svc.port:
                start_time = time.time()
                while time.time() - start_time < 45:
                    if self.stop_requested or self.shutting_down:
                        break
                    if is_port_open(svc.port):
                        self._set_running_state(key, "Running")
                        self._refresh_status_table_async()
                        return
                    time.sleep(0.5)

                self._set_running_state(key, "Started (port not detected)")
            else:
                self._set_running_state(key, "Running")

        except Exception as exc:
            self._set_running_state(key, f"Error: {type(exc).__name__}: {exc}")

        self._refresh_status_table_async()

    def stop_started(self) -> None:
        self.stop_requested = True
        self.shutting_down = True

        with self.state_lock:
            run_services = list(self.current_run_services) if self.current_run_services else self.selected_services()

        with self.state_lock:
            tracked_processes = list(self.processes.items())

        for key, proc in tracked_processes:
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

        if self.worker_thread and self.worker_thread.is_alive() and threading.current_thread() is not self.worker_thread:
            self.worker_thread.join(timeout=3)

        self.force_cleanup(selected_only=True, keys=run_services)
        time.sleep(2.5)

        for key in SERVICES:
            if key in run_services:
                if SERVICES[key].port and is_port_open(SERVICES[key].port):
                    self._set_running_state(key, "Still running")
                else:
                    self._set_running_state(key, "Stopped")

        with self.state_lock:
            opened_urls = list(self.opened_urls)

        for url in opened_urls:
            browser_close_tab(url)
        for key in run_services:
            svc = SERVICES[key]
            for url in svc.urls:
                browser_close_tab(url, svc.name)

        self._clear_runtime_tracking()
        self.worker_thread = None
        self.shutting_down = False

        self.refresh_status_table()
        self.start_btn.config(state="normal")
        self.restart_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.log("Stopped processes started by this app, cleaned known ports/processes, and closed opened tabs.")

    def on_close(self) -> None:
        with self.state_lock:
            any_running = any(state not in {"Stopped", "Executable not found"} for state in self.running_state.values())
        if self.worker_thread and self.worker_thread.is_alive():
            any_running = True

        if any_running:
            if not messagebox.askyesno(APP_NAME, "Stop started services and exit?"):
                return
            self.shutting_down = True
            self.stop_started()

        self.save_preferences()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    LauncherApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
