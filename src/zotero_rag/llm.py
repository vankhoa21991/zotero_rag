"""Local-LLM client and sectioned summarizer.

The LLM is an OpenAI-compatible chat endpoint (llama.cpp server by default).
`post` is injectable so tests run offline. Long texts are summarized in
sections (dense notes per section) and then merged into the final summary.
"""
import json
import os
import re
import urllib.request

UA = "zotero-rag/0.1"
CHUNK = 24000  # longer documents are summarized in sections of this size

PROMPT = (
    "Summarize this document for a personal research library, in 300-500 words of plain prose (never fewer than 300 when there is enough content). "
    "Start with one sentence saying what it is (paper, tutorial, repo, article, video...) and "
    "its main subject. Then cover the key ideas, methods or arguments, the concrete results, "
    "numbers or tools it names, and who would use it and why. Use only the text provided; "
    "do not add outside knowledge or guess at parts you cannot see. If the text is too thin "
    "to summarize, reply exactly: INSUFFICIENT"
)

NOTES_PROMPT = (
    "These are one section of a longer document. List its key points, arguments, methods, "
    "numbers, names and conclusions as dense plain-prose notes (150-250 words). Use only the text."
)

_THINK = re.compile(r"<think>.*?</think>", re.S)


def _strip_think(s: str) -> str:
    """Drop complete think blocks, any leading unclosed block, and orphan closers."""
    s = _THINK.sub("", s or "")
    if "</think>" in s:
        s = s.split("</think>", 1)[1]
    return s.strip()


class LLMError(RuntimeError):
    pass


def _default_post(url: str, body: dict, headers: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA, **headers},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


class LLM:
    def __init__(self, base=None, key=None, model=None, post=None):
        self.base = (base or os.environ.get("LLM_BASE", "http://127.0.0.1:8080/v1")).rstrip("/")
        self.key = key if key is not None else os.environ.get("LLM_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "qwen3.8-27b")
        self.post = post or _default_post

    def ask(self, title: str, text: str, system: str = PROMPT, max_tokens: int = 1200) -> str:
        """One chat completion; returns the content with any think blocks stripped."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Title: {title}\n\nText:\n{text}"},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        try:
            out = self.post(self.base + "/chat/completions", body, headers)
            content = out["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMError(str(e)) from e
        return _strip_think(content)


def summarize(title: str, text: str, llm: LLM) -> str:
    """One pass for short texts; notes per section then a final merge for long ones.

    Returns the summary, or the literal "INSUFFICIENT" when there is nothing to say.
    """
    if len(text) <= CHUNK:
        return llm.ask(title, text)
    notes = []
    for i in range(0, len(text), CHUNK):
        n = llm.ask(title, text[i:i + CHUNK], NOTES_PROMPT, 500)
        if not n.upper().startswith("INSUFFICIENT"):
            notes.append(n)
    return llm.ask(title, "\n\n".join(notes)) if notes else "INSUFFICIENT"
