"""
Structured interview history persistence and markdown compatibility.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime


DEFAULT_HISTORY_JSONL = "interview_history.jsonl"


@dataclass
class InterviewRecord:
    created_at: str
    mode: str
    question: str
    answer: str
    provider: str
    model: str
    token_count: int | None = None
    duration_seconds: float | None = None

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.created_at.strip(),
            self.mode.strip(),
            self.question.strip(),
            self.answer.strip(),
        )


class InterviewHistoryStore:
    def __init__(
        self,
        app_dir: str,
        markdown_filename: str,
        jsonl_filename: str = DEFAULT_HISTORY_JSONL,
    ):
        self.app_dir = app_dir
        self.markdown_path = os.path.join(app_dir, markdown_filename)
        self.jsonl_path = os.path.join(app_dir, jsonl_filename)

    def append_record(self, record: InterviewRecord):
        self._append_jsonl(record)
        self._append_markdown(record)

    def load_records(self) -> list[InterviewRecord]:
        merged: dict[tuple[str, str, str, str], InterviewRecord] = {}
        for record in self._load_markdown_records():
            merged[record.key()] = record
        for record in self._load_jsonl_records():
            merged[record.key()] = record

        records = list(merged.values())
        records.sort(key=self._sort_key, reverse=True)
        return records

    def _append_jsonl(self, record: InterviewRecord):
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def _append_markdown(self, record: InterviewRecord):
        entry = (
            f"\n## {record.created_at} | {record.mode}\n"
            f"- Provider: {record.provider}\n"
            f"- Model: {record.model}\n"
        )
        if record.token_count is not None:
            entry += f"- Tokens: {record.token_count}\n"
        if record.duration_seconds is not None:
            entry += f"- Duration Seconds: {record.duration_seconds:.2f}\n"
        entry += (
            f"\n### Question\n{record.question.strip()}\n\n"
            f"### Answer\n{record.answer.strip()}\n"
        )

        with open(self.markdown_path, "a", encoding="utf-8") as f:
            f.write(entry)

    def _load_jsonl_records(self) -> list[InterviewRecord]:
        if not os.path.exists(self.jsonl_path):
            return []

        records: list[InterviewRecord] = []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                    records.append(InterviewRecord(**payload))
                except Exception:
                    continue
        return records

    def _load_markdown_records(self) -> list[InterviewRecord]:
        if not os.path.exists(self.markdown_path):
            return []

        with open(self.markdown_path, "r", encoding="utf-8") as f:
            content = f.read()

        pattern = re.compile(r"^## (?P<header>.+?)\n(?P<body>.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
        records: list[InterviewRecord] = []
        for match in pattern.finditer(content):
            header = match.group("header").strip()
            body = match.group("body").strip()

            created_at, mode = self._parse_header(header)
            if not created_at or not mode:
                continue

            provider = self._match_meta(body, "Provider") or "Unknown"
            model = self._match_meta(body, "Model") or "Unknown"
            token_count = self._match_int_meta(body, "Tokens")
            duration_seconds = self._match_float_meta(body, "Duration Seconds")
            question = self._extract_markdown_section(body, "Question", "Answer")
            answer = self._extract_markdown_section(body, "Answer", None)
            if not question and not answer:
                continue

            records.append(
                InterviewRecord(
                    created_at=created_at,
                    mode=mode,
                    question=question.strip(),
                    answer=answer.strip(),
                    provider=provider.strip(),
                    model=model.strip(),
                    token_count=token_count,
                    duration_seconds=duration_seconds,
                )
            )

        return records

    def _parse_header(self, header: str) -> tuple[str, str]:
        if "|" not in header:
            return "", ""
        created_at, mode = header.split("|", 1)
        return created_at.strip(), mode.strip()

    def _match_meta(self, body: str, label: str) -> str | None:
        pattern = re.compile(rf"^- {re.escape(label)}:\s*(.+)$", re.MULTILINE)
        match = pattern.search(body)
        return match.group(1).strip() if match else None

    def _match_int_meta(self, body: str, label: str) -> int | None:
        value = self._match_meta(body, label)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _match_float_meta(self, body: str, label: str) -> float | None:
        value = self._match_meta(body, label)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _extract_markdown_section(self, body: str, section_name: str, next_section_name: str | None) -> str:
        if next_section_name:
            pattern = re.compile(
                rf"### {re.escape(section_name)}\n(?P<value>.*?)(?=\n### {re.escape(next_section_name)}|\Z)",
                re.DOTALL,
            )
        else:
            pattern = re.compile(rf"### {re.escape(section_name)}\n(?P<value>.*)\Z", re.DOTALL)
        match = pattern.search(body)
        return match.group("value").strip() if match else ""

    def _sort_key(self, record: InterviewRecord):
        text = record.created_at.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return datetime.min
