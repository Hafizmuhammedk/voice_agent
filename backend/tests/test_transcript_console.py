from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from backend.voice_agent.transcript_console import TranscriptConsole, sanitize_transcript


class TranscriptConsoleTests(unittest.TestCase):
    def test_speaker_labels_have_distinct_terminal_colors(self) -> None:
        output = io.StringIO()
        console = TranscriptConsole(enabled=True, colors=True, stream=output)

        console.write("user", "I need a room.")
        console.write("agent", "I can help with that.")

        rendered = output.getvalue()
        self.assertIn("\x1b[1;96mUSER", rendered)
        self.assertIn("\x1b[1;92mAGENT", rendered)
        self.assertIn("I need a room.", rendered)
        self.assertIn("I can help with that.", rendered)

    def test_plain_output_is_filterable_and_marks_interruption(self) -> None:
        output = io.StringIO()
        console = TranscriptConsole(enabled=True, colors=False, stream=output)

        console.write("agent", "One moment.", interrupted=True)

        self.assertEqual(
            output.getvalue(),
            "[VOICE] AGENT > (interrupted) One moment.\n",
        )

    def test_untrusted_terminal_controls_are_removed(self) -> None:
        clean = sanitize_transcript(
            "hello\x1b[31m red\nnext\tline",
            max_characters=500,
        )

        self.assertEqual(clean, "hello red next line")
        self.assertNotIn("\x1b", clean)

    def test_narrow_terminal_encoding_cannot_break_the_voice_callback(self) -> None:
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding="ascii")
        console = TranscriptConsole(enabled=True, colors=True, stream=output)

        console.write("user", "مرحبا")
        output.flush()

        self.assertIn(b"[VOICE]", buffer.getvalue())
        output.detach()

    def test_environment_configuration_is_opt_in(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CONSOLE_TRANSCRIPTS": "true",
                "CONSOLE_TRANSCRIPT_COLORS": "false",
                "CONSOLE_TRANSCRIPT_MAX_CHARACTERS": "120",
            },
            clear=True,
        ):
            console = TranscriptConsole.from_environment(stream=io.StringIO())

        self.assertTrue(console.enabled)
        self.assertFalse(console.colors)
        self.assertEqual(console.max_characters, 120)


if __name__ == "__main__":
    unittest.main()
