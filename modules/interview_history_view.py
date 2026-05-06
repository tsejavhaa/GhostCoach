"""
Reusable saved interview history view.
"""
from __future__ import annotations

import customtkinter as ctk

from modules.interview_history import InterviewRecord


class InterviewHistoryView(ctk.CTkFrame):
    def __init__(self, parent, on_refresh):
        super().__init__(parent, fg_color="transparent")
        self._on_refresh = on_refresh
        self._build_ui()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 6))

        ctk.CTkLabel(
            header,
            text="🗂️  Saved Interviews",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        self.summary_lbl = ctk.CTkLabel(
            header,
            text="No saved interviews yet",
            text_color="#888",
            font=ctk.CTkFont(size=12),
        )
        self.summary_lbl.pack(side="right", padx=(0, 10))

        ctk.CTkButton(
            header,
            text="🔄  Refresh",
            command=self._on_refresh,
            width=120,
        ).pack(side="right", padx=(0, 10))

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def render_records(self, records: list[InterviewRecord]):
        for child in self.scroll.winfo_children():
            child.destroy()

        self.summary_lbl.configure(text=f"{len(records)} saved interview(s)")

        if not records:
            ctk.CTkLabel(
                self.scroll,
                text="No interview history found yet.",
                text_color="#888",
                font=ctk.CTkFont(size=13),
            ).pack(anchor="w", padx=12, pady=12)
            return

        for record in records:
            self._render_card(record)

    def _render_card(self, record: InterviewRecord):
        card = ctk.CTkFrame(self.scroll, corner_radius=10)
        card.pack(fill="x", padx=6, pady=6)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            header,
            text=f"{record.created_at}  •  {record.mode}",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

        provider_bits = [record.provider, record.model]
        ctk.CTkLabel(
            header,
            text=" • ".join(bit for bit in provider_bits if bit),
            text_color="#a5b4fc",
            font=ctk.CTkFont(size=11),
        ).pack(side="right")

        meta_parts = [
            f"Tokens: {record.token_count}" if record.token_count is not None else "Tokens: —",
            f"Time: {record.duration_seconds:.2f}s" if record.duration_seconds is not None else "Time: —",
        ]
        ctk.CTkLabel(
            card,
            text=" • ".join(meta_parts),
            text_color="#888",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkLabel(
            card,
            text="Question",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=12)
        ctk.CTkLabel(
            card,
            text=record.question.strip() or "—",
            justify="left",
            wraplength=920,
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(2, 8))

        ctk.CTkLabel(
            card,
            text="Answer",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=12)
        ctk.CTkLabel(
            card,
            text=record.answer.strip() or "—",
            justify="left",
            wraplength=920,
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(2, 12))
