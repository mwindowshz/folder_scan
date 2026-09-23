from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Folder Content Search"
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Match:
    path: str
    size: int
    modified: float


def human_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def byte_patterns(text: str, case_sensitive: bool) -> list[bytes]:
    """Return encodings commonly found in both text and binary files."""
    candidates: list[bytes] = []
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        encoded = text.encode(encoding)
        if not case_sensitive:
            encoded = encoded.lower()
        if encoded and encoded not in candidates:
            candidates.append(encoded)
    return candidates


def file_contains(path: str, patterns: list[bytes], case_sensitive: bool) -> bool:
    overlap = max(len(pattern) for pattern in patterns) - 1
    tail = b""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                return False
            data = tail + chunk
            searchable = data if case_sensitive else data.lower()
            if any(pattern in searchable for pattern in patterns):
                return True
            tail = data[-overlap:] if overlap else b""


def check_match_via_server(server_url: str, file_1: str, file_2: str) -> bool:
    payload = json.dumps({"file_1": file_1, "file_2": file_2}).encode("utf-8")
    request = urllib.request.Request(
        server_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data)
    return bool(parsed.get("match"))


def wait_for_server(host: str, port: int, timeout_seconds: float = 30.0) -> bool:
    import socket

    deadline = datetime.now().timestamp() + timeout_seconds
    while datetime.now().timestamp() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            pass
    return False


class FolderSearchApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x650")
        self.minsize(780, 480)

        self.folder_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.source_file_var = tk.StringVar()
        self.server_exe_var = tk.StringVar()
        self.server_url_var = tk.StringVar(value="http://127.0.0.1:1949/Protrack-LMS/License/check_match")
        self.recursive_var = tk.BooleanVar(value=True)
        self.case_var = tk.BooleanVar(value=True)
        self.compare_mode_var = tk.BooleanVar(value=False)
        self.compare_pt_var = tk.BooleanVar(value=True)
        self.compare_ptl_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Choose a folder and enter text to search for.")

        self._events: queue.Queue[tuple] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._match_paths: dict[str, str] = {}
        self._search_mode = "text"

        self._build_ui()
        self.after(100, self._process_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)

        ttk.Label(root, text="Folder:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        folder_entry = ttk.Entry(root, textvariable=self.folder_var)
        folder_entry.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse...", command=self._choose_folder).grid(
            row=0, column=2, sticky="ew", padx=(8, 0), pady=4
        )

        ttk.Label(root, text="Find text:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        search_entry = ttk.Entry(root, textvariable=self.search_var)
        search_entry.grid(row=1, column=1, sticky="ew", pady=4)
        search_entry.bind("<Return>", lambda _event: self._start_search())

        ttk.Label(root, text="Source file:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        source_entry = ttk.Entry(root, textvariable=self.source_file_var)
        source_entry.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse...", command=self._choose_source_file).grid(
            row=2, column=2, sticky="ew", padx=(8, 0), pady=4
        )

        ttk.Label(root, text="LicenseServer exe:").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        app_entry = ttk.Entry(root, textvariable=self.server_exe_var)
        app_entry.grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="Browse...", command=self._choose_license_app).grid(
            row=3, column=2, sticky="ew", padx=(8, 0), pady=4
        )

        ttk.Label(root, text="Server URL:").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        server_url_entry = ttk.Entry(root, textvariable=self.server_url_var)
        server_url_entry.grid(row=4, column=1, sticky="ew", pady=4)

        options = ttk.Frame(root)
        options.grid(row=5, column=1, columnspan=2, sticky="w", pady=(3, 8))
        ttk.Checkbutton(options, text="Include subfolders", variable=self.recursive_var).pack(side="left")
        ttk.Checkbutton(options, text="Case-sensitive", variable=self.case_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(options, text="LicenseServer compare mode", variable=self.compare_mode_var).pack(
            side="left", padx=(18, 0)
        )
        ttk.Checkbutton(options, text="Compare .pt", variable=self.compare_pt_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(options, text="Compare .ptl", variable=self.compare_ptl_var).pack(side="left", padx=(12, 0))

        actions = ttk.Frame(root)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.search_button = ttk.Button(actions, text="Search", command=self._start_search)
        self.search_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self._cancel_search, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Clear results", command=self._clear_results).pack(side="left", padx=(8, 0))

        results_frame = ttk.Frame(root)
        results_frame.grid(row=7, column=0, columnspan=3, sticky="nsew")
        results_frame.rowconfigure(0, weight=1)
        results_frame.columnconfigure(0, weight=1)

        columns = ("file", "size", "modified")
        self.results = ttk.Treeview(results_frame, columns=columns, show="headings", selectmode="extended")
        self.results.heading("file", text="Matching file", command=lambda: self._sort_results("file", False))
        self.results.heading("size", text="Size", command=lambda: self._sort_results("size", False))
        self.results.heading("modified", text="Modified", command=lambda: self._sort_results("modified", False))
        self.results.column("file", width=690, minwidth=300)
        self.results.column("size", width=100, anchor="e", stretch=False)
        self.results.column("modified", width=150, anchor="center", stretch=False)
        self.results.grid(row=0, column=0, sticky="nsew")
        self.results.bind("<Double-1>", lambda _event: self._open_selected())
        self.results.bind("<Return>", lambda _event: self._open_selected())

        scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=self.results.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results.configure(yscrollcommand=scrollbar.set)

        result_actions = ttk.Frame(root)
        result_actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(result_actions, text="Open selected file", command=self._open_selected).pack(side="left")
        ttk.Button(result_actions, text="Open containing folder", command=self._reveal_selected).pack(
            side="left", padx=(8, 0)
        )

        ttk.Separator(root).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(12, 7))
        ttk.Label(root, textvariable=self.status_var).grid(row=10, column=0, columnspan=2, sticky="w")
        self.progress = ttk.Progressbar(root, mode="indeterminate", length=150)
        self.progress.grid(row=10, column=2, sticky="e")

        folder_entry.focus_set()

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose folder to scan", initialdir=self.folder_var.get() or None)
        if selected:
            self.folder_var.set(selected)

    def _choose_source_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose source file",
            initialdir=self.source_file_var.get() or self.folder_var.get() or None,
            filetypes=[("PT/PTL files", "*.pt *.ptl"), ("All files", "*.*")],
        )
        if selected:
            self.source_file_var.set(selected)

    def _choose_license_app(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose LicenseServer executable",
            initialdir=self.server_exe_var.get() or self.folder_var.get() or None,
            filetypes=[("Executable files", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            self.server_exe_var.set(selected)

    def _start_search(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        folder = self.folder_var.get().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            messagebox.showerror(APP_TITLE, "Please choose an existing folder.")
            return

        self._clear_results()
        self._cancel.clear()
        self.search_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.start(12)
        if self.compare_mode_var.get():
            server_exe = self.server_exe_var.get().strip().strip('"')
            server_url = self.server_url_var.get().strip()
            source_file = self.source_file_var.get().strip().strip('"')
            if not server_exe or not os.path.isfile(server_exe):
                self._finish_ui()
                messagebox.showerror(APP_TITLE, "Please choose an existing LicenseServer executable.")
                return
            if not source_file or not os.path.isfile(source_file):
                self._finish_ui()
                messagebox.showerror(APP_TITLE, "Please choose an existing source file.")
                return
            if not server_url:
                self._finish_ui()
                messagebox.showerror(APP_TITLE, "Please enter the LicenseServer HTTP URL.")
                return
            if not self.compare_pt_var.get() and not self.compare_ptl_var.get():
                self._finish_ui()
                messagebox.showerror(APP_TITLE, "Please enable at least one compare file type.")
                return
            if Path(source_file).suffix.lower() not in {".pt", ".ptl"}:
                self._finish_ui()
                messagebox.showerror(APP_TITLE, "The source file must be a .pt or .ptl file.")
                return
            self._search_mode = "compare"
            self.status_var.set("Comparing files through LicenseServer...")
            self._worker = threading.Thread(
                target=self._compare_with_license_server,
                args=(
                    folder,
                    server_exe,
                    server_url,
                    source_file,
                    self.recursive_var.get(),
                    self.compare_pt_var.get(),
                    self.compare_ptl_var.get(),
                ),
                daemon=True,
            )
        else:
            needle = self.search_var.get()
            if not needle:
                self._finish_ui()
                messagebox.showerror(APP_TITLE, "Please enter text to search for.")
                return
            self._search_mode = "text"
            self.status_var.set("Scanning...")
            self._worker = threading.Thread(
                target=self._scan_text,
                args=(folder, needle, self.recursive_var.get(), self.case_var.get()),
                daemon=True,
            )
        self._worker.start()

    def _scan_text(self, folder: str, needle: str, recursive: bool, case_sensitive: bool) -> None:
        patterns = byte_patterns(needle, case_sensitive)
        scanned = matched = skipped = 0
        try:
            if recursive:
                paths = (os.path.join(base, name) for base, _dirs, files in os.walk(folder) for name in files)
            else:
                paths = (entry.path for entry in os.scandir(folder) if entry.is_file(follow_symlinks=False))

            for path in paths:
                if self._cancel.is_set():
                    self._events.put(("done", scanned, matched, skipped, True))
                    return
                scanned += 1
                try:
                    if file_contains(path, patterns, case_sensitive):
                        info = os.stat(path)
                        matched += 1
                        self._events.put(("match", Match(os.path.abspath(path), info.st_size, info.st_mtime)))
                except (OSError, PermissionError):
                    skipped += 1
                if scanned % 25 == 0:
                    self._events.put(("progress", scanned, matched, skipped))
            self._events.put(("done", scanned, matched, skipped, False))
        except OSError as error:
            self._events.put(("error", str(error)))

    def _compare_with_license_server(
        self,
        folder: str,
        server_exe: str,
        server_url: str,
        source_file: str,
        recursive: bool,
        compare_pt: bool,
        compare_ptl: bool,
    ) -> None:
        allowed_extensions = set()
        if compare_pt:
            allowed_extensions.add(".pt")
        if compare_ptl:
            allowed_extensions.add(".ptl")
        scanned = matched = skipped = 0
        launched_server = False
        server_process: subprocess.Popen[bytes] | None = None
        try:
            parsed_url = urllib.parse.urlparse(server_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise ValueError("Server URL must be a valid http:// or https:// address.")

            if parsed_url.hostname in {"127.0.0.1", "localhost"} and parsed_url.port:
                if not wait_for_server(parsed_url.hostname, parsed_url.port, timeout_seconds=0.5):
                    server_process = subprocess.Popen(
                        [server_exe],
                        cwd=str(Path(server_exe).parent),
                    )
                    launched_server = True
                    if not wait_for_server(parsed_url.hostname, parsed_url.port, timeout_seconds=30.0):
                        raise RuntimeError("LicenseServer did not become ready on the configured host and port.")

            for path in self._candidate_paths(folder, recursive, allowed_extensions):
                if self._cancel.is_set():
                    self._events.put(("done", scanned, matched, skipped, True))
                    return
                if os.path.abspath(path) == os.path.abspath(source_file):
                    continue
                scanned += 1
                try:
                    if check_match_via_server(server_url, source_file, path):
                        matched += 1
                        info = os.stat(path)
                        self._events.put(("match", Match(os.path.abspath(path), info.st_size, info.st_mtime)))
                except (OSError, PermissionError):
                    skipped += 1
                except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as error:
                    self._events.put(("error", f"LicenseServer request failed for {path}: {error}"))
                    return
                if scanned % 10 == 0:
                    self._events.put(("progress", scanned, matched, skipped))
            self._events.put(("done", scanned, matched, skipped, False))
        except OSError as error:
            self._events.put(("error", str(error)))
        except ValueError as error:
            self._events.put(("error", str(error)))
        finally:
            if launched_server and server_process is not None:
                server_process.terminate()
                try:
                    server_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server_process.kill()

    def _candidate_paths(self, folder: str, recursive: bool, allowed_extensions: set[str]) -> Iterator[str]:
        if recursive:
            for base, _dirs, files in os.walk(folder):
                for name in files:
                    if Path(name).suffix.lower() in allowed_extensions:
                        yield os.path.join(base, name)
        else:
            for entry in os.scandir(folder):
                if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in allowed_extensions:
                    yield entry.path

    def _process_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "match":
                    match: Match = event[1]
                    item = self.results.insert(
                        "",
                        "end",
                        values=(
                            match.path,
                            human_size(match.size),
                            datetime.fromtimestamp(match.modified).strftime("%Y-%m-%d %H:%M"),
                        ),
                        tags=(str(match.size), str(match.modified)),
                    )
                    self._match_paths[item] = match.path
                elif kind == "progress":
                    verb = "Compared" if self._search_mode == "compare" else "Scanned"
                    self.status_var.set(f"{verb} {event[1]:,} files — found {event[2]:,} matches")
                elif kind == "done":
                    self._finish_search(*event[1:])
                elif kind == "error":
                    self._finish_ui()
                    messagebox.showerror(APP_TITLE, f"The scan could not continue:\n\n{event[1]}")
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _finish_search(self, scanned: int, matched: int, skipped: int, cancelled: bool) -> None:
        self._finish_ui()
        state = "Cancelled" if cancelled else "Finished"
        skipped_text = f"; {skipped:,} unreadable files skipped" if skipped else ""
        verb = "compared" if self._search_mode == "compare" else "scanned"
        self.status_var.set(f"{state}: {verb} {scanned:,} files; found {matched:,} matches{skipped_text}.")

    def _finish_ui(self) -> None:
        self.progress.stop()
        self.search_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _cancel_search(self) -> None:
        self._cancel.set()
        self.status_var.set("Cancelling after the current file...")
        self.cancel_button.configure(state="disabled")

    def _clear_results(self) -> None:
        for item in self.results.get_children(""):
            self.results.delete(item)
        self._match_paths.clear()

    def _selected_path(self) -> str | None:
        selection = self.results.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Select a result first.")
            return None
        return self._match_paths.get(selection[0])

    def _open_selected(self) -> None:
        path = self._selected_path()
        if path:
            self._open_path(path)

    def _reveal_selected(self) -> None:
        path = self._selected_path()
        if not path:
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", str(Path(path).parent)])
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"Could not open the folder:\n\n{error}")

    def _open_path(self, path: str) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"Could not open the file:\n\n{error}")

    def _sort_results(self, column: str, descending: bool) -> None:
        items = list(self.results.get_children(""))
        if column == "file":
            key = lambda item: self.results.set(item, column).casefold()
        elif column == "size":
            key = lambda item: int(self.results.item(item, "tags")[0])
        else:
            key = lambda item: float(self.results.item(item, "tags")[1])
        items.sort(key=key, reverse=descending)
        for index, item in enumerate(items):
            self.results.move(item, "", index)
        self.results.heading(column, command=lambda: self._sort_results(column, not descending))


if __name__ == "__main__":
    FolderSearchApp().mainloop()
