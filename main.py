"""
Interview Assistant — Main Application
Screen-capture + OCR + LLM workflow.
Computer A (iMac M1): runs this app
Computer B (MacBook): runs a tiny HTTP screenshot server
Optional webcam feed is shown in a separate tab.
"""
import json
import sys
import os
import re
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

from modules.vnc_capture import VNCCapture
from modules.interview_history import InterviewHistoryStore, InterviewRecord
from modules.interview_history_view import InterviewHistoryView
from modules.local_window_capture import LocalWindowCapture, WindowInfo
from modules.webcam_capture import WebcamCapture
from modules.llm_client import (
    DEFAULT_MODEL,
    DEFAULT_OPENAI_MODEL,
    PROVIDER_LOCAL,
    PROVIDER_OPENAI,
    LLMClient,
)
from modules.ocr_module import OCRModule

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT = "#7c3aed"
ACCENT_H = "#8b5cf6"
GREEN = "#16a34a"
GREEN_H = "#22c55e"
RED = "#dc2626"
RED_H = "#ef4444"
ORANGE = "#ea580c"
ORANGE_H = "#f97316"
BG_DARK = "#0d1117"
PANEL_BG = "#161b22"
DEFAULT_LLM_OPTIONS = [DEFAULT_MODEL, "qwen3.2", "phi4-mini", "gemma2:2b"]
SETTINGS_FILE = "interview_assistant_settings.json"
SESSION_LOG_FILE = "interview_session_log.md"
DEFAULT_TEXT_SIZE = 16
MIN_TEXT_SIZE = 12
MAX_TEXT_SIZE = 26
SCREEN_IMAGE_FORMAT = "jpeg"
MANUAL_SCREEN_QUALITY = 70
MANUAL_SCREEN_SCALE = 2.0
MANUAL_SCREEN_MIN_DIMENSION = 1600
LIVE_SCREEN_QUALITY = 45
LIVE_SCREEN_SCALE = 1.2
LIVE_SCREEN_MIN_DIMENSION = 1100
LIVE_SCREEN_REFRESH_MS = 180
DEFAULT_CONTEXT_TEXT = (
    "Example:\n"
    "I'm a Python backend engineer with 5 years of experience.\n"
    "Role: Senior Backend Engineer at Stripe.\n"
    "Skills: Python, FastAPI, PostgreSQL, Redis, AWS."
)
LLM_PROVIDER_LABELS = {
    PROVIDER_LOCAL: "Local LLM (Ollama)",
    PROVIDER_OPENAI: "OpenAI API",
}
LLM_PROVIDER_VALUES = list(LLM_PROVIDER_LABELS.values())
SOURCE_REMOTE_SERVER = "MacBook Server (HTTP)"
SOURCE_LOCAL_WINDOW = "Screen Sharing Window (Local)"
SCREEN_SOURCE_VALUES = [SOURCE_REMOTE_SERVER, SOURCE_LOCAL_WINDOW]
DEFAULT_WINDOW_FILTER = "Screen Sharing, VNC, Remote Desktop"


class InterviewAssistant(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("🎯  Interview Assistant")

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")
        self.minsize(1100, 700)

        self.vnc = VNCCapture()
        self.local_window_capture = LocalWindowCapture()
        self.webcam = WebcamCapture()
        self.llm = LLMClient()
        self.ocr = OCRModule()
        self.history_store = InterviewHistoryStore(
            app_dir=os.path.dirname(os.path.abspath(__file__)),
            markdown_filename=SESSION_LOG_FILE,
        )

        self.ui_text_size = DEFAULT_TEXT_SIZE
        self.current_screen = None
        self.webcam_screen = None
        self.ocr_region = None
        self._region_start = None
        self._selection_rect = None
        self._live_screen_on = False
        self._screen_capture_in_flight = False
        self._screen_source_mode = SOURCE_REMOTE_SERVER
        self._local_window_lookup: dict[str, WindowInfo] = {}
        self._main_panes_initialized = False
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._img_offset_x = 0
        self._img_offset_y = 0
        self.main_text_font = ctk.CTkFont(size=self.ui_text_size)
        self.answer_text_font = ctk.CTkFont(size=self.ui_text_size)

        self._build_ui()
        self._load_settings()
        threading.Thread(target=self._init_models, daemon=True).start()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color=("#1a1a2e", "#1a1a2e"))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(
            hdr,
            text="🎯  Interview Assistant",
            font=ctk.CTkFont(size=21, weight="bold"),
        ).pack(side="left", padx=20)

        self.status_lbl = ctk.CTkLabel(
            hdr,
            text="●  Ready",
            text_color="gray",
            font=ctk.CTkFont(size=12),
        )
        self.status_lbl.pack(side="right", padx=20)

        self.tabs = ctk.CTkTabview(self, corner_radius=8)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.tabs.add("🖥️  Screen + OCR")
        self.tabs.add("📷  Webcam")
        self.tabs.add("🗂️  Saved Interviews")
        self.tabs.add("⚙️  Settings")

        self._build_main_tab()
        self._build_webcam_tab()
        self._build_history_tab()
        self._build_settings_tab()
        self.after(150, self._refresh_saved_interviews)

    def _build_main_tab(self):
        tab = self.tabs.tab("🖥️  Screen + OCR")
        self.main_paned = tk.PanedWindow(
            tab,
            orient=tk.HORIZONTAL,
            sashwidth=8,
            bd=0,
            relief="flat",
            bg="#20262f",
        )
        self.main_paned.pack(fill="both", expand=True, padx=4, pady=2)

        self.left_paned = tk.PanedWindow(
            self.main_paned,
            orient=tk.VERTICAL,
            sashwidth=8,
            bd=0,
            relief="flat",
            bg="#20262f",
        )
        self.right_paned = tk.PanedWindow(
            self.main_paned,
            orient=tk.VERTICAL,
            sashwidth=8,
            bd=0,
            relief="flat",
            bg="#20262f",
        )
        self.main_paned.add(self.left_paned, minsize=360)
        self.main_paned.add(self.right_paned, minsize=320)

        left = ctk.CTkFrame(self.left_paned, fg_color=PANEL_BG, corner_radius=8)
        self.left_paned.add(left, minsize=320)

        tr = ctk.CTkFrame(left, fg_color="transparent")
        tr.pack(fill="x", padx=12, pady=(10, 4))
        self.screen_panel_title_lbl = ctk.CTkLabel(
            tr,
            text="💻  Computer B — Live Screen",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.screen_panel_title_lbl.pack(side="left")
        self.screen_panel_hint_lbl = ctk.CTkLabel(
            tr,
            text="Click & drag on screen to select region",
            text_color="#666",
            font=ctk.CTkFont(size=11),
        )
        self.screen_panel_hint_lbl.pack(side="right")

        canvas_wrap = ctk.CTkFrame(left, fg_color=BG_DARK, corner_radius=6)
        canvas_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        self.screen_canvas = tk.Canvas(
            canvas_wrap,
            bg=BG_DARK,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.screen_canvas.pack(fill="both", expand=True)
        self.screen_canvas.bind("<ButtonPress-1>", self._sel_start)
        self.screen_canvas.bind("<B1-Motion>", self._sel_drag)
        self.screen_canvas.bind("<ButtonRelease-1>", self._sel_end)
        self.screen_canvas.bind("<Configure>", self._on_canvas_resize)

        ctrl = ctk.CTkFrame(left, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=(0, 10))

        self.capture_screen_btn = ctk.CTkButton(
            ctrl,
            text="📷  Capture Screen",
            command=self._refresh_screen_async,
            width=160,
            height=34,
        )
        self.capture_screen_btn.pack(side="left", padx=(0, 6))

        self.live_screen_btn = ctk.CTkButton(
            ctrl,
            text="▶  Start Live",
            fg_color=GREEN,
            hover_color=GREEN_H,
            command=self._start_live_screen,
            width=130,
            height=34,
        )
        self.live_screen_btn.pack(side="left", padx=(0, 6))

        self.stop_live_screen_btn = ctk.CTkButton(
            ctrl,
            text="■  Stop",
            fg_color="#444",
            hover_color="#555",
            command=self._stop_live_screen,
            width=90,
            height=34,
        )
        self.stop_live_screen_btn.pack(side="left", padx=(0, 6))
        self._update_live_screen_controls()
        self._update_screen_source_ui()

        ctk.CTkButton(
            ctrl,
            text="🔍  OCR Selection",
            fg_color=ORANGE,
            hover_color=ORANGE_H,
            command=self._run_ocr,
            width=150,
            height=34,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            ctrl,
            text="✖  Clear",
            fg_color="#444",
            hover_color="#555",
            command=self._clear_selection,
            width=90,
            height=34,
        ).pack(side="left")

        self.keep_ocr_region_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            ctrl,
            text="Keep selection",
            variable=self.keep_ocr_region_var,
        ).pack(side="left", padx=(10, 0))

        screen_log_panel = ctk.CTkFrame(self.left_paned, fg_color=PANEL_BG, corner_radius=8)
        self.left_paned.add(screen_log_panel, minsize=110)

        ctk.CTkLabel(
            screen_log_panel,
            text="📡  Screen Log",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(0, 4))

        self.screen_log_box = ctk.CTkTextbox(
            screen_log_panel,
            height=92,
            font=ctk.CTkFont(size=11),
            wrap="word",
        )
        self.screen_log_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.screen_log_box.insert("0.0", "Screen connection and live capture logs appear here.\n")
        self.screen_log_box.configure(state="disabled")

        ocr_panel = ctk.CTkFrame(self.right_paned, fg_color=PANEL_BG, corner_radius=8)
        self.right_paned.add(ocr_panel, minsize=230)

        ctk.CTkLabel(
            ocr_panel,
            text="📝  Extracted Text  (editable)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))

        self.ocr_box = ctk.CTkTextbox(ocr_panel, height=200, font=self.main_text_font, wrap="word")
        self.ocr_box.pack(fill="both", expand=True, padx=14)
        self.ocr_box.insert(
            "0.0",
            "Start the screenshot server on Computer B, connect, then OCR a selected region.\n"
            "Text appears here — you can edit it before generating.",
        )

        ocr_options_row = ctk.CTkFrame(ocr_panel, fg_color="transparent")
        ocr_options_row.pack(fill="x", padx=14, pady=(6, 4))

        self.generate_with_ocr_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            ocr_options_row,
            text="OCR before generate",
            variable=self.generate_with_ocr_var,
        ).pack(side="left")

        self.ocr_meta_lbl = ctk.CTkLabel(
            ocr_options_row,
            text="No OCR run yet",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        )
        self.ocr_meta_lbl.pack(side="right")

        br = ctk.CTkFrame(ocr_panel, fg_color="transparent")
        br.pack(fill="x", padx=14, pady=8)

        ctk.CTkButton(
            br,
            text="✨  Generate Answer",
            fg_color=ACCENT,
            hover_color=ACCENT_H,
            command=self._gen_answer,
            width=190,
            height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            br,
            text="⚡  Generate Code",
            fg_color=ORANGE,
            hover_color=ORANGE_H,
            command=self._gen_code,
            width=170,
            height=36,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            br,
            text="🗑",
            fg_color="#444",
            hover_color="#555",
            command=lambda: self._clear(self.ocr_box),
            width=42,
            height=36,
        ).pack(side="left")

        answer_panel = ctk.CTkFrame(self.right_paned, fg_color=PANEL_BG, corner_radius=8)
        self.right_paned.add(answer_panel, minsize=260)

        ctk.CTkLabel(
            answer_panel,
            text="🤖  AI Answer",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(4, 4))

        self.answer_box = ctk.CTkTextbox(
            answer_panel,
            font=self.answer_text_font,
            text_color="#f3f4f6",
            wrap="word",
        )
        self.answer_box.pack(fill="both", expand=True, padx=14)
        self.answer_box.insert("0.0", "AI answer / code will stream here…")

        ar = ctk.CTkFrame(answer_panel, fg_color="transparent")
        ar.pack(fill="x", padx=14, pady=(6, 12))

        ctk.CTkButton(
            ar,
            text="📋  Copy",
            command=lambda: self._copy(self.answer_box),
            width=100,
            height=32,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            ar,
            text="🗑  Clear",
            fg_color="#444",
            hover_color="#555",
            command=lambda: self._clear(self.answer_box),
            width=90,
            height=32,
        ).pack(side="left")

        self.answer_meta_lbl = ctk.CTkLabel(
            ar,
            text="No generation yet",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        )
        self.answer_meta_lbl.pack(side="right")

        ctk.CTkLabel(
            answer_panel,
            text="🪵  OCR + LLM Log",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(0, 4))

        self.activity_log_box = ctk.CTkTextbox(
            answer_panel,
            height=96,
            font=ctk.CTkFont(size=11),
            wrap="word",
        )
        self.activity_log_box.pack(fill="x", padx=14, pady=(0, 12))
        self.activity_log_box.insert("0.0", "OCR and generation logs appear here.\n")
        self.activity_log_box.configure(state="disabled")
        tab.after(180, self._init_main_paned_layout)

    def _init_main_paned_layout(self):
        if self._main_panes_initialized:
            return
        try:
            self.update_idletasks()
            main_width = self.main_paned.winfo_width()
            left_height = self.left_paned.winfo_height()
            right_height = self.right_paned.winfo_height()
            if main_width > 200:
                self.main_paned.sash_place(0, int(main_width * 0.5), 0)
            if left_height > 200:
                self.left_paned.sash_place(0, 0, int(left_height * 0.78))
            if right_height > 200:
                self.right_paned.sash_place(0, 0, int(right_height * 0.42))
            self._main_panes_initialized = True
        except tk.TclError:
            self.after(120, self._init_main_paned_layout)

    def _build_webcam_tab(self):
        tab = self.tabs.tab("📷  Webcam")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        panel = ctk.CTkFrame(tab, fg_color=PANEL_BG, corner_radius=8)
        panel.grid(row=0, column=0, sticky="nsew", padx=4, pady=2)

        tr = ctk.CTkFrame(panel, fg_color="transparent")
        tr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            tr,
            text="📷  Webcam Feed",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            tr,
            text="Use this tab for the local camera view",
            text_color="#666",
            font=ctk.CTkFont(size=11),
        ).pack(side="right")

        canvas_wrap = ctk.CTkFrame(panel, fg_color=BG_DARK, corner_radius=6)
        canvas_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        self.webcam_canvas = tk.Canvas(canvas_wrap, bg=BG_DARK, highlightthickness=0)
        self.webcam_canvas.pack(fill="both", expand=True)
        self.webcam_canvas.bind("<Configure>", self._on_webcam_canvas_resize)

        ctrl = ctk.CTkFrame(panel, fg_color="transparent")
        ctrl.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkButton(
            ctrl,
            text="📷  Capture Frame",
            command=self._capture_webcam_async,
            width=160,
            height=34,
        ).pack(side="left", padx=(0, 6))

    def _build_history_tab(self):
        tab = self.tabs.tab("🗂️  Saved Interviews")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.history_view = InterviewHistoryView(tab, on_refresh=self._refresh_saved_interviews)
        self.history_view.grid(row=0, column=0, sticky="nsew")

    def _build_settings_tab(self):
        tab = self.tabs.tab("⚙️  Settings")
        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True, padx=6, pady=6)

        self._section(scroll, "🖥️  Screen Source")
        ctk.CTkLabel(
            scroll,
            text="Choose between the MacBook HTTP server or a local Screen Sharing / VNC window on this iMac.",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        source_row = ctk.CTkFrame(scroll, fg_color="transparent")
        source_row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(source_row, text="Source mode:", width=110, anchor="w").pack(
            side="left", padx=(0, 6)
        )
        self.screen_source_menu = ctk.CTkOptionMenu(
            source_row,
            values=SCREEN_SOURCE_VALUES,
            command=self._on_screen_source_change,
            width=240,
        )
        self.screen_source_menu.pack(side="left", padx=(0, 8))
        self.screen_source_menu.set(SOURCE_REMOTE_SERVER)

        ctk.CTkLabel(
            scroll,
            text=(
                "For the local path, open macOS Screen Sharing or your VNC viewer on this iMac first, "
                "then scan and select that window below."
            ),
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(4, 6))

        local_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        local_grid.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(local_grid, text="Window filter:", width=110, anchor="w").pack(
            side="left", padx=(0, 6)
        )
        self.local_window_filter_entry = ctk.CTkEntry(local_grid, width=320)
        self.local_window_filter_entry.pack(side="left", padx=(0, 8))
        self.local_window_filter_entry.insert(0, DEFAULT_WINDOW_FILTER)

        local_pick_row = ctk.CTkFrame(scroll, fg_color="transparent")
        local_pick_row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(local_pick_row, text="Target window:", width=110, anchor="w").pack(
            side="left", padx=(0, 6)
        )
        self.local_window_menu = ctk.CTkOptionMenu(
            local_pick_row,
            values=["Scan for Screen Sharing windows first"],
            width=420,
        )
        self.local_window_menu.pack(side="left", padx=(0, 8))
        self.local_window_menu.set("Scan for Screen Sharing windows first")

        self.local_window_status = ctk.CTkLabel(
            scroll,
            text="⚫  Local Screen Sharing window not selected",
            text_color="#888",
        )
        self.local_window_status.pack(anchor="w", padx=14, pady=(4, 0))

        local_btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        local_btn_row.pack(anchor="w", padx=14, pady=8)
        ctk.CTkButton(
            local_btn_row,
            text="🔄  Scan Windows",
            command=self._scan_local_windows,
            width=150,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            local_btn_row,
            text="🪟  Use Selected Window",
            fg_color=GREEN,
            hover_color=GREEN_H,
            command=self._connect_local_window,
            width=190,
        ).pack(side="left", padx=(0, 8))

        self._section(scroll, "🌐  Screenshot Server  (Computer B)")
        ctk.CTkLabel(
            scroll,
            text=(
                "Run this on the MacBook:\n"
                "python3 codex/screenshot_http_server.py --host 0.0.0.0 --port 8765 --request-access\n"
                "Then use the MacBook IP address and port below.\n"
                "Make sure both Macs are on the same network and macOS Firewall allows Python/Terminal.\n"
                "If you only see wallpaper, open System Settings -> Privacy & Security -> Screen Recording\n"
                "and allow Python or Terminal on Computer B, then restart the server."
            ),
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        ssh_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        ssh_grid.pack(fill="x", padx=14, pady=4)
        self.ssh_host = self._entry(ssh_grid, "IP Address", "192.168.1.xxx", 0)
        self.ssh_user = self._entry(ssh_grid, "Port", "8765", 1)
        self.ssh_pass = self._entry(
            ssh_grid,
            "Full URL",
            "Optional, e.g. http://192.168.1.5:8765",
            2,
        )

        self.remember_ssh_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            scroll,
            text="Remember server details on this Mac",
            variable=self.remember_ssh_var,
        ).pack(anchor="w", padx=14, pady=(4, 0))

        ctk.CTkLabel(
            scroll,
            text="Saved locally in a small settings file in this app folder.",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(2, 6))

        self.vnc_status = ctk.CTkLabel(scroll, text="⚫  Server not connected", text_color="#888")
        self.vnc_status.pack(anchor="w", padx=14, pady=(4, 0))

        ssh_row = ctk.CTkFrame(scroll, fg_color="transparent")
        ssh_row.pack(anchor="w", padx=14, pady=8)

        ctk.CTkButton(
            ssh_row,
            text="🔌  Connect",
            fg_color=GREEN,
            hover_color=GREEN_H,
            command=self._connect_ssh,
            width=160,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            ssh_row,
            text="💾  Save",
            command=self._save_current_ssh_settings,
            width=120,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            ssh_row,
            text="🧹  Clear Saved",
            fg_color="#444",
            hover_color="#555",
            command=self._clear_saved_ssh_settings,
            width=140,
        ).pack(side="left")

        self._section(scroll, "📷  Webcam")
        ctk.CTkLabel(
            scroll,
            text="Scan to find available cameras. 0 = built-in/default camera, 1+ = external webcam.",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        cam_row = ctk.CTkFrame(scroll, fg_color="transparent")
        cam_row.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(cam_row, text="Camera index:", width=110, anchor="w").pack(
            side="left", padx=(0, 6)
        )

        self.cam_menu = ctk.CTkOptionMenu(cam_row, values=["0", "1", "2", "3"], width=140)
        self.cam_menu.pack(side="left", padx=(0, 8))
        self.cam_menu.set("0")

        ctk.CTkButton(
            cam_row,
            text="🔄  Scan",
            command=self._scan_cameras,
            width=110,
        ).pack(side="left", padx=(0, 8))

        self.remember_webcam_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            scroll,
            text="Remember webcam setting on this Mac",
            variable=self.remember_webcam_var,
        ).pack(anchor="w", padx=14, pady=(4, 0))

        self.cam_status = ctk.CTkLabel(scroll, text="⚫  No camera opened", text_color="#888")
        self.cam_status.pack(anchor="w", padx=14, pady=(4, 0))

        cam_btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        cam_btn_row.pack(anchor="w", padx=14, pady=8)

        ctk.CTkButton(
            cam_btn_row,
            text="📷  Open Camera",
            fg_color=GREEN,
            hover_color=GREEN_H,
            command=self._open_camera,
            width=160,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            cam_btn_row,
            text="💾  Save",
            command=self._save_current_webcam_settings,
            width=120,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            cam_btn_row,
            text="🧹  Clear Saved",
            fg_color="#444",
            hover_color="#555",
            command=self._clear_saved_webcam_settings,
            width=140,
        ).pack(side="left")

        self._section(scroll, "🧠  LLM Provider")
        self.llm_provider_menu = ctk.CTkOptionMenu(
            scroll,
            values=LLM_PROVIDER_VALUES,
            width=260,
            command=self._change_llm_provider,
        )
        self.llm_provider_menu.pack(anchor="w", padx=14)

        self.llm_status = ctk.CTkLabel(scroll, text="⚫  Not tested", text_color="#888")
        self.llm_status.pack(anchor="w", padx=14, pady=(6, 0))
        self.llm_provider_menu.set(LLM_PROVIDER_LABELS[self.llm.provider])

        self._section(scroll, "🤖  Local LLM  (Ollama)")
        ctk.CTkLabel(
            scroll,
            text=(
                "Install: brew install ollama   |   Start: ollama serve\n"
                "Default: llama3.2:3b   |   Options: qwen3.2, phi4-mini, gemma2:2b"
            ),
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.llm_menu = ctk.CTkOptionMenu(
            scroll,
            values=DEFAULT_LLM_OPTIONS,
            width=260,
            command=self._change_llm,
        )
        self.llm_menu.pack(anchor="w", padx=14)
        self.llm_menu.set(self.llm.model)

        self._section(scroll, "🌐  OpenAI API")
        ctk.CTkLabel(
            scroll,
            text="Use this if local Ollama feels slow. Requires your own API key.\nInstall SDK: pip install openai",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        openai_grid = ctk.CTkFrame(scroll, fg_color="transparent")
        openai_grid.pack(fill="x", padx=14, pady=4)
        self.openai_model_entry = self._entry(openai_grid, "OpenAI Model", DEFAULT_OPENAI_MODEL, 0)
        self.openai_model_entry.insert(0, self.llm.openai_model)
        self.openai_api_key_entry = self._entry(openai_grid, "API Key", "sk-...", 1, show="*")

        llm_row = ctk.CTkFrame(scroll, fg_color="transparent")
        llm_row.pack(fill="x", padx=14, pady=8)
        ctk.CTkButton(
            llm_row,
            text="🔄  Refresh Models",
            command=lambda: threading.Thread(target=self._refresh_llm_models, daemon=True).start(),
            width=160,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            llm_row,
            text="💾  Save LLM",
            command=self._save_llm_settings,
            width=130,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            llm_row,
            text="🧪  Test",
            command=self._test_llm,
            width=100,
        ).pack(side="left")

        self._section(scroll, "📄  Your Context  (personalises answers)")
        ctk.CTkLabel(
            scroll,
            text="Paste your resume summary, the job description, or key skills.",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 4))

        self.context_box = ctk.CTkTextbox(scroll, height=140, font=self.main_text_font)
        self.context_box.pack(fill="x", padx=14, pady=(0, 16))
        self.context_box.insert("0.0", DEFAULT_CONTEXT_TEXT)

        context_row = ctk.CTkFrame(scroll, fg_color="transparent")
        context_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkButton(
            context_row,
            text="💾  Save Context",
            command=self._save_context_settings,
            width=140,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            context_row,
            text="↺  Restore Example",
            fg_color="#444",
            hover_color="#555",
            command=self._restore_default_context,
            width=150,
        ).pack(side="left")

        self._section(scroll, "🔎  Display")
        ctk.CTkLabel(
            scroll,
            text="Increase or decrease the text size used in the main text boxes.",
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        display_row = ctk.CTkFrame(scroll, fg_color="transparent")
        display_row.pack(fill="x", padx=14, pady=(0, 12))

        self.text_size_value_lbl = ctk.CTkLabel(
            display_row,
            text=f"{self.ui_text_size}px",
            width=54,
            anchor="w",
        )
        self.text_size_value_lbl.pack(side="right")

        self.text_size_slider = ctk.CTkSlider(
            display_row,
            from_=MIN_TEXT_SIZE,
            to=MAX_TEXT_SIZE,
            number_of_steps=MAX_TEXT_SIZE - MIN_TEXT_SIZE,
            command=self._on_text_size_change,
        )
        self.text_size_slider.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.text_size_slider.set(self.ui_text_size)

    def _section(self, parent, title: str):
        ctk.CTkLabel(
            parent,
            text=title,
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(16, 2))
        ctk.CTkFrame(parent, height=1, fg_color="#333").pack(fill="x", padx=14, pady=(0, 6))

    def _entry(self, grid, label, placeholder, row, show=""):
        ctk.CTkLabel(grid, text=f"{label}:", width=110, anchor="w").grid(
            row=row, column=0, padx=6, pady=4, sticky="w"
        )
        e = ctk.CTkEntry(grid, placeholder_text=placeholder, width=240, show=show)
        e.grid(row=row, column=1, padx=6, pady=4)
        return e

    def _set_status(self, msg: str, color: str = "gray"):
        self.after(0, lambda: self.status_lbl.configure(text=f"●  {msg}", text_color=color))

    def _llm_provider_label(self, provider: str) -> str:
        return LLM_PROVIDER_LABELS.get(provider, LLM_PROVIDER_LABELS[PROVIDER_LOCAL])

    def _llm_provider_value(self, label: str) -> str:
        for provider, provider_label in LLM_PROVIDER_LABELS.items():
            if provider_label == label:
                return provider
        return PROVIDER_LOCAL

    def _sync_llm_settings_from_ui(self):
        self.llm.provider = self._llm_provider_value(self.llm_provider_menu.get())
        self.llm.model = self.llm_menu.get().strip() or DEFAULT_MODEL
        self.llm.openai_model = self.openai_model_entry.get().strip() or DEFAULT_OPENAI_MODEL
        self.llm.openai_api_key = self.openai_api_key_entry.get().strip()

    def _llm_status_text(self) -> tuple[str, str]:
        provider_name = self._llm_provider_label(self.llm.provider)
        model_name = self.llm.openai_model if self.llm.provider == PROVIDER_OPENAI else self.llm.model
        if self.llm.provider == PROVIDER_OPENAI and not self.llm.openai_api_key:
            return "⚠️  OpenAI API selected • add your API key", "orange"
        return f"🟡  {provider_name} selected • model {model_name}", "orange"

    def _update_llm_status_hint(self):
        self._sync_llm_settings_from_ui()
        text, color = self._llm_status_text()
        self.llm_status.configure(text=text, text_color=color)

    def _apply_text_size(self, size: int):
        self.ui_text_size = max(MIN_TEXT_SIZE, min(MAX_TEXT_SIZE, int(size)))
        self.main_text_font.configure(size=self.ui_text_size)
        self.answer_text_font.configure(size=self.ui_text_size)
        if hasattr(self, "text_size_value_lbl"):
            self.text_size_value_lbl.configure(text=f"{self.ui_text_size}px")
        if hasattr(self, "text_size_slider"):
            self.text_size_slider.set(self.ui_text_size)

    def _on_text_size_change(self, value: float):
        self._apply_text_size(round(value))

    def _settings_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILE)

    def _append_session_log(
        self,
        mode: str,
        prompt: str,
        answer: str,
        *,
        token_count: int | None = None,
        duration_seconds: float | None = None,
    ):
        provider_name = self._llm_provider_label(self.llm.provider)
        model_name = self.llm.openai_model if self.llm.provider == PROVIDER_OPENAI else self.llm.model
        record = InterviewRecord(
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            mode=mode,
            question=prompt.strip(),
            answer=answer.strip(),
            provider=provider_name,
            model=model_name,
            token_count=token_count,
            duration_seconds=duration_seconds,
        )
        self.history_store.append_record(record)
        self.after(0, self._refresh_saved_interviews)

    def _refresh_saved_interviews(self):
        records = self.history_store.load_records()
        if hasattr(self, "history_view"):
            self.history_view.render_records(records)

    def _load_settings(self):
        path = self._settings_path()
        if not os.path.exists(path):
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._set_status("Saved settings could not be loaded", ORANGE)
            return

        ssh = data.get("ssh", {})
        ssh_host_value = ssh.get("host", "").strip()
        ssh_user_value = ssh.get("user", "").strip()
        ssh_pass_value = ssh.get("password", "")
        legacy_webcam_index = ""
        if ssh_host_value.isdigit() and not ssh_user_value and not ssh_pass_value:
            legacy_webcam_index = ssh_host_value
            ssh_host_value = ""

        # Migrate old SSH credentials to the new HTTP server layout.
        #if ssh_user_value and not ssh_user_value.isdigit():
        #    ssh_user_value = "8765"
        #if ssh_pass_value and not str(ssh_pass_value).startswith(("http://", "https://")):
        #    ssh_pass_value = ""

        self.remember_ssh_var.set(bool(ssh.get("remember", False)))
        for entry, value in (
            (self.ssh_host, ssh_host_value),
            (self.ssh_user, ssh_user_value),
            (self.ssh_pass, ssh_pass_value),
        ):
            if value:
                entry.delete(0, "end")
                entry.insert(0, value)

        webcam = data.get("webcam", {})
        self.remember_webcam_var.set(bool(webcam.get("remember", False)))
        saved_camera = webcam.get("index", "").strip() or legacy_webcam_index or "0"
        current_values = list(self.cam_menu.cget("values"))
        if saved_camera not in current_values:
            current_values.append(saved_camera)
            self.cam_menu.configure(values=current_values)
        self.cam_menu.set(saved_camera)

        screen_source = data.get("screen_source", {})
        saved_source_mode = screen_source.get("mode", SOURCE_REMOTE_SERVER)
        if saved_source_mode not in SCREEN_SOURCE_VALUES:
            saved_source_mode = SOURCE_REMOTE_SERVER
        self.screen_source_menu.set(saved_source_mode)
        self._screen_source_mode = saved_source_mode

        saved_window_filter = screen_source.get("window_filter", "").strip()
        saved_window_label = screen_source.get("window_label", "").strip()
        self.local_window_filter_entry.delete(0, "end")
        self.local_window_filter_entry.insert(0, saved_window_filter or DEFAULT_WINDOW_FILTER)
        if saved_window_label:
            self.local_window_menu.configure(values=[saved_window_label])
            self.local_window_menu.set(saved_window_label)

        llm = data.get("llm", {})
        provider = llm.get("provider", self.llm.provider)
        local_model = llm.get("local_model", self.llm.model)
        openai_model = llm.get("openai_model", self.llm.openai_model)
        openai_api_key = llm.get("openai_api_key", self.llm.openai_api_key)
        ui = data.get("ui", {})
        text_size = ui.get("text_size", self.ui_text_size)
        candidate_context = data.get("candidate_context", "").strip()

        self.llm_provider_menu.set(self._llm_provider_label(provider))
        self.llm_menu.set(local_model)

        self.openai_model_entry.delete(0, "end")
        self.openai_model_entry.insert(0, openai_model)

        self.openai_api_key_entry.delete(0, "end")
        if openai_api_key:
            self.openai_api_key_entry.insert(0, openai_api_key)

        self.context_box.delete("0.0", "end")
        self.context_box.insert("0.0", candidate_context or DEFAULT_CONTEXT_TEXT)
        self._apply_text_size(text_size)
        self._update_screen_source_ui()
        self._update_llm_status_hint()

    def _save_settings(self):
        self._sync_llm_settings_from_ui()
        path = self._settings_path()
        data = {
            "ssh": {
                "remember": self.remember_ssh_var.get(),
                "host": self.ssh_host.get().strip() if self.remember_ssh_var.get() else "",
                "user": self.ssh_user.get().strip() if self.remember_ssh_var.get() else "",
                "password": self.ssh_pass.get() if self.remember_ssh_var.get() else "",
            },
            "webcam": {
                "remember": self.remember_webcam_var.get(),
                "index": self.cam_menu.get().strip() if self.remember_webcam_var.get() else "",
            },
            "screen_source": {
                "mode": self._current_screen_source_mode(),
                "window_filter": self.local_window_filter_entry.get().strip(),
                "window_label": self.local_window_menu.get().strip(),
            },
            "llm": {
                "provider": self.llm.provider,
                "local_model": self.llm.model,
                "openai_model": self.llm.openai_model,
                "openai_api_key": self.llm.openai_api_key,
            },
            "ui": {"text_size": self.ui_text_size},
            "candidate_context": self.context_box.get("0.0", "end").strip(),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _save_current_ssh_settings(self):
        try:
            self._save_settings()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not save settings:\n{e}")
            return

        if self.remember_ssh_var.get():
            self._set_status("Server settings saved ✓", "green")
        else:
            self._set_status("Saved server settings cleared ✓", "green")

    def _clear_saved_ssh_settings(self):
        self.remember_ssh_var.set(False)
        for entry in (self.ssh_host, self.ssh_user, self.ssh_pass):
            entry.delete(0, "end")

        try:
            self._save_settings()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not clear saved settings:\n{e}")
            return

        self._set_status("Saved server settings cleared ✓", "green")

    def _save_current_webcam_settings(self):
        try:
            self._save_settings()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not save settings:\n{e}")
            return

        if self.remember_webcam_var.get():
            self._set_status("Webcam settings saved ✓", "green")
        else:
            self._set_status("Saved webcam settings cleared ✓", "green")

    def _clear_saved_webcam_settings(self):
        self.remember_webcam_var.set(False)
        self.cam_menu.set("0")

        try:
            self._save_settings()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not clear webcam settings:\n{e}")
            return

        self._set_status("Saved webcam settings cleared ✓", "green")

    def _current_screen_source_mode(self) -> str:
        if hasattr(self, "screen_source_menu"):
            self._screen_source_mode = self.screen_source_menu.get().strip() or SOURCE_REMOTE_SERVER
        return self._screen_source_mode

    def _empty_screen_message(self) -> str:
        if self._current_screen_source_mode() == SOURCE_LOCAL_WINDOW:
            return "No Screen Sharing window captured\nSettings -> Scan Windows -> Use Selected Window"
        return "No screen captured\nSettings -> Connect Server -> Capture Screen"

    def _update_screen_source_ui(self):
        source_mode = self._current_screen_source_mode()
        if hasattr(self, "screen_panel_title_lbl"):
            title = "🪟  Screen Sharing / VNC Window" if source_mode == SOURCE_LOCAL_WINDOW else "💻  Computer B — Live Screen"
            self.screen_panel_title_lbl.configure(text=title)
        if hasattr(self, "screen_panel_hint_lbl"):
            hint = (
                "Click & drag on the local shared-screen window to select region"
                if source_mode == SOURCE_LOCAL_WINDOW
                else "Click & drag on screen to select region"
            )
            self.screen_panel_hint_lbl.configure(text=hint)
        if hasattr(self, "screen_canvas") and not self.current_screen:
            self._show_empty_screen_message()

    def _on_screen_source_change(self, _choice=None):
        was_live = self._live_screen_on
        if was_live:
            self._stop_live_screen()
        self._update_screen_source_ui()
        self._append_screen_log(f"Screen source switched to {self._current_screen_source_mode()}.")
        if was_live:
            self._set_status("Screen source changed — live stopped", ORANGE)

    def _show_empty_screen_message(self):
        width = max(10, self.screen_canvas.winfo_width())
        height = max(10, self.screen_canvas.winfo_height())
        self.screen_canvas.delete("all")
        self.screen_canvas.create_text(
            width // 2,
            height // 2,
            text=self._empty_screen_message(),
            fill="#444",
            font=("Helvetica", 16),
            justify="center",
        )

    def _filter_local_windows(self, windows: list[WindowInfo], filter_text: str) -> list[WindowInfo]:
        tokens = [token.strip().lower() for token in filter_text.split(",") if token.strip()]
        if not tokens:
            return windows

        matches = []
        for window in windows:
            haystack = f"{window.app_name} {window.title}".lower()
            if any(token in haystack for token in tokens):
                matches.append(window)

        return matches or windows

    #def _window_label(self, window: WindowInfo) -> str:
    #    return window.label()

    def _scan_local_windows(self):
        filter_text = self.local_window_filter_entry.get().strip()
        self.local_window_status.configure(text="🟡  Scanning local windows…", text_color="orange")

        def _do():
            ok, msg = self.local_window_capture.connect()
            if not ok:
                self.after(
                    0,
                    lambda: self.local_window_status.configure(text=f"🔴  {msg}", text_color=RED),
                )
                return

            windows = self.local_window_capture.list_windows()
            filtered = self._filter_local_windows(windows, filter_text)
            lookup: dict[str, WindowInfo] = {}
            labels: list[str] = []
            for window in filtered:
                label = self._window_label(window)
                if label in lookup:
                    label = f"{label} [id {window.window_id or window.pid}]"
                lookup[label] = window
                labels.append(label)

            def _update():
                self._local_window_lookup = lookup
                if labels:
                    self.local_window_menu.configure(values=labels)
                    self.local_window_menu.set(labels[0])
                    self.local_window_status.configure(
                        text=f"🟢  Found {len(labels)} candidate window(s)",
                        text_color="green",
                    )
                else:
                    placeholder = "No matching windows found"
                    self.local_window_menu.configure(values=[placeholder])
                    self.local_window_menu.set(placeholder)
                    self.local_window_status.configure(text="🔴  No matching windows found", text_color=RED)

            self.after(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    def _connect_local_window(self):
        selected = self.local_window_menu.get().strip()
        window = self._local_window_lookup.get(selected)
        if not window:
            messagebox.showwarning("Local Window", "Scan and select a Screen Sharing / VNC window first.")
            return

        ok, msg = self.local_window_capture.connect()
        if not ok:
            self.local_window_status.configure(text=f"🔴  {msg}", text_color=RED)
            return

        self.local_window_capture.set_target_window(window)
        self.screen_source_menu.set(SOURCE_LOCAL_WINDOW)
        self._on_screen_source_change()
        self.local_window_status.configure(text=f"🟢  Using {window.label()}", text_color="green")
        self._append_screen_log(f"Local Screen Sharing window selected: {window.label()}")
        try:
            self._save_settings()
        except Exception:
            pass
        self._set_status("Local Screen Sharing window ready", "green")
        self.after(250, lambda: self._refresh_screen_async(show_warning=False))

    def _save_llm_settings(self):
        try:
            self._save_settings()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not save LLM settings:\n{e}")
            return

        text, _color = self._llm_status_text()
        self.llm_status.configure(text=text.replace("🟡", "✅", 1), text_color="green")
        self._set_status("LLM settings saved ✓", "green")

    def _save_context_settings(self):
        try:
            self._save_settings()
        except Exception as e:
            messagebox.showerror("Settings", f"Could not save context:\n{e}")
            return

        self._set_status("Context saved ✓", "green")

    def _restore_default_context(self):
        self.context_box.delete("0.0", "end")
        self.context_box.insert("0.0", DEFAULT_CONTEXT_TEXT)
        self._set_status("Example context restored", "gray")

    def _clear(self, box):
        box.delete("0.0", "end")

    def _copy(self, box):
        text = box.get("0.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Copied ✓", "green")

    def _append(self, box, text):
        box.insert("end", text)
        box.see("end")

    def _append_box_line(self, box, text: str):
        box.configure(state="normal")
        box.insert("end", text.rstrip() + "\n")
        box.see("end")
        box.configure(state="disabled")

    def _append_screen_log(self, text: str):
        if hasattr(self, "screen_log_box"):
            self.after(0, lambda msg=text: self._append_box_line(self.screen_log_box, msg))

    def _append_activity_log(self, text: str):
        if hasattr(self, "activity_log_box"):
            self.after(0, lambda msg=text: self._append_box_line(self.activity_log_box, msg))

    def _set_ocr_meta(self, text: str):
        if hasattr(self, "ocr_meta_lbl"):
            self.after(0, lambda: self.ocr_meta_lbl.configure(text=text))

    def _set_answer_meta(self, text: str):
        if hasattr(self, "answer_meta_lbl"):
            self.after(0, lambda: self.answer_meta_lbl.configure(text=text))

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        pieces = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
        return len(pieces)

    def _draw_selection_overlay(self):
        if self._selection_rect:
            self.screen_canvas.delete(self._selection_rect)
            self._selection_rect = None

        if not self.ocr_region or not self.current_screen:
            return

        x, y, w, h = self.ocr_region
        if w <= 0 or h <= 0:
            return

        sx = self._scale_x or 1.0
        sy = self._scale_y or 1.0

        x1 = self._img_offset_x + int(x * sx)
        y1 = self._img_offset_y + int(y * sy)
        x2 = self._img_offset_x + int((x + w) * sx)
        y2 = self._img_offset_y + int((y + h) * sy)

        self._selection_rect = self.screen_canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            outline="#f97316",
            width=2,
            dash=(5, 3),
        )

    def _on_canvas_resize(self, event):
        if self.current_screen:
            self._render_screen()
        else:
            self._show_empty_screen_message()

    def _on_webcam_canvas_resize(self, event):
        if self.webcam_screen:
            self._render_webcam()
        else:
            self.webcam_canvas.delete("all")
            self.webcam_canvas.create_text(
                event.width // 2,
                event.height // 2,
                text="No webcam frame yet\nSettings → Open Camera",
                fill="#444",
                font=("Helvetica", 16),
                justify="center",
            )

    def _init_models(self):
        self._set_status("Checking LLM…", "orange")
        self._refresh_llm_models()
        self.llm.warmup()
        self._set_status("Ready", "gray")

    def _connect_ssh(self):
        host = self.ssh_pass.get().strip() or self.ssh_host.get().strip()
        port = self.ssh_user.get().strip() or "8765"

        if not host:
            messagebox.showerror("Server", "Enter the IP address or full URL of Computer B.")
            return

        self.vnc_status.configure(text="🟡  Connecting…", text_color="orange")
        self._append_screen_log(f"Connecting to MacBook server at {host}:{port}…")

        def _do():
            ok, msg = self.vnc.connect(host, port, "")
            if ok:
                self.local_window_capture.disconnect()
                self.screen_source_menu.set(SOURCE_REMOTE_SERVER)
                self._on_screen_source_change()
                try:
                    self._save_settings()
                except Exception as e:
                    self.after(
                        0,
                        lambda err=e: self._set_status(
                            f"Connected, but settings not saved: {err}",
                            ORANGE,
                        ),
                    )

                self.after(
                    0,
                    lambda: self.vnc_status.configure(
                        text=f"🟢  {msg}",
                        text_color="green",
                    ),
                )
                self._set_status("Screen server connected", "green")
                self._append_screen_log(msg)
                self.after(300, lambda: self._refresh_screen_async(show_warning=False))
            else:
                self.after(
                    0,
                    lambda: self.vnc_status.configure(text=f"🔴  {msg}", text_color=RED),
                )
                self._set_status("Screen server failed", RED)
                self._append_screen_log(msg)

        threading.Thread(target=_do, daemon=True).start()

    def _open_camera(self):
        idx_str = self.cam_menu.get().strip()
        try:
            idx = int(idx_str.split()[0])
        except (ValueError, IndexError):
            idx = 0

        self.cam_status.configure(text="🟡  Opening…", text_color="orange")

        def _do():
            ok, msg = self.webcam.connect(idx)
            if ok:
                try:
                    self._save_settings()
                except Exception:
                    pass
                self.after(
                    0,
                    lambda: self.cam_status.configure(
                        text=f"🟢  Camera {idx} ready",
                        text_color="green",
                    ),
                )
                self._set_status(f"Camera {idx} opened", "green")
                self.after(300, self._capture_webcam_async)
            else:
                self.after(
                    0,
                    lambda: self.cam_status.configure(text=f"🔴  {msg}", text_color=RED),
                )
                self._set_status("Camera open failed", RED)

        threading.Thread(target=_do, daemon=True).start()

    def _scan_cameras(self):
        self.cam_status.configure(text="🟡  Scanning…", text_color="orange")

        def _do():
            found = self.webcam.list_cameras()
            if found:
                labels = [str(i) for i in found]
                self.after(0, lambda: self.cam_menu.configure(values=labels))
                self.after(0, lambda: self.cam_menu.set(labels[0]))
                self.after(
                    0,
                    lambda: self.cam_status.configure(
                        text=f"✅  Found cameras: {found}",
                        text_color="green",
                    ),
                )
            else:
                self.after(
                    0,
                    lambda: self.cam_status.configure(text="❌  No cameras found", text_color=RED),
                )

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_screen_async(self, *, show_warning=True):
        source_mode = self._current_screen_source_mode()
        if source_mode == SOURCE_LOCAL_WINDOW:
            if not self.local_window_capture.connected:
                if show_warning:
                    messagebox.showwarning(
                        "Local Window",
                        "Go to Settings, scan Screen Sharing windows, and use the selected window first.",
                    )
                return
        elif not self.vnc.connected:
            if show_warning:
                messagebox.showwarning(
                    "Not connected",
                    "Go to Settings and connect to Computer B first.",
                )
            return
        if self._screen_capture_in_flight:
            return
        self._screen_capture_in_flight = True
        if not self._live_screen_on:
            self._append_screen_log("Capturing screen…")
        threading.Thread(target=self._do_capture, daemon=True).start()

    def _update_live_screen_controls(self):
        if not hasattr(self, "live_screen_btn") or not hasattr(self, "stop_live_screen_btn"):
            return
        if self._live_screen_on:
            self.live_screen_btn.configure(state="disabled")
            self.stop_live_screen_btn.configure(state="normal")
        else:
            self.live_screen_btn.configure(state="normal")
            self.stop_live_screen_btn.configure(state="disabled")

    def _start_live_screen(self):
        source_mode = self._current_screen_source_mode()
        if source_mode == SOURCE_LOCAL_WINDOW and not self.local_window_capture.connected:
            messagebox.showwarning(
                "Local Window",
                "Go to Settings, scan Screen Sharing windows, and use the selected window first.",
            )
            return
        if source_mode == SOURCE_REMOTE_SERVER and not self.vnc.connected:
            messagebox.showwarning(
                "Not connected",
                "Go to Settings and connect to Computer B first.",
            )
            return
        if self._live_screen_on:
            return
        self._live_screen_on = True
        self._update_live_screen_controls()
        self._set_status("Live screen started", "green")
        self._append_screen_log("Live screen started.")
        self._refresh_screen_async()
        self.after(LIVE_SCREEN_REFRESH_MS, self._live_screen_loop)

    def _stop_live_screen(self):
        if not self._live_screen_on:
            return
        self._live_screen_on = False
        self._update_live_screen_controls()
        self._set_status("Live screen stopped", "gray")
        self._append_screen_log("Live screen stopped.")

    def _live_screen_loop(self):
        if not self._live_screen_on:
            return
        if self._current_screen_source_mode() == SOURCE_REMOTE_SERVER and not self.vnc.connected:
            self._stop_live_screen()
            self._set_status("Live screen stopped: server disconnected", ORANGE)
            self._append_screen_log("Live screen stopped because remote server disconnected.")
            return
        if not self._screen_capture_in_flight:
            self._refresh_screen_async(show_warning=False)
        self.after(LIVE_SCREEN_REFRESH_MS, self._live_screen_loop)

    def _do_capture(self):
        try:
            if self._current_screen_source_mode() == SOURCE_LOCAL_WINDOW:
                img = self.local_window_capture.capture_screen()
            else:
                cw = max(1, self.screen_canvas.winfo_width())
                ch = max(1, self.screen_canvas.winfo_height())
                if self._live_screen_on:
                    request_width = max(LIVE_SCREEN_MIN_DIMENSION, int(cw * LIVE_SCREEN_SCALE))
                    request_height = max(LIVE_SCREEN_MIN_DIMENSION, int(ch * LIVE_SCREEN_SCALE))
                    quality = LIVE_SCREEN_QUALITY
                else:
                    request_width = max(MANUAL_SCREEN_MIN_DIMENSION, int(cw * MANUAL_SCREEN_SCALE))
                    request_height = max(MANUAL_SCREEN_MIN_DIMENSION, int(ch * MANUAL_SCREEN_SCALE))
                    quality = MANUAL_SCREEN_QUALITY

                img = self.vnc.capture_screen(
                    max_width=request_width,
                    max_height=request_height,
                    image_format=SCREEN_IMAGE_FORMAT,
                    quality=quality,
                )
            if img:
                self.current_screen = img
                self.after(0, self._render_screen)
                if not self._live_screen_on:
                    self.after(0, lambda: self._set_status("Screen captured ✓", "green"))
                    self._append_screen_log("Screen captured successfully.")
            else:
                if self._live_screen_on:
                    self.after(0, lambda: self._set_status("Live screen: dropped frame, retrying…", ORANGE))
                    self._append_screen_log("Live screen dropped a frame and is retrying.")
                else:
                    self.after(0, lambda: self._set_status("Capture failed", RED))
                    self._append_screen_log("Screen capture failed.")
        finally:
            self._screen_capture_in_flight = False

    def _capture_webcam_async(self):
        if not self.webcam.connected:
            messagebox.showwarning("No camera", "Go to Settings and open a camera first.")
            return
        self._set_status("Capturing webcam…", "orange")
        threading.Thread(target=self._do_webcam_capture, daemon=True).start()

    def _do_webcam_capture(self):
        img = self.webcam.capture_screen()
        if img:
            self.webcam_screen = img
            self.after(0, self._render_webcam)
            self.after(0, lambda: self._set_status("Webcam frame captured ✓", "green"))
        else:
            self.after(0, lambda: self._set_status("Webcam capture failed", RED))

    def _render_screen(self):
        if not self.current_screen:
            return
        cw = self.screen_canvas.winfo_width()
        ch = self.screen_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        img = self.current_screen.copy()
        img.thumbnail((cw, ch), Image.BILINEAR)

        self._scale_x = img.width / self.current_screen.width
        self._scale_y = img.height / self.current_screen.height
        self._img_offset_x = (cw - img.width) // 2
        self._img_offset_y = (ch - img.height) // 2

        photo = ImageTk.PhotoImage(img)
        self.screen_canvas.delete("all")
        self.screen_canvas.create_image(
            self._img_offset_x,
            self._img_offset_y,
            image=photo,
            anchor="nw",
        )
        self.screen_canvas._photo = photo
        self._draw_selection_overlay()

    def _render_webcam(self):
        if not self.webcam_screen:
            return
        cw = self.webcam_canvas.winfo_width()
        ch = self.webcam_canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        img = self.webcam_screen.copy()
        img.thumbnail((cw, ch), Image.BILINEAR)

        ox = (cw - img.width) // 2
        oy = (ch - img.height) // 2

        photo = ImageTk.PhotoImage(img)
        self.webcam_canvas.delete("all")
        self.webcam_canvas.create_image(ox, oy, image=photo, anchor="nw")
        self.webcam_canvas._photo = photo

    def _sel_start(self, event):
        self._region_start = (event.x, event.y)
        if self._selection_rect:
            self.screen_canvas.delete(self._selection_rect)
            self._selection_rect = None

    def _sel_drag(self, event):
        if not self._region_start:
            return
        if self._selection_rect:
            self.screen_canvas.delete(self._selection_rect)
        x0, y0 = self._region_start
        self._selection_rect = self.screen_canvas.create_rectangle(
            x0,
            y0,
            event.x,
            event.y,
            outline="#f97316",
            width=2,
            dash=(5, 3),
        )

    def _sel_end(self, event):
        if not self._region_start or not self.current_screen:
            self._region_start = None
            return

        x1, y1 = self._region_start
        x2, y2 = event.x, event.y
        self._region_start = None

        x1, x2 = sorted([x1, x2])
        y1, y2 = sorted([y1, y2])

        x1 -= self._img_offset_x
        x2 -= self._img_offset_x
        y1 -= self._img_offset_y
        y2 -= self._img_offset_y

        sx = self._scale_x or 1.0
        sy = self._scale_y or 1.0
        ax1 = max(0, int(x1 / sx))
        ay1 = max(0, int(y1 / sy))
        ax2 = max(0, int(x2 / sx))
        ay2 = max(0, int(y2 / sy))

        self.ocr_region = (ax1, ay1, ax2 - ax1, ay2 - ay1)
        self._draw_selection_overlay()

    def _clear_selection(self):
        if self._selection_rect:
            self.screen_canvas.delete(self._selection_rect)
            self._selection_rect = None
        self.ocr_region = None

    def _run_ocr(self):
        self._run_ocr_workflow()

    def _run_ocr_workflow(self, on_complete=None):
        if not self.current_screen:
            messagebox.showwarning("OCR", "Capture the screen first.")
            return
        if not self.ocr_region or self.ocr_region[2] < 5 or self.ocr_region[3] < 5:
            messagebox.showwarning("OCR", "Drag to select a region first.")
            return

        self._set_status("Running OCR…", "orange")
        self._append_activity_log("OCR: extracting text…")
        self._set_ocr_meta("OCR running…")
        started_at = time.perf_counter()

        def _run():
            x, y, w, h = self.ocr_region
            text = self.ocr.extract_text_from_region(self.current_screen, x, y, w, h)
            elapsed = time.perf_counter() - started_at

            def _update():
                self.ocr_box.delete("0.0", "end")
                self.ocr_box.insert("0.0", text)
                if self.keep_ocr_region_var.get():
                    self._draw_selection_overlay()
                else:
                    self._clear_selection()
                if text.startswith("OCR Error"):
                    self._set_status("OCR failed", RED)
                    self._append_activity_log(f"OCR failed after {elapsed:.2f}s: {text}")
                    self._set_ocr_meta(f"OCR failed • {elapsed:.2f}s")
                    return
                self._set_status("OCR done ✓", "green")
                self._append_activity_log(f"OCR: extracted text completed in {elapsed:.2f}s.")
                self._set_ocr_meta(f"Last OCR: {elapsed:.2f}s")
                if on_complete:
                    on_complete(text)

            self.after(0, _update)

        threading.Thread(target=_run, daemon=True).start()

    def _llm_log_name(self) -> str:
        if self.llm.provider == PROVIDER_OPENAI:
            return f"OpenAI ({self.llm.openai_model})"
        return self.llm.model

    def _prepare_generation_prompt(self, mode: str):
        if self.generate_with_ocr_var.get():
            self._run_ocr_workflow(on_complete=lambda text: self._start_generation(mode, text.strip()))
            return None
        prompt = self.ocr_box.get("0.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Generate", "Run OCR or type a question first.")
            return None
        return prompt

    def _start_generation(self, mode: str, prompt: str | None = None):
        text = (prompt or self.ocr_box.get("0.0", "end")).strip()
        if not text:
            messagebox.showwarning("Generate", "Run OCR or type a question first.")
            return
        if mode == "answer":
            self._generate_answer_from_prompt(text)
        else:
            self._generate_code_from_prompt(text)

    def _generate_answer_from_prompt(self, question: str):
        self._sync_llm_settings_from_ui()
        ctx = self.context_box.get("0.0", "end").strip()
        if "Example:" in ctx:
            ctx = ""
        self._clear(self.answer_box)
        provider_name = self._llm_provider_label(self.llm.provider)
        llm_log_name = self._llm_log_name()
        self._set_status(f"Generating answer via {provider_name}…", "orange")
        self._append_activity_log(f"{llm_log_name}: generating answer…")
        self._set_answer_meta("Generating answer…")
        started_at = time.perf_counter()

        def _run():
            def _cb(tok):
                self.after(0, lambda t=tok: self._append(self.answer_box, t))

            final_answer = self.llm.generate_answer(question, context=ctx, stream_callback=_cb)

            def _finish():
                elapsed = time.perf_counter() - started_at
                if final_answer and not final_answer.startswith("[LLM Error"):
                    cleaned_answer = re.sub(r"\n\s*\n+", "\n", final_answer).strip()
                    self._clear(self.answer_box)
                    self._append(self.answer_box, cleaned_answer)
                    token_estimate = self._estimate_tokens(cleaned_answer)
                    self._set_answer_meta(f"Last answer: ~{token_estimate} tokens • {elapsed:.2f}s")
                    self._append_activity_log(
                        f"{llm_log_name}: generating answer completed in {elapsed:.2f}s (~{token_estimate} tokens)."
                    )
                    try:
                        self._append_session_log(
                            "Interview Answer",
                            question,
                            cleaned_answer,
                            token_count=token_estimate,
                            duration_seconds=elapsed,
                        )
                    except Exception as e:
                        self._set_status(f"Done ✓ (log failed: {e})", ORANGE)
                        return
                    self._set_status("Done ✓", "green")
                else:
                    self._set_answer_meta(f"Generation failed • {elapsed:.2f}s")
                    self._append_activity_log(f"{llm_log_name}: generating answer failed after {elapsed:.2f}s.")
                    self._set_status("Generation failed", RED)

            self.after(0, _finish)

        threading.Thread(target=_run, daemon=True).start()

    def _generate_code_from_prompt(self, problem: str):
        self._sync_llm_settings_from_ui()
        self._clear(self.answer_box)
        provider_name = self._llm_provider_label(self.llm.provider)
        llm_log_name = self._llm_log_name()
        self._set_status(f"Generating code via {provider_name}…", "orange")
        self._append_activity_log(f"{llm_log_name}: generating code…")
        self._set_answer_meta("Generating code…")
        started_at = time.perf_counter()

        def _run():
            def _cb(tok):
                self.after(0, lambda t=tok: self._append(self.answer_box, t))

            final_code = self.llm.generate_code(problem, stream_callback=_cb)

            def _finish():
                elapsed = time.perf_counter() - started_at
                if final_code and not final_code.startswith("[LLM Error"):
                    cleaned_code = re.sub(r"\n\s*\n+", "\n", final_code).strip()
                    self._clear(self.answer_box)
                    self._append(self.answer_box, cleaned_code)
                    token_estimate = self._estimate_tokens(cleaned_code)
                    self._set_answer_meta(f"Last code: ~{token_estimate} tokens • {elapsed:.2f}s")
                    self._append_activity_log(
                        f"{llm_log_name}: generating code completed in {elapsed:.2f}s (~{token_estimate} tokens)."
                    )
                    try:
                        self._append_session_log(
                            "Coding Answer",
                            problem,
                            cleaned_code,
                            token_count=token_estimate,
                            duration_seconds=elapsed,
                        )
                    except Exception as e:
                        self._set_status(f"Done ✓ (log failed: {e})", ORANGE)
                        return
                    self._set_status("Done ✓", "green")
                else:
                    self._set_answer_meta(f"Generation failed • {elapsed:.2f}s")
                    self._append_activity_log(f"{llm_log_name}: generating code failed after {elapsed:.2f}s.")
                    self._set_status("Generation failed", RED)

            self.after(0, _finish)

        threading.Thread(target=_run, daemon=True).start()

    def _gen_answer(self):
        prompt = self._prepare_generation_prompt("answer")
        if prompt is not None:
            self._start_generation("answer", prompt)

    def _gen_code(self):
        prompt = self._prepare_generation_prompt("code")
        if prompt is not None:
            self._start_generation("code", prompt)

    def _change_llm_provider(self, _label: str):
        self._update_llm_status_hint()
        if self.llm.provider == PROVIDER_LOCAL:
            threading.Thread(target=self._refresh_llm_models, daemon=True).start()
            self.llm.warmup()

    def _change_llm(self, model: str):
        self._sync_llm_settings_from_ui()
        self.llm.model = model
        self.llm_status.configure(
            text=f"🟡  Local LLM selected • model {model}",
            text_color="orange",
        )

    def _refresh_llm_models(self):
        self._sync_llm_settings_from_ui()
        if self.llm.provider != PROVIDER_LOCAL:
            self.after(
                0,
                lambda: self.llm_status.configure(
                    text=f"🟡  OpenAI API selected • model {self.llm.openai_model}",
                    text_color="orange" if self.llm.openai_api_key else ORANGE,
                ),
            )
            return

        models = self.llm.list_models()
        if models:
            preferred = [m for m in DEFAULT_LLM_OPTIONS if m in models]
            extras = [m for m in models if m not in DEFAULT_LLM_OPTIONS]
            menu_models = preferred + extras
            selected = self.llm.model if self.llm.model in menu_models else menu_models[0]

            self.after(0, lambda: self.llm_menu.configure(values=menu_models))
            self.after(0, lambda: self.llm_menu.set(selected))
            self.llm.model = selected
            self.after(
                0,
                lambda: self.llm_status.configure(
                    text=f"✅  {len(models)} model(s) available • using {selected}",
                    text_color="green",
                ),
            )
        else:
            self.after(
                0,
                lambda: self.llm_status.configure(
                    text=f"⚠️  No models — is `ollama serve` running? Default is {self.llm.model}",
                    text_color="orange",
                ),
            )

    def _test_llm(self):
        self._sync_llm_settings_from_ui()
        self.llm_status.configure(text="⏳  Testing…", text_color="orange")

        def _do():
            r = self.llm.generate_answer("Say exactly: LLM OK")
            ok = bool(r) and "Error" not in r

            def _update():
                self.llm_status.configure(
                    text="✅  LLM working!" if ok else f"❌  {r}",
                    text_color="green" if ok else RED,
                )
                if not ok:
                    messagebox.showerror("LLM Test Failed", r)

            self.after(0, _update)

        threading.Thread(target=_do, daemon=True).start()

    def on_close(self):
        self._live_screen_on = False
        try:
            self._save_settings()
        except Exception:
            pass
        self.vnc.disconnect()
        self.local_window_capture.disconnect()
        self.webcam.disconnect()
        self.destroy()


if __name__ == "__main__":
    app = InterviewAssistant()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
