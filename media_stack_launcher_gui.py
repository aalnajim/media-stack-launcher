#!/usr/bin/env python3
from __future__ import annotations

import threading
import time
import tkinter as tk
from typing import Dict, List, Optional
from tkinter import filedialog, messagebox, ttk

from service_manager import (
    APP_NAME,
    CONFIG_PATH,
    SERVICES,
    WORKFLOWS,
    ServiceManager,
    load_config,
    save_config,
)


class LauncherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1080x720")
        self.root.minsize(1000, 640)

        self.config = load_config()
        self.manager = ServiceManager()
        self.worker_thread: Optional[threading.Thread] = None
        self.clean_thread: Optional[threading.Thread] = None

        self.workflow_var = tk.StringVar(value=self.config.get("workflow", "Full media workflow"))
        self.clean_start_var = tk.BooleanVar(value=self.config.get("clean_start", True))

        self.service_vars: Dict[str, tk.BooleanVar] = {}
        self.open_vars: Dict[str, tk.BooleanVar] = {}
        self.path_vars: Dict[str, tk.StringVar] = {}
        self.diagnostics_text: tk.Text | None = None

        self._build_ui()
        self._load_config_or_defaults()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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
        for item in self.tree.get_children():
            self.tree.delete(item)

        for service_name, state, port in self.manager.snapshot_rows():
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
        exe = self.manager.detect_path(key)
        if exe:
            self.path_vars[key].set(exe)
            self.log(f"Detected {SERVICES[key].name}: {exe}")
        else:
            self.log(f"Could not detect path for {SERVICES[key].name}")

    def detect_all(self) -> None:
        detected_paths = self.manager.detect_all_paths()
        for key, exe in detected_paths.items():
            self.path_vars[key].set(exe)
        self.log(f"Detected {len(detected_paths)} executable(s).")

    def collect_diagnostics(self) -> str:
        return self.manager.collect_diagnostics(
            workflow_name=self.workflow_var.get(),
            clean_start=self.clean_start_var.get(),
            path_map={key: var.get().strip() for key, var in self.path_vars.items()},
            start_map={key: var.get() for key, var in self.service_vars.items()},
            open_map={key: var.get() for key, var in self.open_vars.items()},
        )

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
        if self.manager.stop_requested or self.manager.shutting_down:
            return

        keys = service_keys if service_keys is not None else self.selected_services()
        opened = self.manager.open_selected_pages(
            keys,
            {key: var.get() for key, var in self.open_vars.items()},
        )
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
        self.manager.stop_requested = True
        self.manager.shutting_down = True
        self.log("Cleaning all known ports/processes...")

        self.clean_thread = threading.Thread(target=self._clean_worker, daemon=True)
        self.clean_thread.start()

    def _clean_worker(self) -> None:
        try:
            self.manager.force_cleanup(list(SERVICES.keys()))
            time.sleep(0.8)
            self.manager.clear_runtime_tracking()
            self.manager.stop_requested = False
            self.manager.shutting_down = False
            self.root.after(0, self.refresh_port_based_states)
            self.root.after(0, lambda: self.log("Finished cleaning known ports/processes."))
        finally:
            self.root.after(0, lambda: self.clean_btn.config(state="normal"))
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.restart_btn.config(state="normal"))
            self.root.after(0, lambda: self.stop_btn.config(state="disabled"))
            self.clean_thread = None

    def refresh_port_based_states(self) -> None:
        self.manager.refresh_port_based_states()
        self.refresh_status_table()

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

        self.manager.prepare_run(
            run_services=run_services,
            run_open_keys={key for key in run_services if self.open_vars[key].get()},
            run_paths={key: self.path_vars[key].get().strip() for key in run_services},
            clean_start=bool(self.clean_start_var.get()),
        )
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
        try:
            self.manager.start_prepared(
                log_cb=self.log,
                refresh_cb=self.refresh_status_table,
                schedule=lambda fn: self.root.after(0, fn),
                open_pages_cb=self.open_selected_pages,
            )
        finally:
            self.root.after(0, lambda: self.start_btn.config(state="normal"))
            self.root.after(0, lambda: self.restart_btn.config(state="normal"))
            if self.manager.stop_requested or self.manager.shutting_down:
                self.root.after(0, lambda: self.stop_btn.config(state="disabled"))
            else:
                self.root.after(0, lambda: self.stop_btn.config(state="normal"))
            self.worker_thread = None

    def stop_started(self) -> None:
        self.manager.stop_requested = True
        self.manager.shutting_down = True

        if self.worker_thread and self.worker_thread.is_alive() and threading.current_thread() is not self.worker_thread:
            self.worker_thread.join(timeout=3)

        self.manager.stop_started(self.selected_services())
        self.worker_thread = None

        self.refresh_status_table()
        self.start_btn.config(state="normal")
        self.restart_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.log("Stopped processes started by this app, cleaned known ports/processes, and closed opened tabs.")

    def on_close(self) -> None:
        any_running = any(
            self.manager.get_running_state(key) not in {"Stopped", "Executable not found"}
            for key in SERVICES
        )
        if self.worker_thread and self.worker_thread.is_alive():
            any_running = True

        if any_running:
            if not messagebox.askyesno(APP_NAME, "Stop started services and exit?"):
                return
            self.manager.shutting_down = True
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
