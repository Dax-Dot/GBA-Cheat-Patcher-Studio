#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
GBA Cheat Patcher Studio v1.0 GUI
International English UI.

v1.0 public release:
- The app starts with the Windows light/dark preference when available, and users can switch themes manually.
- Dead legacy manual-tab code removed (manual cheats live in the dialog).
- open_output_folder is now safe on non-Windows platforms.
- Patch operation runs on a background thread to keep the UI responsive.
- Version strings unified across GUI and engine.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import gba_patch_engine as engine

APP_TITLE = "GBA Cheat Patcher Studio v1.0"
CODE_RE = re.compile(r"^\s*([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{4})\s*$")
HEXISH_RE = re.compile(r"^[0-9A-Fa-f?]{8}\s+[0-9A-Fa-f?]{4}$")


def resource_path(relative: str) -> Path:
    """Resolve files both when run as .py and when bundled by PyInstaller."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent / relative

DEFAULT_CHEAT_DB = resource_path("database/gba_codebreaker_named_db.json")
DEFAULT_NOINTRO_DB = resource_path("database/nointro_gba_db_export_crc.json")
APP_ICON_PNG = resource_path("assets/app_icon.png")
HEADER_LOGO_PNG = resource_path("assets/header_logo.png")
ABOUT_LOGO_PNG = resource_path("assets/about_logo.png")


def enable_windows_dpi_awareness() -> None:
    """Make Tk rendering sharper on Windows high-DPI displays."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


enable_windows_dpi_awareness()


def windows_prefers_dark_mode() -> bool:
    """Best-effort Windows 10/11 app theme detection. Defaults to light mode."""
    if sys.platform != "win32":
        return False
    try:
        import winreg  # type: ignore
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except Exception:
        return False


LIGHT_COLORS = {
    "bg": "#f3f4f6",
    "panel": "#ffffff",
    "text": "#111827",
    "muted": "#4b5563",
    "field": "#ffffff",
    "button": "#eef0f3",
    "button_active": "#d8dce2",
    "accent": "#2563eb",
    "accent_active": "#1d4ed8",
    "accent_text": "#ffffff",
    "border": "#9ca3af",
}

DARK_COLORS = {
    "bg": "#2f3136",
    "panel": "#383b40",
    "text": "#f5f5f5",
    "muted": "#c7c9cc",
    "field": "#303339",
    "button": "#454950",
    "button_active": "#555a63",
    "accent": "#3b82f6",
    "accent_active": "#2563eb",
    "accent_text": "#ffffff",
    "border": "#626772",
}


@dataclass
class ManualAnalysis:
    selected: List[engine.SelectedCheat]
    report_lines: List[str]
    ok_count: int
    unsupported_count: int
    invalid_count: int

    @property
    def has_errors(self) -> bool:
        return self.unsupported_count > 0 or self.invalid_count > 0


def is_supported_manual_code(first_word: int) -> bool:
    return f"{first_word:08X}"[0] in engine.SUPPORTED_TYPES


def manual_message(raw_first: int, value: int) -> Tuple[str, str, int]:
    t = f"{raw_first:08X}"[0]
    addr = raw_first & 0x0FFFFFFF
    if t == "3":
        return t, f"8-bit write to 0x{addr:08X} = 0x{value & 0xFF:02X}", addr
    if t == "8":
        return t, f"16-bit write to 0x{addr:08X} = 0x{value & 0xFFFF:04X}", addr
    if t == "2":
        return t, f"16-bit OR at 0x{addr:08X} with 0x{value & 0xFFFF:04X}", addr
    if t == "6":
        return t, f"16-bit AND at 0x{addr:08X} with 0x{value & 0xFFFF:04X}", addr
    return t, "unsupported", addr


def unsupported_reason(type_char: str) -> str:
    if type_char == "4":
        return "Code starts with 4 — slide/repeated-write code. Not supported yet."
    if type_char == "5":
        return "Code starts with 5 — super/multi-line block code. Not supported yet."
    if type_char in {"7", "A", "B", "C", "F"}:
        return f"Code starts with {type_char} — conditional code. It depends on another line and is not supported yet."
    if type_char == "D":
        return "Code starts with D — button activator code. Not supported yet."
    if type_char in {"0", "1", "9"}:
        return f"Code starts with {type_char} — master/enabler/encryption/metadata. Not used as a normal cheat."
    return f"Code starts with {type_char} — unsupported CodeBreaker type."


def analyze_manual_cheats(text: str) -> ManualAnalysis:
    selected: List[engine.SelectedCheat] = []
    report: List[str] = []
    ok_count = 0
    unsupported_count = 0
    invalid_count = 0
    auto_no = 1
    block_no = 1
    current_title: Optional[str] = None
    block_entries: List[dict] = []

    def flush() -> None:
        nonlocal current_title, block_entries, auto_no, block_no, ok_count, unsupported_count, invalid_count
        title = (current_title or f"Manual Cheat {auto_no}").strip()
        if not block_entries:
            current_title = None
            return
        code_entries = [e for e in block_entries if e.get("kind") == "code"]
        invalid_entries = [e for e in block_entries if e.get("kind") == "invalid"]
        unsupported_entries = [e for e in code_entries if not e.get("supported")]
        supported_entries = [e for e in code_entries if e.get("supported")]
        if invalid_entries:
            invalid_count += len(invalid_entries)
            report.append(f"[INVALID] Cheat {block_no}: {title}")
            report.append("  This cheat will not be patched. Fix the invalid line(s) first.")
            for e in invalid_entries:
                report.append(f"  Line {e['lineno']}: {e['raw']} — {e['reason']}")
            report.append("")
        elif unsupported_entries:
            unsupported_count += len(unsupported_entries)
            report.append(f"[UNSUPPORTED] Cheat {block_no}: {title}")
            report.append("  This whole cheat block will be skipped because at least one line is not supported yet.")
            for e in unsupported_entries:
                report.append(f"  Line {e['lineno']}: {e['normalized']} — {e['reason']}")
            if supported_entries:
                report.append("  Note: some lines look supported alone, but they belong to this unsupported block.")
            report.append("")
        elif supported_entries:
            cheat_lines: List[engine.CheatLine] = []
            report.append(f"[OK] Cheat {block_no}: {title}")
            report.append(f"  Patchable: {len(supported_entries)} direct CodeBreaker write line(s).")
            for e in supported_entries:
                cheat_lines.append(e["cheat_line"])
                ok_count += 1
                report.append(f"  Line {e['lineno']}: {e['normalized']} — code starts with {e['type']}; {e['message']}.")
            selected.append(engine.SelectedCheat(title=title, lines=cheat_lines))
            auto_no += 1
            report.append("")
        block_no += 1
        current_title = None
        block_entries = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        m = CODE_RE.match(line)
        if m:
            first = int(m.group(1), 16)
            value = int(m.group(2), 16)
            type_char = f"{first:08X}"[0]
            normalized = f"{m.group(1).upper()} {m.group(2).upper()}"
            if not is_supported_manual_code(first):
                block_entries.append({
                    "kind": "code", "lineno": lineno, "raw": line, "normalized": normalized,
                    "type": type_char, "supported": False, "reason": unsupported_reason(type_char),
                })
            else:
                t, msg, _ = manual_message(first, value)
                cheat_line = engine.CheatLine(raw=normalized, address=first, value=value, type=t, message=msg)
                block_entries.append({
                    "kind": "code", "lineno": lineno, "raw": line, "normalized": normalized,
                    "type": t, "supported": True, "message": msg, "cheat_line": cheat_line,
                })
            continue
        if "?" in line and HEXISH_RE.match(line):
            block_entries.append({"kind": "invalid", "lineno": lineno, "raw": line, "reason": "replace ?? with a real hexadecimal value before patching"})
            continue
        if HEXISH_RE.match(line) or re.search(r"[0-9A-Fa-f]{6,}", line):
            block_entries.append({"kind": "invalid", "lineno": lineno, "raw": line, "reason": "expected format: XXXXXXXX YYYY"})
            continue
        if block_entries:
            flush()
        current_title = line
    flush()
    if not report:
        invalid_count += 1
        report.append("[ERROR] No manual cheats were entered.")
    return ManualAnalysis(selected, report, ok_count, unsupported_count, invalid_count)


class CheatPatcherGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.dark_mode = windows_prefers_dark_mode()
        self.colors = DARK_COLORS if self.dark_mode else LIGHT_COLORS
        self.configure(bg=self.colors["bg"])
        self._setup_fonts_and_scaling()
        self._setup_styles()
        self.geometry("1240x860")
        self.minsize(1120, 760)

        self.cheat_db = None
        self.nointro_db = None
        self.current_rom_crc: Optional[str] = None
        self.current_rom_header: dict = {}
        self.current_cheat_game: Optional[dict] = None
        self.current_nointro_info: Optional[dict] = None
        self.cheat_items: List[Tuple[int, dict, tk.BooleanVar, int]] = []

        self.rom_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Select a GBA ROM to auto-detect cheats by CRC32.")
        self.db_status_var = tk.StringVar(value="Loading bundled databases...")
        self.manual_status_var = tk.StringVar(value="Paste direct CodeBreaker cheats here, then click Validate.")
        self.manual_text_cache = ""
        self.manual_analysis: Optional[ManualAnalysis] = None
        self.use_manual_cheats = False
        self.last_output_dir: Optional[Path] = None
        self._tk_color_widgets: List[tk.Widget] = []
        self.code_popup = None
        self.code_popup_anchor = None
        self.hover_code_popup = None
        self.hover_code_anchor = None
        self.hover_after_id = None
        self.app_icon_image = None
        self.header_logo_image = None
        self.about_logo_image = None

        self._load_assets()
        self._build_ui()
        self._load_bundled_databases()

    def _setup_fonts_and_scaling(self):
        """Use a modern Windows-friendly font and a sane scaling baseline."""
        self.base_font = ("Segoe UI", 10)
        self.heading_font = ("Segoe UI", 17, "bold")
        self.subheading_font = ("Segoe UI", 11, "bold")
        self.mono_font = ("Consolas", 10)
        try:
            # Let Tk use the display DPI, but keep the baseline from becoming tiny.
            current = float(self.tk.call("tk", "scaling"))
            self.tk.call("tk", "scaling", max(current, 1.25))
        except Exception:
            pass
        self.option_add("*Font", self.base_font)

    def _setup_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        c = self.colors
        style.configure("TFrame", background=c["bg"])
        style.configure("Panel.TFrame", background=c["panel"], relief="flat")
        style.configure("TLabel", background=c["bg"], foreground=c["text"], font=self.base_font)
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=self.base_font)
        style.configure("TLabelframe", background=c["bg"], foreground=c["text"], bordercolor=c["border"], relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text"])
        style.configure("TCheckbutton", background=c["panel"], foreground=c["text"], font=self.base_font)
        style.configure("TEntry", fieldbackground=c["field"], foreground=c["text"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
        style.configure("App.TButton", padding=(13, 8), borderwidth=2, relief="solid", background=c["button"], foreground=c["text"], font=self.base_font, focusthickness=2, focuscolor=c["border"])
        style.map("App.TButton", background=[("active", c["button_active"]), ("pressed", c["button_active"])] )
        style.configure("Tiny.TButton", padding=(8, 4), borderwidth=2, relief="solid", background=c["button"], foreground=c["text"], font=("Segoe UI", 9), focusthickness=1, focuscolor=c["border"])
        style.map("Tiny.TButton", background=[("active", c["button_active"]), ("pressed", c["button_active"])] )
        style.configure("Accent.TButton", padding=(18, 9), borderwidth=2, relief="solid", background=c["accent"], foreground=c["accent_text"], font=("Segoe UI", 10, "bold"), focusthickness=2, focuscolor=c["accent"])
        style.map("Accent.TButton", background=[("active", c["accent_active"]), ("pressed", c["accent_active"])] )
        style.configure("Vertical.TScrollbar", background=c["button"], troughcolor=c["bg"], bordercolor=c["border"])


    def _load_assets(self):
        """Load bundled app images and set the window icon when available."""
        try:
            if APP_ICON_PNG.exists():
                self.app_icon_image = tk.PhotoImage(file=str(APP_ICON_PNG))
                self.iconphoto(True, self.app_icon_image)
        except Exception:
            self.app_icon_image = None
        try:
            if HEADER_LOGO_PNG.exists():
                self.header_logo_image = tk.PhotoImage(file=str(HEADER_LOGO_PNG))
        except Exception:
            self.header_logo_image = None
        try:
            if ABOUT_LOGO_PNG.exists():
                self.about_logo_image = tk.PhotoImage(file=str(ABOUT_LOGO_PNG))
        except Exception:
            self.about_logo_image = None

    def show_about(self):
        """Show a compact About dialog with credits and project notes."""
        dialog = tk.Toplevel(self)
        dialog.title("About GBA Cheat Patcher Studio")
        dialog.transient(self)
        dialog.configure(bg=self.colors["bg"])
        dialog.resizable(False, False)
        try:
            if self.app_icon_image is not None:
                dialog.iconphoto(True, self.app_icon_image)
        except Exception:
            pass

        container = ttk.Frame(dialog, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(container)
        top.pack(fill=tk.X)
        if self.about_logo_image is not None:
            ttk.Label(top, image=self.about_logo_image).pack(side=tk.LEFT, padx=(0, 12))
        title_area = ttk.Frame(top)
        title_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(title_area, text="GBA Cheat Patcher Studio", font=self.heading_font).pack(anchor="w")
        ttk.Label(title_area, text="Version 1.0", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        body = (
            "A small desktop tool for patching supported CodeBreaker cheats "
            "into Game Boy Advance ROMs.\n\n"
            "Cheat data: GameHacking.org\n"
            "ROM metadata: No-Intro.org\n\n"
            "Inspired by GBAATM / GBAATM-Rebirth.\n"
            "Maintainer / Creator: Dax-Dot\n"
            "Created with the help of AI-assisted / vibe coding.\n\n"
            "License: GPL-3.0-only. See LICENSE and ATTRIBUTIONS.md.\n\n"
            "This tool does not include ROMs or BIOS files.\n"
            "Use only with legally obtained backups."
        )
        ttk.Label(container, text=body, justify=tk.LEFT, wraplength=520).pack(anchor="w", pady=(14, 10))
        self.button(container, "Close", dialog.destroy).pack(anchor="e")

        dialog.update_idletasks()
        x = self.winfo_rootx() + max(20, (self.winfo_width() - dialog.winfo_reqwidth()) // 2)
        y = self.winfo_rooty() + max(20, (self.winfo_height() - dialog.winfo_reqheight()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X, pady=(0, 6))
        brand = ttk.Frame(header)
        brand.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if self.header_logo_image is not None:
            ttk.Label(brand, image=self.header_logo_image).pack(side=tk.LEFT, padx=(0, 10))
        title_block = ttk.Frame(brand)
        title_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_block, text="GBA Cheat Patcher Studio", font=self.heading_font).pack(anchor="w")
        ttk.Label(title_block, text="Load a GBA ROM. Matching CodeBreaker cheats appear automatically by CRC32.", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        header_buttons = ttk.Frame(header)
        header_buttons.pack(side=tk.RIGHT)
        self.small_button(header_buttons, "About", self.show_about).pack(side=tk.LEFT, padx=(0, 6))
        self.theme_button = self.small_button(header_buttons, "☀ Light" if self.dark_mode else "☾ Dark", self.toggle_theme)
        self.theme_button.pack(side=tk.LEFT)

        ttk.Label(root, textvariable=self.db_status_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))

        self._build_rom_output(root)
        self._build_auto_tab(root)
        self._build_log(root)

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.colors = DARK_COLORS if self.dark_mode else LIGHT_COLORS
        self._setup_styles()
        self._apply_theme_to_widgets()

    def _apply_theme_to_widgets(self):
        c = self.colors
        self.configure(bg=c["bg"])
        if hasattr(self, "theme_button"):
            self.theme_button.configure(text="☀ Light" if self.dark_mode else "☾ Dark")
        for widget in getattr(self, "_tk_color_widgets", []):
            try:
                if isinstance(widget, tk.Checkbutton):
                    widget.configure(bg=c["panel"], fg=c["text"], selectcolor=c["field"], activebackground=c["panel"], activeforeground=c["text"])
                elif isinstance(widget, tk.Button):
                    widget.configure(bg=c["button"], fg=c["text"], activebackground=c["button_active"], activeforeground=c["text"], highlightbackground=c["border"])
                elif isinstance(widget, tk.Frame):
                    widget.configure(bg=c["panel"])
                elif isinstance(widget, tk.Label):
                    widget.configure(bg=c["panel"], fg=c["text"])
                else:
                    widget.configure(bg=c["field"], fg=c["text"], insertbackground=c["text"])
            except Exception:
                pass
        if hasattr(self, "cheat_canvas"):
            try:
                self.cheat_canvas.configure(bg=c["panel"])
            except Exception:
                pass
        self.update_idletasks()

    def button(self, parent, text, command, accent=False):
        return ttk.Button(parent, text=text, command=command, style="Accent.TButton" if accent else "App.TButton")

    def small_button(self, parent, text, command):
        return ttk.Button(parent, text=text, command=command, style="Tiny.TButton")

    def compact_info_button(self, parent, cheat):
        c = self.colors
        btn = tk.Button(
            parent,
            text="i",
            width=1,
            height=1,
            command=lambda b=None: None,
            font=("Segoe UI", 8, "bold"),
            relief="solid",
            bd=1,
            bg=c["button"],
            fg=c["text"],
            activebackground=c["button_active"],
            activeforeground=c["text"],
            highlightthickness=1,
            highlightbackground=c["border"],
            padx=0,
            pady=0,
            cursor="hand2",
        )
        btn.configure(command=lambda b=btn, ch=cheat: self.toggle_cheat_code_popup(ch, b))
        btn.bind("<Enter>", lambda e, b=btn, ch=cheat: self.show_hover_code_popup(ch, b))
        btn.bind("<Leave>", lambda e: self.schedule_close_hover_code_popup())
        self._tk_color_widgets.append(btn)
        return btn

    def make_cheat_checkbox(self, parent, text, variable):
        # Custom larger checkbox for better visibility on high-DPI displays.
        c = self.colors
        wrap = tk.Frame(parent, bg=c["panel"], cursor="hand2")
        box = tk.Label(
            wrap,
            text="☑" if variable.get() else "☐",
            font=("Segoe UI Symbol", 18),
            width=2,
            anchor="center",
            bg=c["panel"],
            fg=c["text"],
            cursor="hand2",
        )
        label = tk.Label(
            wrap,
            text=text,
            font=("Segoe UI", 11),
            anchor="w",
            bg=c["panel"],
            fg=c["text"],
            cursor="hand2",
        )
        box.pack(side=tk.LEFT, padx=(2, 6), pady=2)
        label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2)

        def refresh(*_):
            try:
                box.configure(text="☑" if variable.get() else "☐")
            except Exception:
                pass

        def toggle(_event=None):
            variable.set(not variable.get())
            refresh()
            self.on_auto_cheat_changed()

        for widget in (wrap, box, label):
            widget.bind("<Button-1>", toggle)
            self._bind_cheat_row_mousewheel(widget)
            self._tk_color_widgets.append(widget)
        try:
            variable.trace_add("write", lambda *_: refresh())
        except Exception:
            pass
        return wrap

    def _build_rom_output(self, root):
        frame = ttk.LabelFrame(root, text="ROM and output")
        frame.pack(fill=tk.X)
        rom_row = ttk.Frame(frame); rom_row.pack(fill=tk.X, padx=6, pady=(6, 3))
        ttk.Label(rom_row, text="GBA ROM:", width=12).pack(side=tk.LEFT)
        ttk.Entry(rom_row, textvariable=self.rom_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.button(rom_row, "Browse ROM", self.browse_rom).pack(side=tk.LEFT, padx=4)
        out_row = ttk.Frame(frame); out_row.pack(fill=tk.X, padx=6, pady=3)
        ttk.Label(out_row, text="Output:", width=12).pack(side=tk.LEFT)
        ttk.Entry(out_row, textvariable=self.output_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.button(out_row, "Choose output", self.browse_output).pack(side=tk.LEFT, padx=4)
        action_row = ttk.Frame(frame); action_row.pack(fill=tk.X, padx=6, pady=(4, 8))
        self.patch_button = self.button(action_row, "Patch ROM", self.patch_rom, accent=True)
        self.patch_button.pack(side=tk.LEFT, ipadx=18, ipady=3)
        self.button(action_row, "Open output folder", self.open_output_folder).pack(side=tk.LEFT, padx=8, ipadx=8, ipady=3)
        self.button(action_row, "Manual Cheats", self.open_manual_dialog).pack(side=tk.LEFT, padx=0, ipadx=8, ipady=3)
        ttk.Label(action_row, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=12)

    def _build_auto_tab(self, root):
        paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=2, pady=(8, 2))
        left = ttk.Frame(paned); right = ttk.Frame(paned)
        paned.add(left, weight=1); paned.add(right, weight=2)
        info_frame = ttk.LabelFrame(left, text="Detected ROM information")
        info_frame.pack(fill=tk.BOTH, expand=True, padx=(0,8), pady=4)
        self.rom_info = tk.Text(info_frame, height=18, wrap=tk.WORD, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="solid", bd=1, font=self.base_font)
        self.rom_info.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._tk_color_widgets.append(self.rom_info)
        self.rom_info.configure(state=tk.DISABLED)
        ttk.Label(info_frame, text="Matching: full ROM CRC32\nCheats source: GameHacking.org\nROM metadata: No-Intro.org\nNo match? Use Manual Cheats.", wraplength=390, style="Muted.TLabel").pack(anchor="w", padx=6, pady=(0,6))

        cheats_frame = ttk.LabelFrame(right, text="Supported cheats for this ROM")
        cheats_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        tools = ttk.Frame(cheats_frame); tools.pack(fill=tk.X, padx=6, pady=6)
        self.button(tools, "Select all", self.select_all_cheats).pack(side=tk.LEFT, padx=2)
        self.button(tools, "Clear", self.clear_cheats).pack(side=tk.LEFT, padx=2)
        ttk.Label(tools, text="Only direct CodeBreaker codes starting with 3, 8, 2 or 6 are selectable.").pack(side=tk.LEFT, padx=12)
        self.cheat_canvas = tk.Canvas(cheats_frame, highlightthickness=0, bg=self.colors["panel"])
        self.cheat_scroll = ttk.Scrollbar(cheats_frame, orient=tk.VERTICAL, command=self.cheat_canvas.yview)
        self.cheat_inner = ttk.Frame(self.cheat_canvas)
        self.cheat_inner.bind("<Configure>", lambda e: self.cheat_canvas.configure(scrollregion=self.cheat_canvas.bbox("all")))
        self.cheat_canvas_window = self.cheat_canvas.create_window((0,0), window=self.cheat_inner, anchor="nw")
        self.cheat_canvas.configure(yscrollcommand=self.cheat_scroll.set)
        self.cheat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6,0), pady=(0,6))
        self.cheat_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0,6), pady=(0,6))
        self.cheat_canvas.bind("<Configure>", lambda e: self.cheat_canvas.itemconfigure(self.cheat_canvas_window, width=e.width))
        self._enable_mousewheel_for_cheats()

    def _enable_mousewheel_for_cheats(self):
        def _scroll(event):
            # Make each physical mouse-wheel notch visibly scroll the checkbox list.
            if getattr(event, "num", None) == 4:
                amount = -5
            elif getattr(event, "num", None) == 5:
                amount = 5
            else:
                delta = getattr(event, "delta", 0)
                if delta == 0:
                    amount = 0
                else:
                    amount = -5 if delta > 0 else 5
            if amount:
                self.cheat_canvas.yview_scroll(amount, "units")
            return "break"

        self._cheat_wheel_handler = _scroll
        self.cheat_canvas.bind("<MouseWheel>", _scroll)
        self.cheat_canvas.bind("<Button-4>", _scroll)
        self.cheat_canvas.bind("<Button-5>", _scroll)
        self.cheat_inner.bind("<MouseWheel>", _scroll)
        self.cheat_inner.bind("<Button-4>", _scroll)
        self.cheat_inner.bind("<Button-5>", _scroll)

    def _build_log(self, root):
        log_frame=ttk.LabelFrame(root, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=False, pady=(8,0))
        self.log_box=tk.Text(log_frame, height=4, wrap=tk.WORD, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="solid", bd=1, font=self.base_font)
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._tk_color_widgets.append(self.log_box)
        self.log_box.configure(state=tk.DISABLED)

    def log(self, msg: str):
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state=tk.DISABLED)

    def set_rom_info(self, lines: List[str]):
        self.rom_info.configure(state=tk.NORMAL)
        self.rom_info.delete("1.0", tk.END)
        self.rom_info.insert(tk.END, "\n".join(lines))
        self.rom_info.configure(state=tk.DISABLED)

    def _load_bundled_databases(self):
        try:
            with DEFAULT_CHEAT_DB.open("r", encoding="utf-8") as f:
                self.cheat_db=json.load(f)
            with DEFAULT_NOINTRO_DB.open("r", encoding="utf-8") as f:
                self.nointro_db=json.load(f)
            csum=self.cheat_db.get('summary',{})
            nsum=self.nointro_db.get('summary',{})
            self.db_status_var.set(f"Databases loaded: {csum.get('games',0)} cheat games, {nsum.get('unique_crc32',0)} No-Intro CRCs")
            self.log("Bundled databases loaded.")
        except Exception as exc:
            self.db_status_var.set("Database load error")
            messagebox.showerror("Database error", f"Could not load bundled databases:\n{exc}")
            self.log("ERROR loading bundled databases: " + str(exc))

    def browse_rom(self):
        path=filedialog.askopenfilename(title="Select GBA ROM", filetypes=[("GBA ROM", "*.gba"), ("All files", "*.*")])
        if path:
            self.rom_path_var.set(path)
            self.detect_rom()

    def browse_output(self):
        default=self.output_path_var.get().strip() or "patched.gba"
        path=filedialog.asksaveasfilename(title="Save patched ROM", initialfile=Path(default).name, defaultextension=".gba", filetypes=[("GBA ROM", "*.gba"), ("All files", "*.*")])
        if path:
            self.output_path_var.set(path)

    def open_output_folder(self):
        raw=self.output_path_var.get().strip().strip('"')
        folder=Path(raw).expanduser().parent if raw else self.last_output_dir
        if not folder or not folder.exists():
            messagebox.showwarning("Output folder", "No output folder is available yet.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(folder)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror("Output folder", f"Could not open folder:\n{folder}\n\n{exc}")

    def detect_rom(self):
        rom=Path(self.rom_path_var.get().strip().strip('"')).expanduser()
        if not rom.exists():
            messagebox.showerror("ROM not found", f"File does not exist:\n{rom}")
            return
        try:
            data=rom.read_bytes()
            self.current_rom_crc=engine.crc32_file(rom).upper()
            self.current_rom_header=engine.gba_header_info(data)
            self.current_cheat_game=None
            self.current_nointro_info=None
            if self.nointro_db:
                self.current_nointro_info=self.nointro_db.get('by_crc',{}).get(self.current_rom_crc)
            cheat_matches=[]
            if self.cheat_db:
                cheat_matches=self.cheat_db.get('by_crc',{}).get(self.current_rom_crc,[])
            if cheat_matches:
                self.current_cheat_game=cheat_matches[0]
            self.populate_cheats(self.current_cheat_game)
            self.show_detected_info(rom)
            self.update_output_name()
            if self.current_cheat_game:
                supported=len(engine.selectable_cheats(self.current_cheat_game))
                self.status_var.set(f"ROM detected. {supported} supported cheat(s) available.")
            else:
                self.status_var.set("ROM detected, but no CRC-matching cheats were found. Use Manual Cheats if needed.")
        except Exception as exc:
            self.status_var.set("Detection error")
            self.log("ERROR detecting ROM: " + str(exc))
            messagebox.showerror("Detection error", str(exc))

    def _format_region(self, value):
        if isinstance(value, list):
            return ", ".join(str(x) for x in value if x)
        return str(value or "").strip()

    def show_detected_info(self, rom: Path):
        h=self.current_rom_header or {}
        n=self.current_nointro_info or {}
        g=self.current_cheat_game

        title = n.get('name') or (g.get('title') if g else '') or h.get('title','') or 'Unknown'
        region = self._format_region(n.get('region')) or 'Unknown region'
        languages = n.get('languages','')
        if isinstance(languages, list):
            languages = ", ".join(str(x) for x in languages if x)
        serial = n.get('serial') or h.get('game_code','') or ''
        game_code = h.get('game_code','') or serial or 'Unknown'

        lines=[
            "ROM",
            f"File: {rom.name}",
            f"Title: {title}",
            f"CRC32: {self.current_rom_crc or 'unknown'}",
            f"Game code: {game_code}    Region: {region}",
        ]
        if languages:
            lines.append(f"Languages: {languages}")
        if serial and serial != game_code:
            lines.append(f"No-Intro serial: {serial}")
        lines.append("")

        lines.append("Matches")
        if self.current_nointro_info:
            lines.append("ROM metadata: ✅ matched by CRC32")
        else:
            lines.append("ROM metadata: ❌ no CRC32 match")

        if g:
            s=g.get('summary',{})
            supported=s.get('simple_supported',0)
            unsupported=sum(v for k,v in s.items() if k != 'simple_supported')
            lines += [
                "Cheat database: ✅ matched by CRC32",
                f"Cheat title: {g.get('title','')}",
                f"Available cheats: {supported} supported / {unsupported} advanced/unsupported",
            ]
        else:
            lines += [
                "Cheat database: ❌ no CRC32 match",
                "Tip: use Manual Cheats if you found CodeBreaker codes online.",
            ]

        lines += [
            "",
            "Sources",
            "Cheats: GameHacking.org",
            "ROM metadata: No-Intro.org",
        ]

        if g:
            warn=engine.partial_warning_for_title(g.get('title',''))
            if warn:
                lines += ["", "Compatibility warning", warn]
        self.set_rom_info(lines)

    def _cheat_code_text(self, cheat: dict) -> str:
        codes = []
        for line in cheat.get("lines", []):
            raw = str(line.get("raw", "")).strip()
            if raw:
                codes.append(raw)
        return "\n".join(codes) if codes else "No code lines available."


    def close_hover_code_popup(self):
        if self.hover_after_id is not None:
            try:
                self.after_cancel(self.hover_after_id)
            except Exception:
                pass
            self.hover_after_id = None
        if self.hover_code_popup is not None:
            try:
                self.hover_code_popup.destroy()
            except Exception:
                pass
        self.hover_code_popup = None
        self.hover_code_anchor = None

    def schedule_close_hover_code_popup(self):
        if self.hover_after_id is not None:
            try:
                self.after_cancel(self.hover_after_id)
            except Exception:
                pass
        self.hover_after_id = self.after(150, self.close_hover_code_popup)

    def show_hover_code_popup(self, cheat: dict, anchor_widget):
        # Do not show the lightweight hover preview if the pinned popup is open for this button.
        if self.code_popup is not None and self.code_popup_anchor is anchor_widget:
            return
        self.close_hover_code_popup()
        code_text = self._cheat_code_text(cheat)
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=self.colors["border"])
        self.hover_code_popup = popup
        self.hover_code_anchor = anchor_widget

        outer = tk.Frame(popup, bg=self.colors["border"], padx=1, pady=1)
        outer.pack(fill=tk.BOTH, expand=True)
        label = tk.Label(
            outer,
            text=code_text,
            justify=tk.LEFT,
            bg=self.colors["field"],
            fg=self.colors["text"],
            font=self.mono_font,
            padx=8,
            pady=6,
        )
        label.pack(fill=tk.BOTH, expand=True)
        # Keep open if mouse moves from the i button into the hover popup.
        popup.bind("<Enter>", lambda e: self.after_cancel(self.hover_after_id) if self.hover_after_id else None)
        popup.bind("<Leave>", lambda e: self.schedule_close_hover_code_popup())

        popup.update_idletasks()
        w = popup.winfo_reqwidth()
        h = popup.winfo_reqheight()
        ax = anchor_widget.winfo_rootx()
        ay = anchor_widget.winfo_rooty()
        ah = anchor_widget.winfo_height()
        screen_w = self.winfo_screenwidth()
        x = ax - w - 8
        if x < 8:
            x = ax + anchor_widget.winfo_width() + 8
        if x + w > screen_w - 8:
            x = max(8, screen_w - w - 8)
        y = ay + max(0, (ah - h) // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")

    def close_code_popup(self):
        self.close_hover_code_popup()
        if self.code_popup is not None:
            try:
                self.code_popup.destroy()
            except Exception:
                pass
        self.code_popup = None
        self.code_popup_anchor = None

    def toggle_cheat_code_popup(self, cheat: dict, anchor_widget):
        if self.code_popup is not None and self.code_popup_anchor is anchor_widget:
            self.close_code_popup()
            return
        self.close_code_popup()
        code_text = self._cheat_code_text(cheat)
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=self.colors["border"])
        self.code_popup = popup
        self.code_popup_anchor = anchor_widget

        outer = tk.Frame(popup, bg=self.colors["border"], padx=1, pady=1)
        outer.pack(fill=tk.BOTH, expand=True)
        inner = tk.Frame(outer, bg=self.colors["field"], padx=8, pady=8)
        inner.pack(fill=tk.BOTH, expand=True)

        text = tk.Text(inner, height=max(2, min(6, len(code_text.splitlines()) + 1)), width=30, wrap=tk.NONE,
                       bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"],
                       relief="flat", bd=0, font=self.mono_font)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", code_text)
        text.configure(state=tk.DISABLED)

        btn_row = tk.Frame(inner, bg=self.colors["field"])
        btn_row.pack(fill=tk.X, pady=(6, 0))
        def copy_codes():
            self.clipboard_clear()
            self.clipboard_append(code_text)
        copy_btn = tk.Button(btn_row, text="Copy", command=copy_codes, font=("Segoe UI", 9), relief="solid", bd=1,
                             bg=self.colors["button"], fg=self.colors["text"], activebackground=self.colors["button_active"],
                             activeforeground=self.colors["text"], padx=8, pady=2)
        copy_btn.pack(side=tk.LEFT)
        close_btn = tk.Button(btn_row, text="×", command=self.close_code_popup, font=("Segoe UI", 9, "bold"), relief="solid", bd=1,
                              bg=self.colors["button"], fg=self.colors["text"], activebackground=self.colors["button_active"],
                              activeforeground=self.colors["text"], padx=6, pady=2)
        close_btn.pack(side=tk.RIGHT)

        popup.update_idletasks()
        w = popup.winfo_reqwidth()
        h = popup.winfo_reqheight()
        ax = anchor_widget.winfo_rootx()
        ay = anchor_widget.winfo_rooty()
        ah = anchor_widget.winfo_height()
        screen_w = self.winfo_screenwidth()
        # Prefer left of the info button so it does not cover the button.
        x = ax - w - 8
        if x < 8:
            x = ax + anchor_widget.winfo_width() + 8
        if x + w > screen_w - 8:
            x = max(8, screen_w - w - 8)
        y = ay + max(0, (ah - h) // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.bind("<Escape>", lambda e: self.close_code_popup())
        popup.bind("<FocusOut>", lambda e: None)

    def _bind_cheat_row_mousewheel(self, widget):
        try:
            widget.bind("<MouseWheel>", self._cheat_wheel_handler)
            widget.bind("<Button-4>", self._cheat_wheel_handler)
            widget.bind("<Button-5>", self._cheat_wheel_handler)
        except Exception:
            pass

    def populate_cheats(self, game: Optional[dict]):
        self.close_code_popup()
        for child in self.cheat_inner.winfo_children(): child.destroy()
        self.cheat_items.clear()
        if not game:
            ttk.Label(self.cheat_inner, text="No CRC-matching supported cheats found for this ROM. Use Manual Cheats if needed.").pack(anchor="w", padx=6, pady=6)
            return
        items=engine.selectable_cheats(game)
        if not items:
            ttk.Label(self.cheat_inner, text="This ROM matched the database, but it has no supported direct CodeBreaker cheats.").pack(anchor="w", padx=6, pady=6)
            return
        for display_no,(db_idx,ch) in enumerate(items, start=1):
            var=tk.BooleanVar(value=False)
            self.cheat_items.append((db_idx,ch,var,display_no))
            title=ch.get('title', f'Cheat {display_no}')
            line_count=ch.get('simple_supported_lines', ch.get('total_code_lines',0))
            text=f"[{display_no}] {title}  ({line_count} line(s))"
            row = ttk.Frame(self.cheat_inner)
            row.pack(anchor="w", fill=tk.X, padx=6, pady=2)
            cb = self.make_cheat_checkbox(row, text, var)
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
            info_btn = self.compact_info_button(row, ch)
            info_btn.pack(side=tk.RIGHT, padx=(3, 2), ipadx=0, ipady=0)
            self._bind_cheat_row_mousewheel(row)
            self._bind_cheat_row_mousewheel(cb)
            self._bind_cheat_row_mousewheel(info_btn)

    def on_auto_cheat_changed(self):
        if any(var.get() for _,_,var,_ in self.cheat_items):
            self.use_manual_cheats = False
        self.update_output_name()

    def select_all_cheats(self):
        self.use_manual_cheats = False
        for _,_,var,_ in self.cheat_items: var.set(True)
        self.update_output_name()

    def clear_cheats(self):
        for _,_,var,_ in self.cheat_items: var.set(False)
        self.update_output_name()

    def open_manual_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("Manual CodeBreaker Cheats")
        dialog.geometry("980x620")
        dialog.minsize(820, 520)
        dialog.configure(bg=self.colors["bg"])
        dialog.resizable(True, True)
        # Keep this as a normal resizable window so the maximize button is available.

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Manual CodeBreaker Cheats", font=self.subheading_font).pack(anchor="w")
        help_text = (
            "Use this if the loaded ROM was not found in the bundled cheat database, or if you found CodeBreaker cheats online.\n"
            "Enter one cheat title followed by one or more code lines. Separate cheats with a blank line.\n"
            "Supported now: codes starting with 3, 8, 2 or 6. Unsupported: codes starting with 4, 5, 7, A, B, C, D, F, 0, 1 or 9."
        )
        ttk.Label(frame, text=help_text, justify=tk.LEFT, style="Muted.TLabel", wraplength=920).pack(anchor="w", pady=(0, 6))

        example_box = ttk.LabelFrame(frame, text="Example format")
        example_box.pack(fill=tk.X, pady=(0, 8))
        example_text = (
            "Max Money\n"
            "33009883 0022\n\n"
            "Infinite Lives\n"
            "33000FC8 0009"
        )
        ttk.Label(example_box, text=example_text, justify=tk.LEFT, font=self.mono_font).pack(anchor="w", padx=8, pady=6)

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, pady=(0, 8))

        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(paned); right = ttk.Frame(paned)
        paned.add(left, weight=1); paned.add(right, weight=1)
        ttk.Label(left, text="Manual cheats:").pack(anchor="w")
        manual_text = tk.Text(left, height=14, wrap=tk.WORD, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="solid", bd=1, font=self.base_font)
        manual_text.pack(fill=tk.BOTH, expand=True)
        manual_text.insert("1.0", self.manual_text_cache)
        ttk.Label(right, text="Validation report:").pack(anchor="w")
        report_text = tk.Text(right, height=14, wrap=tk.WORD, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="solid", bd=1, font=self.base_font)
        report_text.pack(fill=tk.BOTH, expand=True)
        report_text.configure(state=tk.DISABLED)

        def set_report(lines: List[str]):
            report_text.configure(state=tk.NORMAL)
            report_text.delete("1.0", tk.END)
            report_text.insert(tk.END, "\n".join(lines))
            report_text.configure(state=tk.DISABLED)

        def run_validate(show_popup: bool = True) -> ManualAnalysis:
            self.manual_text_cache = manual_text.get("1.0", tk.END)
            analysis = analyze_manual_cheats(self.manual_text_cache)
            summary = [
                "Manual cheats validation:",
                f"Supported code lines: {analysis.ok_count}",
                f"Unsupported code lines: {analysis.unsupported_count}",
                f"Invalid or needs-value lines: {analysis.invalid_count}",
                "",
            ]
            set_report(summary + analysis.report_lines)
            if show_popup:
                if analysis.has_errors:
                    messagebox.showwarning("Manual validation completed", f"Supported: {analysis.ok_count}\nUnsupported: {analysis.unsupported_count}\nInvalid/needs value: {analysis.invalid_count}", parent=dialog)
                else:
                    messagebox.showinfo("Manual cheats valid", f"Found {len(analysis.selected)} supported manual cheat block(s).", parent=dialog)
            return analysis

        def use_manual():
            analysis = run_validate(show_popup=False)
            if analysis.has_errors or not analysis.selected:
                messagebox.showerror("Manual cheats not ready", "Manual input contains unsupported/invalid cheat blocks, or no supported cheats were found. Check the validation report.", parent=dialog)
                return
            self.manual_analysis = analysis
            self.use_manual_cheats = True
            self.manual_status_var.set(f"Manual cheats active: {len(analysis.selected)} cheat(s).")
            self.clear_cheats()
            self.update_output_name()
            self.status_var.set(f"Manual cheats active: {len(analysis.selected)} cheat(s). Click Patch ROM.")
            dialog.destroy()

        def clear_text():
            manual_text.delete("1.0", tk.END)
            set_report([])
            self.manual_text_cache = ""
            self.manual_analysis = None
            self.use_manual_cheats = False
            self.manual_status_var.set("Manual cheats cleared.")
            self.update_output_name()

        self.button(button_row, "Validate", lambda: run_validate(True)).pack(side=tk.LEFT, padx=2)
        self.button(button_row, "Apply Manual Cheats", use_manual, accent=True).pack(side=tk.LEFT, padx=8)
        self.button(button_row, "Clear", clear_text).pack(side=tk.LEFT, padx=2)
        self.button(button_row, "Close", dialog.destroy).pack(side=tk.RIGHT, padx=2)

        if self.manual_text_cache.strip():
            run_validate(False)

    def current_mode(self) -> str:
        return "manual" if self.use_manual_cheats else "auto"

    def selected_display_ids(self) -> List[int]:
        return [display_no for _,_,var,display_no in self.cheat_items if var.get()]

    def selected_db_indices(self) -> List[int]:
        return [db_idx for db_idx,_,var,_ in self.cheat_items if var.get()]

    def update_output_name(self):
        raw=self.rom_path_var.get().strip().strip('"')
        if not raw: return
        rom=Path(raw)
        if self.current_mode()=="manual":
            analysis = self.manual_analysis or analyze_manual_cheats(self.manual_text_cache)
            ids=list(range(1, len(analysis.selected)+1))
            suffix="CHT.Manual" if not ids else "CHT.Manual." + ".".join(str(x) for x in ids)
        else:
            suffix=engine.default_cheat_suffix(self.selected_display_ids())
        self.output_path_var.set(str(rom.with_name(rom.stem + suffix + ".gba")))

    def get_selected_cheats(self) -> Tuple[List[engine.SelectedCheat], str, str]:
        if self.current_mode()=="manual":
            analysis=self.manual_analysis or analyze_manual_cheats(self.manual_text_cache)
            if analysis.has_errors:
                raise ValueError("Manual input contains unsupported or invalid cheat blocks. Open Manual Cheats and check the validation report.")
            if not analysis.selected:
                raise ValueError("No supported manual CodeBreaker cheat blocks were found. Open Manual Cheats to enter and validate codes.")
            ids=list(range(1, len(analysis.selected)+1))
            return analysis.selected, "Manual CodeBreaker input", ",".join(map(str,ids))
        if not self.current_cheat_game:
            raise ValueError("No CRC-matching cheat database entry is loaded for this ROM. Use the Manual Cheats button if needed.")
        db_ids=self.selected_db_indices(); display_ids=self.selected_display_ids()
        if not db_ids: raise ValueError("Select at least one supported cheat.")
        return engine.make_selected(self.current_cheat_game, db_ids), f"CRC DB game: {self.current_cheat_game.get('title')} [{self.current_cheat_game.get('crc32')}]", ",".join(map(str,display_ids))

    def patch_rom(self):
        rom=Path(self.rom_path_var.get().strip().strip('"')).expanduser()
        if not rom.exists():
            messagebox.showerror("ROM not found", f"File does not exist:\n{rom}")
            return
        out_raw=self.output_path_var.get().strip().strip('"')
        if not out_raw:
            self.update_output_name(); out_raw=self.output_path_var.get().strip().strip('"')
        out=Path(out_raw).expanduser()
        try:
            selected, source_label, ids_label = self.get_selected_cheats()
        except Exception as exc:
            messagebox.showerror("Patch error", str(exc))
            return
        current_rom_crc = self.current_rom_crc
        nointro_name = self.current_nointro_info.get("name", "") if self.current_nointro_info else ""

        self.patch_button.configure(state=tk.DISABLED)
        self.status_var.set("Patching...")

        def _do_patch():
            log: List[str]=[]
            try:
                engine.log_print(log, f"{engine.APP} v1.0 GUI")
                engine.log_print(log, f"Created: {datetime.now().isoformat(timespec='seconds')}")
                engine.log_print(log, source_label)
                engine.log_print(log, f"Selected GUI cheat numbers: {ids_label}")
                if current_rom_crc:
                    engine.log_print(log, f"Detected ROM CRC32: {current_rom_crc}")
                if nointro_name:
                    engine.log_print(log, f"No-Intro match: {nointro_name}")
                engine.log_print(log, f"Recommended profile: {engine.RECOMMENDED_PROFILE_NAME}")
                engine.patch_rom(rom, out, selected, log, vblank=False, execute_every=1, max_hooks=1, hook_indices=[1], skip_early_hooks=False, behavior_profile="constant")
                log_path=out.with_suffix(".patch-log.txt")
                log_path.write_text("\n".join(log)+"\n", encoding="utf-8")
                self.after(0, lambda: self._patch_done(out, log_path))
            except Exception as exc:
                tb = traceback.format_exc()
                self.after(0, lambda e=exc, t=tb: self._patch_error(e, t))

        threading.Thread(target=_do_patch, daemon=True).start()

    def _patch_done(self, out: Path, log_path: Path):
        self.last_output_dir = out.parent
        self.status_var.set("Patch completed successfully")
        self.log(f"OK: {out}")
        self.log(f"Log: {log_path}")
        self.patch_button.configure(state=tk.NORMAL)
        messagebox.showinfo("Done", f"Patched ROM created:\n{out}\n\nLog:\n{log_path}")

    def _patch_error(self, exc: Exception, tb: str):
        self.status_var.set("Error")
        self.log("ERROR: " + str(exc))
        self.log(tb)
        self.patch_button.configure(state=tk.NORMAL)
        messagebox.showerror("Patch error", str(exc))


def main():
    app=CheatPatcherGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
