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

STAGE_LABELS_ES = {
    "probe": "Calculando duración",
    "join": "Uniendo capítulos",
    "ffmpeg": "Uniendo capítulos",
    "udtacopy": "Copiando metadatos udta",
    "rename": "Renombrando a .360",
}


def format_size(num_bytes: int) -> str:
    gb = num_bytes / (1024**3)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = num_bytes / (1024**2)
    return f"{mb:.1f} MB"


def stage_progress(stage: str, current: float, total: float) -> tuple[float, str]:
    labels = STAGE_LABELS_ES
    stage_base = {
        "probe": 0.0,
        "join": 5.0,
        "ffmpeg": 5.0,
        "udtacopy": 90.0,
        "rename": 97.0,
    }
    stage_span = {
        "probe": 5.0,
        "join": 85.0,
        "ffmpeg": 85.0,
        "udtacopy": 7.0,
        "rename": 3.0,
    }
    frac = 0.0 if total <= 0 else min(max(current / total, 0.0), 1.0)
    completed = stage_base.get(stage, 0.0) + stage_span.get(stage, 0.0) * frac
    return completed, labels.get(stage, stage)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("gopro-360-merge")
        self.geometry("780x720")
        self.minsize(640, 560)
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
        self.after(100, self._poll_events)

    def _apply_app_icon(self) -> None:
        """Set window / taskbar icon; missing assets are ignored."""
        try:
            if sys.platform == "win32" and APP_ICON_ICO.is_file():
                self.iconbitmap(default=str(APP_ICON_ICO))
            if APP_ICON_PNG.is_file():
                photo = tk.PhotoImage(file=str(APP_ICON_PNG))
                self._icon_images.append(photo)
                self.iconphoto(True, photo)
        except Exception:  # noqa: BLE001 — icon must never block startup
            pass

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # Source folder
        folder_frame = ctk.CTkFrame(self)
        folder_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 6))
        folder_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(folder_frame, text="Carpeta GS*.360 *").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 4)
        )
        self.folder_var = ctk.StringVar(value="")
        self.folder_entry = ctk.CTkEntry(folder_frame, textvariable=self.folder_var)
        self.folder_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(12, 8), pady=(0, 10))
        ctk.CTkButton(
            folder_frame,
            text="Elegir carpeta…",
            width=140,
            command=self._pick_folder,
        ).grid(row=1, column=2, padx=(0, 8), pady=(0, 10))
        ctk.CTkButton(
            folder_frame,
            text="Escanear",
            width=100,
            command=self._scan_folder,
        ).grid(row=1, column=3, padx=(0, 12), pady=(0, 10))

        # Blocks (content-sized, no scroll viewport)
        blocks_outer = ctk.CTkFrame(self)
        blocks_outer.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
        blocks_outer.grid_columnconfigure(0, weight=1)

        blocks_header = ctk.CTkFrame(blocks_outer, fg_color="transparent")
        blocks_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        blocks_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            blocks_header,
            text="Bloques / capítulos *",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            blocks_header,
            text="Seleccionar todos",
            width=140,
            command=self._select_all,
        ).grid(row=0, column=1, padx=4)
        ctk.CTkButton(
            blocks_header,
            text="Ninguno",
            width=90,
            command=self._select_none,
        ).grid(row=0, column=2, padx=(4, 0))

        self.blocks_list = ctk.CTkFrame(blocks_outer, fg_color="transparent")
        self.blocks_list.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.blocks_list.grid_columnconfigure(0, weight=1)
        self._blocks_placeholder = ctk.CTkLabel(
            self.blocks_list,
            text="Elige una carpeta y pulsa Escanear.",
            text_color="gray60",
        )
        self._blocks_placeholder.grid(row=0, column=0, sticky="w", padx=2, pady=4)

        # Output
        opts = ctk.CTkFrame(self)
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        opts.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(opts, text="Salida *").grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 4)
        )
        self.output_var = ctk.StringVar(value="")
        ctk.CTkEntry(opts, textvariable=self.output_var).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=(12, 8), pady=(0, 6)
        )
        ctk.CTkButton(
            opts,
            text="…",
            width=40,
            command=self._pick_output,
        ).grid(row=1, column=2, padx=(0, 12), pady=(0, 6))

        self.duration_label = ctk.CTkLabel(
            opts,
            text="Duración: —",
            text_color="gray70",
        )
        self.duration_label.grid(
            row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 8)
        )

        # Actions
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=16, pady=4)
        actions.grid_columnconfigure(0, weight=1)
        self.run_button = ctk.CTkButton(
            actions,
            text="Ejecutar merge",
            height=36,
            command=self._run,
        )
        self.run_button.grid(row=0, column=0, sticky="ew")

        # Progress + log (takes remaining vertical space)
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=4, column=0, sticky="nsew", padx=16, pady=(6, 16))
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_rowconfigure(2, weight=1)

        self.status_label = ctk.CTkLabel(
            bottom,
            text="Elige una carpeta y pulsa Escanear.",
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self.progress = ctk.CTkProgressBar(bottom)
        self.progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.progress.set(0)

        self.log_box = ctk.CTkTextbox(bottom, height=140)
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
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
                self._set_status("Elige una carpeta y pulsa Escanear.")
            else:
                self._set_status("Sin bloques detectados. Elige otra carpeta o vuelve a escanear.")
            return
        selected = self._selected_blocks()
        if not selected:
            self._set_status("Selecciona uno o más bloques.")
            return
        if not self.output_var.get().strip():
            self._set_status("Indica la carpeta de salida.")
            return
        n = len(selected)
        noun = "bloque" if n == 1 else "bloques"
        self._set_status(f"Listo para merge ({n} {noun}).")

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
        path = filedialog.askdirectory(title="Carpeta con archivos GS*.360")
        if path:
            self.folder_var.set(path)
            self.output_var.set(str(Path(path) / "merged"))
            self._scan_folder()

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Carpeta de salida")
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
            messagebox.showwarning("Carpeta", "Elige una carpeta con archivos GS*.360.")
            self._refresh_idle_status()
            return
        source = Path(raw).expanduser().resolve()
        if not source.is_dir():
            messagebox.showerror("Carpeta", f"No es un directorio:\n{source}")
            self._refresh_idle_status()
            return

        self._set_status("Escaneando…")
        try:
            blocks = scan_directory(source)
        except NotADirectoryError as exc:
            messagebox.showerror("Carpeta", str(exc))
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
                text=f"No se encontraron GS*.360 en {source}",
                text_color="orange",
            ).grid(row=0, column=0, sticky="w", padx=2, pady=4)
            self.duration_label.configure(text="Duración: —")
            self._log(f"Sin bloques en {source}")
            self._refresh_idle_status()
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
            cb.grid(row=i, column=0, sticky="ew", padx=2, pady=2)

        self._log(f"Detectados {len(blocks)} bloque(s) en {source}")
        self._on_selection_change()

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
            self.duration_label.configure(text="Duración: — (ningún bloque seleccionado)")
            self._refresh_idle_status()
            return

        missing = [b for b in selected if b.block_id not in self._durations]
        if not missing:
            self._update_duration_label(selected)
            self._refresh_idle_status()
            return

        self.duration_label.configure(text="Duración: calculando…")
        self._set_status("Calculando duración…")
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
        self.duration_label.configure(text="Duración: " + "  |  ".join(parts))

    def _run(self) -> None:
        if self._busy:
            return

        selected = self._selected_blocks()
        if not selected:
            messagebox.showwarning("Bloques", "Selecciona al menos un bloque.")
            return

        out_raw = self.output_var.get().strip()
        if not out_raw:
            messagebox.showwarning("Salida", "Indica la carpeta de salida.")
            return
        output_dir = Path(out_raw).expanduser().resolve()

        missing = require_tools()
        if missing:
            messagebox.showerror(
                "Dependencias",
                "Faltan herramientas: " + ", ".join(missing) + "\n\n"
                "Instala ffmpeg (incluye ffprobe).",
            )
            return

        summary_lines = [f"Se va a merge {len(selected)} bloque(s) → {output_dir}"]
        for block in selected:
            summary_lines.append(f"  • {block.block_id}")

        if not messagebox.askokcancel("Confirmar", "\n".join(summary_lines)):
            return

        self._set_busy(True)
        self.progress.set(0)
        self._set_status("Iniciando merge…")
        self._log("---")
        self._log(f"Iniciando merge de {len(selected)} bloque(s)")

        def worker() -> None:
            failures = 0
            for index, block in enumerate(selected):
                self._event_queue.put(
                    ("status", f"Bloque {block.block_id} ({index + 1}/{len(selected)})…")
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

                try:
                    out = merge_block(block, output_dir, on_stage=on_stage)
                    self._event_queue.put(
                        ("log", f"✓ Bloque {block.block_id} → {out}")
                    )
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    self._event_queue.put(
                        ("log", f"✗ Bloque {block.block_id}: {exc}")
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
                        self._log(f"Duración: {err}")
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
                        self._set_status(
                            f"Terminado con {failures} error(es)."
                        )
                        self._log(f"Finalizado con {failures}/{total} fallos.")
                        messagebox.showwarning(
                            "Resultado",
                            f"Terminado con {failures} fallo(s) de {total}.",
                        )
                    else:
                        self.progress.set(1.0)
                        self._set_status(
                            f"Merge completado ({total} bloque(s))."
                        )
                        self._log("Todos los bloques procesados correctamente.")
                        messagebox.showinfo(
                            "Resultado",
                            f"{total} bloque(s) procesados correctamente.",
                        )
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
