"""Desktop GUI for merging GoPro .360 chapter blocks."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from pathlib import Path

import customtkinter as ctk

from gopro_360_merge.detect import Block, scan_directory
from gopro_360_merge.merge import (
    estimate_block_duration,
    format_timecode,
    merge_block,
    require_tools,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
APP_ICON_PNG = ASSETS_DIR / "app_icon.png"
APP_ICON_ICO = ASSETS_DIR / "app_icon.ico"
# Must match System.AppUserModel.ID stamped on Gopro360Merge.lnk (gui.ps1).
APP_USER_MODEL_ID = "com.gopro360merge.gui"


def _set_windows_app_user_model_id() -> None:
    """Identify this process to the Windows taskbar (before any Tk window)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 — identity is best-effort
        pass


STAGE_LABELS = {
    "probe": "Estimating duration",
    "join": "Joining chapters",
    "ffmpeg": "Joining chapters",
    "udtacopy": "Copying udta metadata",
    "trim": "Trimming empty GPMF",
    "rename": "Renaming to .360",
}


def format_size(num_bytes: int) -> str:
    gb = num_bytes / (1024**3)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = num_bytes / (1024**2)
    return f"{mb:.1f} MB"


def stage_progress(stage: str, current: float, total: float) -> tuple[float, str]:
    labels = STAGE_LABELS
    stage_base = {
        "probe": 0.0,
        "join": 5.0,
        "ffmpeg": 5.0,
        "udtacopy": 88.0,
        "trim": 92.0,
        "rename": 97.0,
    }
    stage_span = {
        "probe": 5.0,
        "join": 83.0,
        "ffmpeg": 83.0,
        "udtacopy": 4.0,
        "trim": 5.0,
        "rename": 3.0,
    }
    frac = 0.0 if total <= 0 else min(max(current / total, 0.0), 1.0)
    completed = stage_base.get(stage, 0.0) + stage_span.get(stage, 0.0) * frac
    return completed, labels.get(stage, stage)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("gopro-360-merge")
        self.geometry("760x420")
        self.minsize(640, 360)
        self._icon_images: list[tk.PhotoImage] = []
        self._apply_app_icon()

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self._blocks: list[Block] = []
        self._block_vars: dict[str, ctk.BooleanVar] = {}
        self._durations: dict[str, float] = {}
        self._busy = False
        self._event_queue: queue.Queue[tuple] = queue.Queue()

        self._build_ui()
        self.after_idle(self._fit_window)
        self.after(100, self._poll_events)

    def _apply_app_icon(self) -> None:
        """Set window / taskbar icon; missing assets are ignored."""
        try:
            if sys.platform == "win32" and APP_ICON_ICO.is_file():
                ico = str(APP_ICON_ICO)
                self.iconbitmap(ico)
                self.iconbitmap(default=ico)
            if APP_ICON_PNG.is_file():
                photo = tk.PhotoImage(file=str(APP_ICON_PNG))
                self._icon_images.append(photo)
                self.iconphoto(True, photo)
        except Exception:  # noqa: BLE001 — icon must never block startup
            pass

    def _section(self, row: int, *, pady: tuple[int, int] = (0, 6)) -> ctk.CTkFrame:
        """Content-sized card (CTkFrame defaults to height=200 — override)."""
        frame = ctk.CTkFrame(self, height=1)
        frame.grid(row=row, column=0, sticky="ew", padx=12, pady=pady)
        frame.grid_columnconfigure(0, weight=1)
        return frame

    def _fit_window(self) -> None:
        """Shrink/grow window height to the laid-out content."""
        self.update_idletasks()
        req_h = max(self.winfo_reqheight(), 360)
        height = min(max(req_h + 8, 360), 900)
        width = max(self.winfo_width(), 760) if self.winfo_width() > 100 else 760
        self.geometry(f"{width}x{height}")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # --- Folder ---
        folder = self._section(0, pady=(12, 6))
        folder.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(folder, text="GS*.360 folder *").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 2)
        )
        self.folder_var = ctk.StringVar(value="")
        self.folder_entry = ctk.CTkEntry(
            folder, textvariable=self.folder_var, height=28
        )
        self.folder_entry.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=(8, 6), pady=(0, 8)
        )
        ctk.CTkButton(
            folder,
            text="Browse…",
            width=88,
            height=28,
            command=self._pick_folder,
        ).grid(row=1, column=2, padx=(0, 4), pady=(0, 8))
        ctk.CTkButton(
            folder,
            text="Scan",
            width=88,
            height=28,
            command=self._scan_folder,
        ).grid(row=1, column=3, padx=(0, 8), pady=(0, 8))

        # --- Blocks ---
        blocks_outer = self._section(1, pady=(0, 6))
        blocks_header = ctk.CTkFrame(blocks_outer, fg_color="transparent", height=1)
        blocks_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        blocks_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            blocks_header,
            text="Blocks / chapters *",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            blocks_header,
            text="All",
            width=64,
            height=26,
            command=self._select_all,
        ).grid(row=0, column=1, padx=(4, 2))
        ctk.CTkButton(
            blocks_header,
            text="None",
            width=72,
            height=26,
            command=self._select_none,
        ).grid(row=0, column=2, padx=(2, 0))

        self.blocks_list = ctk.CTkFrame(
            blocks_outer, fg_color="transparent", height=1
        )
        self.blocks_list.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.blocks_list.grid_columnconfigure(0, weight=1)
        self._blocks_placeholder = ctk.CTkLabel(
            self.blocks_list,
            text="No blocks — pick a folder and scan.",
            text_color="gray60",
            font=ctk.CTkFont(size=12),
        )
        self._blocks_placeholder.grid(row=0, column=0, sticky="w", padx=2, pady=2)

        # --- Output + run ---
        opts = self._section(2, pady=(0, 6))
        opts.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(opts, text="Output *").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2)
        )
        self.output_var = ctk.StringVar(value="")
        ctk.CTkEntry(opts, textvariable=self.output_var, height=28).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=(8, 6), pady=(0, 4)
        )
        ctk.CTkButton(
            opts,
            text="…",
            width=36,
            height=28,
            command=self._pick_output,
        ).grid(row=1, column=2, padx=(0, 8), pady=(0, 4))

        self.duration_label = ctk.CTkLabel(
            opts,
            text="Duration: —",
            text_color="gray70",
            font=ctk.CTkFont(size=12),
        )
        self.duration_label.grid(
            row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4)
        )
        self.run_button = ctk.CTkButton(
            opts,
            text="Run merge",
            height=32,
            command=self._run,
        )
        self.run_button.grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8)
        )

        # --- Status + progress + log ---
        bottom = self._section(3, pady=(0, 12))
        self.status_label = ctk.CTkLabel(
            bottom,
            text="Choose a folder and click Scan.",
            anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.progress = ctk.CTkProgressBar(bottom, height=8)
        self.progress.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.progress.set(0)
        self.log_box = ctk.CTkTextbox(bottom, height=68)
        self.log_box.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.log_box.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _refresh_idle_status(self) -> None:
        """Status when not merging — mirrors what the user still needs to do."""
        if self._busy:
            return
        if not self._blocks:
            folder = self.folder_var.get().strip()
            if not folder:
                self._set_status("Choose a folder and click Scan.")
            else:
                self._set_status(
                    "No blocks found. Choose another folder or scan again."
                )
            return
        selected = self._selected_blocks()
        if not selected:
            self._set_status("Select one or more blocks.")
            return
        if not self.output_var.get().strip():
            self._set_status("Set the output folder.")
            return
        n = len(selected)
        noun = "block" if n == 1 else "blocks"
        self._set_status(f"Ready to merge ({n} {noun}).")

    def _log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.run_button.configure(state=state)

    def _pick_folder(self) -> None:
        path = filedialog.askdirectory(title="Folder with GS*.360 files")
        if path:
            self.folder_var.set(path)
            self.output_var.set(str(Path(path) / "merged"))
            self._scan_folder()

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Output folder")
        if path:
            self.output_var.set(path)
            self._refresh_idle_status()

    def _clear_blocks_ui(self) -> None:
        for child in self.blocks_list.winfo_children():
            child.destroy()
        self._block_vars.clear()

    def _scan_folder(self) -> None:
        raw = self.folder_var.get().strip()
        if not raw:
            messagebox.showwarning("Folder", "Choose a folder with GS*.360 files.")
            self._refresh_idle_status()
            return
        source = Path(raw).expanduser().resolve()
        if not source.is_dir():
            messagebox.showerror("Folder", f"Not a directory:\n{source}")
            self._refresh_idle_status()
            return

        self._set_status("Scanning…")
        try:
            blocks = scan_directory(source)
        except NotADirectoryError as exc:
            messagebox.showerror("Folder", str(exc))
            self._refresh_idle_status()
            return

        self._blocks = blocks
        self._durations.clear()
        self._clear_blocks_ui()

        if not self.output_var.get().strip():
            self.output_var.set(str(source / "merged"))

        if not blocks:
            ctk.CTkLabel(
                self.blocks_list,
                text=f"No GS*.360 files found in {source}",
                text_color="orange",
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, sticky="w", padx=2, pady=2)
            self.duration_label.configure(text="Duration: —")
            self._log(f"No blocks in {source}")
            self._refresh_idle_status()
            self.after_idle(self._fit_window)
            return

        for i, block in enumerate(blocks):
            var = ctk.BooleanVar(value=len(blocks) == 1)
            self._block_vars[block.block_id] = var
            text = (
                f"#{block.block_id}  ·  {len(block.chapters)} caps  ·  "
                f"{format_size(block.size_bytes)}  ·  "
                f"{block.first.path.name} … {block.last.path.name}"
            )
            cb = ctk.CTkCheckBox(
                self.blocks_list,
                text=text,
                variable=var,
                command=self._on_selection_change,
            )
            cb.grid(row=i, column=0, sticky="ew", padx=2, pady=1)

        self._log(f"Detected {len(blocks)} block(s) in {source}")
        self._on_selection_change()
        self.after_idle(self._fit_window)

    def _select_all(self) -> None:
        for var in self._block_vars.values():
            var.set(True)
        self._on_selection_change()

    def _select_none(self) -> None:
        for var in self._block_vars.values():
            var.set(False)
        self._on_selection_change()

    def _selected_blocks(self) -> list[Block]:
        return [
            b
            for b in self._blocks
            if self._block_vars.get(b.block_id) and self._block_vars[b.block_id].get()
        ]

    def _on_selection_change(self) -> None:
        selected = self._selected_blocks()
        if not selected:
            self.duration_label.configure(text="Duration: — (no block selected)")
            self._refresh_idle_status()
            return

        missing = [b for b in selected if b.block_id not in self._durations]
        if not missing:
            self._update_duration_label(selected)
            self._refresh_idle_status()
            return

        self.duration_label.configure(text="Duration: estimating…")
        self._set_status("Estimating duration…")
        ids = [b.block_id for b in missing]

        def worker() -> None:
            results: dict[str, float] = {}
            errors: list[str] = []
            for block in missing:
                try:
                    results[block.block_id] = estimate_block_duration(block)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{block.block_id}: {exc}")
            self._event_queue.put(("durations", results, errors, ids))

        threading.Thread(target=worker, daemon=True).start()

    def _update_duration_label(self, selected: list[Block]) -> None:
        parts = []
        for block in selected:
            total = self._durations.get(block.block_id)
            if total is None:
                parts.append(f"#{block.block_id}: …")
            else:
                parts.append(f"#{block.block_id}: {format_timecode(total)}")
        self.duration_label.configure(text="Duration: " + "  |  ".join(parts))

    def _run(self) -> None:
        if self._busy:
            return

        selected = self._selected_blocks()
        if not selected:
            messagebox.showwarning("Blocks", "Select at least one block.")
            return

        out_raw = self.output_var.get().strip()
        if not out_raw:
            messagebox.showwarning("Output", "Set the output folder.")
            return
        output_dir = Path(out_raw).expanduser().resolve()

        missing = require_tools()
        if missing:
            messagebox.showerror(
                "Dependencies",
                "Missing tools: " + ", ".join(missing) + "\n\n"
                "Install ffmpeg (includes ffprobe).",
            )
            return

        summary_lines = [f"Will merge {len(selected)} block(s) → {output_dir}"]
        for block in selected:
            summary_lines.append(f"  • {block.block_id}")

        if not messagebox.askokcancel("Confirm", "\n".join(summary_lines)):
            return

        self._set_busy(True)
        self.progress.set(0)
        self._set_status("Starting merge…")
        self._log("---")
        self._log(f"Starting merge of {len(selected)} block(s)")

        def worker() -> None:
            failures = 0
            for index, block in enumerate(selected):
                self._event_queue.put(
                    ("status", f"Block {block.block_id} ({index + 1}/{len(selected)})…")
                )

                def on_stage(
                    stage: str,
                    current: float,
                    total: float,
                    *,
                    _block_id: str = block.block_id,
                ) -> None:
                    pct, label = stage_progress(stage, current, total)
                    overall = (index + pct / 100.0) / len(selected)
                    self._event_queue.put(
                        ("progress", overall, f"#{_block_id}: {label}")
                    )

                def on_notice(msg: str) -> None:
                    self._event_queue.put(("log", f"! {msg}"))

                try:
                    out = merge_block(
                        block,
                        output_dir,
                        on_stage=on_stage,
                        on_notice=on_notice,
                    )
                    self._event_queue.put(
                        ("log", f"✓ Block {block.block_id} → {out}")
                    )
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    self._event_queue.put(
                        ("log", f"✗ Block {block.block_id}: {exc}")
                    )

            self._event_queue.put(("done", failures, len(selected)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                kind = event[0]
                if kind == "durations":
                    _, results, errors, _ids = event
                    self._durations.update(results)
                    for err in errors:
                        self._log(f"Duration: {err}")
                    self._update_duration_label(self._selected_blocks())
                    self._refresh_idle_status()
                elif kind == "status":
                    self._set_status(event[1])
                elif kind == "progress":
                    _, overall, label = event
                    self.progress.set(max(0.0, min(overall, 1.0)))
                    self._set_status(label)
                elif kind == "log":
                    self._log(event[1])
                elif kind == "done":
                    _, failures, total = event
                    self._set_busy(False)
                    if failures:
                        self.progress.set(1.0)
                        self._set_status(f"Finished with {failures} error(s).")
                        self._log(f"Finished with {failures}/{total} failure(s).")
                        messagebox.showwarning(
                            "Result",
                            f"Finished with {failures} failure(s) out of {total}.",
                        )
                    else:
                        self.progress.set(1.0)
                        self._set_status(f"Merge completed ({total} block(s)).")
                        self._log("All blocks processed successfully.")
                        messagebox.showinfo(
                            "Result",
                            f"{total} block(s) processed successfully.",
                        )
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


def main() -> int:
    _set_windows_app_user_model_id()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
