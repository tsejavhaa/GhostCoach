"""
LLM Client Module
Wraps Ollama and OpenAI for text generation.
Supports streaming responses for smooth UI updates.
"""
import re
import threading
from typing import Callable


PROVIDER_LOCAL = "local"
PROVIDER_OPENAI = "openai"
DEFAULT_PROVIDER = PROVIDER_LOCAL
DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_KEEP_ALIVE = "15m"
OPENAI_TIMEOUT_SECONDS = 20.0
ANSWER_MAX_CHARS = 1500
CONTEXT_MAX_CHARS = 1200
CODE_MAX_CHARS = 2500

INTERVIEW_SYSTEM_PROMPT = """\
You are an expert job interview coach helping a candidate in real-time.
Given an interview question, provide a natural, specific, first-person answer that sounds like a real candidate speaking.
Use the candidate context as the main source of truth whenever it is relevant.
Do not invent employers, degrees, timelines, projects, or achievements that are not supported by the context or the question.
Prefer concrete details over generic claims, and sound thoughtful rather than overly polished.
Avoid AI-sounding buzzwords such as "leverage", "utilize", "end-to-end", "not just a demo", and other marketing-style phrasing unless the context clearly uses them.
Use STAR structure only when it helps the answer feel natural.
Keep the answer concise but complete.
Format the answer as 2-4 short bullet points.
Each bullet can contain one or two short related sentences.
Make the wording feel conversational, grounded, and human.
Do not add markdown headers like "Answer:".
Start answering immediately without restating the question."""

CODING_SYSTEM_PROMPT = """\
You are a senior software engineer specializing in coding interviews.
Solve the problem correctly before optimizing for style.
Carefully follow the exact problem statement, constraints, and examples.
Check your solution logic against the provided examples and avoid placeholder reasoning.
Before finalizing, make sure the algorithm satisfies the sample inputs and outputs.
If the problem asks for a function, return the function only and do not add demo prints.
Do not use fake checks like str(x) != "0" when the task requires digit-level validation.
Do not include time complexity or space complexity unless the user explicitly asks for them.
Keep the explanation brief: 1-2 short sentences maximum.
Then provide clean Python code with no markdown fences.
Return only the explanation and the code."""

CODING_REVIEW_SYSTEM_PROMPT = """\
You are reviewing a coding interview answer for correctness.
Check the solution against the problem statement, constraints, and examples.
If the draft has any logic bug, edge-case issue, formatting issue, or invalid assumption, fix it.
Return only the corrected final answer:
- 1-2 short sentences of explanation
- plain Python code with no markdown fences
Do not include time complexity or space complexity unless explicitly requested."""


class LLMClient:
    
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str = DEFAULT_PROVIDER,
        openai_model: str = DEFAULT_OPENAI_MODEL,
        openai_api_key: str = "",
    ):
        self.provider = provider
        self.model = model
        self.openai_model = openai_model
        self.openai_api_key = openai_api_key
        self.keep_alive = DEFAULT_KEEP_ALIVE
        self.answer_options = {"temperature": 0.35, "num_ctx": 2048, "num_predict": 260}
        self.code_options = {"temperature": 0.0, "num_ctx": 4096, "num_predict": 420}
        self.code_review_options = {"temperature": 0.0, "num_ctx": 4096, "num_predict": 420}

    def _trim_text(self, text: str, limit: int) -> str:
        """Keep prompts compact so prompt-eval stays fast."""
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n\n[Truncated for speed]"

    def _normalize_interview_answer(self, text: str) -> str:
        """Convert interview answers into short bullet lines."""
        if not text or text.startswith("[LLM Error"):
            return text

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n\s*\n+", "\n", text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return text.strip()

        cleaned_lines = []
        for line in lines:
            line = re.sub(r"^[-*•]\s+", "", line)
            line = re.sub(r"^\d+[.)]\s+", "", line)
            cleaned_lines.append(line)

        bullets = [f"- {line}" for line in cleaned_lines if line]
        final_text = "\n".join(bullets) if bullets else text.strip()
        return re.sub(r"\n\s*\n+", "\n", final_text).strip()

    # ── Model management ───────────────────────────────────────────
    def list_models(self) -> list[str]:
        """Return names of locally available Ollama models."""
        if self.provider != PROVIDER_LOCAL:
            return []

        try:
            import ollama
            result = ollama.list()
            models = result.get("models", [])
            return [m.get("name", m.get("model", "")) for m in models]
        except Exception as e:
            print(f"[LLM] list_models error: {e}")
            return []

    # ── Generation ─────────────────────────────────────────────────
    def generate_answer(
        self,
        question: str,
        context: str = "",
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Generate an interview answer, optionally streaming tokens."""
        question = self._trim_text(question, ANSWER_MAX_CHARS)
        context = self._trim_text(context, CONTEXT_MAX_CHARS)

        system = INTERVIEW_SYSTEM_PROMPT
        if context:
            system += f"\n\nCandidate context (use this to personalise answers):\n{context}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Interview question: {question}"},
        ]
        answer = self._chat(messages, stream_callback, options=self.answer_options)
        return self._normalize_interview_answer(answer)

    def generate_code(
        self,
        problem: str,
        language: str = "python",
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Generate a code solution for a coding interview problem."""
        problem = self._trim_text(problem, CODE_MAX_CHARS)

        draft_messages = [
            {"role": "system", "content": CODING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Coding problem:\n\n{problem}\n\nProvide a {language} solution.",
            },
        ]
        draft = self._chat(draft_messages, None, options=self.code_options)
        if draft.startswith("[LLM Error"):
            if stream_callback:
                stream_callback(draft)
            return draft

        review_messages = [
            {"role": "system", "content": CODING_REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Coding problem:\n\n{problem}\n\n"
                    f"Draft answer to review and fix if needed:\n\n{draft}"
                ),
            },
        ]
        return self._chat(review_messages, stream_callback, options=self.code_review_options)

    def warmup(self):
        """Preload the selected model in the background for a faster first response."""
        if self.provider != PROVIDER_LOCAL:
            return

        def _run():
            try:
                self._chat(
                    [{"role": "user", "content": "Reply with OK."}],
                    stream_callback=None,
                    options={"temperature": 0, "num_predict": 4, "num_ctx": 256},
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    # ── Internal ───────────────────────────────────────────────────
    def _chat(
        self,
        messages: list[dict],
        stream_callback: Callable[[str], None] | None,
        options: dict | None = None,
    ) -> str:
        if self.provider == PROVIDER_OPENAI:
            return self._chat_openai(messages, stream_callback, options or {})
        return self._chat_ollama(messages, stream_callback, options or {})

    def _chat_ollama(
        self,
        messages: list[dict],
        stream_callback: Callable[[str], None] | None,
        options: dict,
    ) -> str:
        import ollama

        try:
            if stream_callback:
                full = ""
                for chunk in ollama.chat(
                    model=self.model,
                    messages=messages,
                    stream=True,
                    options=options,
                    keep_alive=self.keep_alive,
                ):
                    token = chunk["message"]["content"]
                    full += token
                    stream_callback(token)
                return full
            else:
                resp = ollama.chat(
                    model=self.model,
                    messages=messages,
                    options=options,
                    keep_alive=self.keep_alive,
                )
                return resp["message"]["content"]
        except Exception as e:
            err = f"[LLM Error] {e}"
            if stream_callback:
                stream_callback(err)
            return err

    def _chat_openai(
        self,
        messages: list[dict],
        stream_callback: Callable[[str], None] | None,
        options: dict,
    ) -> str:
        try:
            client = self._get_openai_client()
            request = {
                "model": self.openai_model.strip() or DEFAULT_OPENAI_MODEL,
                "messages": messages,
                "temperature": options.get("temperature", 0.2),
                "max_completion_tokens": options.get("num_predict", 200),
            }

            if stream_callback:
                full = ""
                for chunk in client.chat.completions.create(stream=True, **request):
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full += delta
                        stream_callback(delta)
                return full

            resp = client.chat.completions.create(**request)
            return self._extract_openai_text(resp)
        except Exception as e:
            err = self._format_openai_error(e)
            if stream_callback:
                stream_callback(err)
            return err

    def _get_openai_client(self):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "OpenAI SDK is not installed. Run: pip install openai"
            ) from e

        api_key = self.openai_api_key.strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is empty.")

        return OpenAI(
            api_key=api_key,
            timeout=OPENAI_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def _extract_openai_text(self, response) -> str:
        try:
            return response.choices[0].message.content or ""
        except Exception:
            return ""

    def _format_openai_error(self, error: Exception) -> str:
        status = getattr(error, "status_code", None)
        body = getattr(error, "body", None)

        message = ""
        if isinstance(body, dict):
            err_obj = body.get("error", {})
            if isinstance(err_obj, dict):
                message = err_obj.get("message", "") or ""
                err_type = err_obj.get("type", "")
                err_code = err_obj.get("code", "")
                extra = ", ".join(part for part in (err_type, err_code) if part)
                if extra:
                    message = f"{message} ({extra})".strip()

        if not message:
            message = str(error)

        if status:
            return f"[LLM Error {status}] {message}"
        return f"[LLM Error] {message}"
