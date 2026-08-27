"""Structured, deterministic reservation state for hotel conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUMBER_PATTERN = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.I,
)
_GUEST_NUMBER_PATTERN = re.compile(
    r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:adult|adults|guest|guests|person|people|child|children|kid|kids)\b",
    re.I,
)
_ROOM_TYPES = (
    "accessible",
    "deluxe",
    "double",
    "family",
    "king",
    "non-smoking",
    "queen",
    "single",
    "standard",
    "suite",
    "twin",
)


def _number_value(value: str) -> int | None:
    normalized = value.casefold()
    number = int(normalized) if normalized.isdigit() else _NUMBER_WORDS.get(normalized)
    return number if number is not None and 0 < number <= 20 else None


def _first_number(text: str) -> int | None:
    match = _NUMBER_PATTERN.search(text)
    return _number_value(match.group(1)) if match else None


def _guest_count(text: str) -> int | None:
    matches = list(_GUEST_NUMBER_PATTERN.finditer(text))
    if matches:
        values = [_number_value(match.group(1)) for match in matches]
        total = sum(value for value in values if value is not None)
        return total if 0 < total <= 20 else None
    return _first_number(text)


def _clean_guest_name(text: str) -> str | None:
    cleaned = re.sub(
        r"^\s*(?:my name is|this is|the name is|book it under)\s+",
        "",
        text,
        flags=re.I,
    ).strip(" .,!?")
    if not cleaned or len(cleaned) > 120 or len(cleaned.split()) > 6:
        return None
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", cleaned) is None:
        return None
    return " ".join(part.capitalize() for part in cleaned.split())


@dataclass(slots=True)
class ReservationDraft:
    """Facts retained independently from the LLM conversation history."""

    check_in: str | None = None
    check_out: str | None = None
    guest_count: int | None = None
    room_count: int | None = None
    room_type: str | None = None
    guest_name: str | None = None
    contact_phone: str | None = None
    contact_confirmed: bool = False
    final_confirmed: bool = False

    @property
    def missing_fields(self) -> tuple[str, ...]:
        fields = (
            ("check-in date", self.check_in),
            ("check-out date", self.check_out),
            ("guest count", self.guest_count),
            ("room count", self.room_count),
            ("room preference", self.room_type),
            ("guest name", self.guest_name),
            ("contact confirmation", self.contact_confirmed),
        )
        return tuple(name for name, value in fields if value is None or value is False)

    @property
    def ready_for_final_confirmation(self) -> bool:
        return not self.missing_fields

    def capture_answer(self, last_question: str, answer: str) -> set[str]:
        """Capture fields only when the preceding question establishes their meaning."""
        question = last_question.casefold()
        updated: set[str] = set()

        if any(term in question for term in ("how many guests", "number of guests", "how many people")):
            value = _guest_count(answer)
            if value is not None:
                self.guest_count = value
                updated.add("guest count")

        if any(term in question for term in ("how many rooms", "number of rooms")):
            value = _first_number(answer)
            if value is not None:
                self.room_count = value
                updated.add("room count")

        if any(
            term in question
            for term in ("room type", "room preference", "kind of room", "bed preference")
        ):
            normalized_answer = answer.casefold()
            matches = [room_type for room_type in _ROOM_TYPES if room_type in normalized_answer]
            if matches:
                self.room_type = " ".join(matches)
                updated.add("room preference")

        if any(
            term in question
            for term in ("your name", "guest name", "name for", "name should")
        ):
            name = _clean_guest_name(answer)
            if name is not None:
                self.guest_name = name
                updated.add("guest name")

        return updated

    def prompt_summary(self) -> str:
        known = {
            "check-in": self.check_in,
            "check-out": self.check_out,
            "guests": self.guest_count,
            "rooms": self.room_count,
            "room preference": self.room_type,
            "guest name": self.guest_name,
            "contact confirmed": self.contact_confirmed,
        }
        retained = ", ".join(
            f"{field}={value}" for field, value in known.items() if value is not None
        )
        missing = ", ".join(self.missing_fields) or "none"
        return f"Retained reservation facts: {retained or 'none'}. Missing fields: {missing}."
