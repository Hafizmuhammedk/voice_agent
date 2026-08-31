"""Small terminal renderer for committed voice transcripts."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Literal, TextIO

from .config import as_bool

TranscriptSpeaker = Literal["user", "agent"]

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")
_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_SPEAKER_COLORS: dict[TranscriptSpeaker, str] = {
    "user": "\x1b[1;96m",
    "agent": "\x1b[1;92m",
}
_SPEAKER_LABELS: dict[TranscriptSpeaker, str] = {
    "user": "USER ",
    "agent": "AGENT",
}


def sanitize_transcript(text: str, *, max_characters: int) -> str:
    """Flatten transcript text and remove control sequences from untrusted speech."""
    clean = _ANSI_ESCAPE.sub("", text)
    clean = _CONTROL_CHARACTERS.sub("", clean)
    clean = _WHITESPACE.sub(" ", clean).strip()
    if len(clean) <= max_characters:
        return clean
    return f"{clean[: max_characters - 3].rstrip()}..."


def _bounded_max_characters(raw_value: str | None) -> int:
    try:
        value = int(raw_value or "500")
    except ValueError:
        return 500
    return min(2_000, max(80, value))


@dataclass(slots=True)
class TranscriptConsole:
    """Render easy-to-scan speaker lines without changing structured app logs."""

    enabled: bool
    colors: bool
    max_characters: int = 500
    stream: TextIO = sys.stdout

    @classmethod
    def from_environment(cls, *, stream: TextIO = sys.stdout) -> TranscriptConsole:
        default_colors = bool(getattr(stream, "isatty", lambda: False)())
        colors = as_bool(os.getenv("CONSOLE_TRANSCRIPT_COLORS"), default_colors)
        if "NO_COLOR" in os.environ:
            colors = False
        return cls(
            enabled=as_bool(os.getenv("CONSOLE_TRANSCRIPTS"), True),
            colors=colors,
            max_characters=_bounded_max_characters(os.getenv("CONSOLE_TRANSCRIPT_MAX_CHARACTERS")),
            stream=stream,
        )

    def write(
        self,
        speaker: TranscriptSpeaker,
        text: str,
        *,
        interrupted: bool = False,
    ) -> None:
        if not self.enabled:
            return
        clean = sanitize_transcript(text, max_characters=self.max_characters)
        if not clean:
            return

        label = _SPEAKER_LABELS[speaker]
        suffix = " (interrupted)" if interrupted else ""
        if self.colors:
            color = _SPEAKER_COLORS[speaker]
            line = f"{_DIM}[VOICE]{_RESET} {color}{label} >{suffix}{_RESET} {color}{clean}{_RESET}\n"
        else:
            line = f"[VOICE] {label} >{suffix} {clean}\n"

        try:
            self.stream.write(line)
            self.stream.flush()
        except UnicodeEncodeError:
            # Legacy Windows terminals may still use a narrow code page. The
            # transcript display must never raise into the live audio callback.
            encoding = getattr(self.stream, "encoding", None) or "ascii"
            safe_line = line.encode(encoding, errors="replace").decode(encoding)
            try:
                self.stream.write(safe_line)
                self.stream.flush()
            except (OSError, UnicodeError, ValueError):
                return
        except (OSError, ValueError):
            return
