"""Safe call-control and support tools exposed to the language model."""

from __future__ import annotations

import logging
import re
from typing import Literal

from livekit import rtc
from livekit.agents import AgentSession, JobContext, RunContext, ToolError, function_tool, llm

from .state import CallState

logger = logging.getLogger("voice-agent")

END_CALL_PHRASES = (
    "goodbye",
    "bye",
    "hang up",
    "end the call",
    "end this call",
    "that's all",
    "that is all",
    "nothing else",
    "مع السلامة",
    "أنهِ المكالمة",
    "هذا كل شيء",
    "farvel",
    "afslut opkaldet",
    "auf wiedersehen",
    "anruf beenden",
    "adiós",
    "termina la llamada",
    "au revoir",
    "terminer l'appel",
    "tot ziens",
    "beëindig het gesprek",
    "tchau",
    "encerrar a chamada",
    "hej då",
    "avsluta samtalet",
)
END_CALL_PATTERNS = tuple(
    re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)") for phrase in END_CALL_PHRASES
)
END_CALL_NEGATION_PATTERN = re.compile(
    r"(?:\b(?:do not|don't|dont|not|never|ikke|nicht|kein|pas|ne|niet|geen|"
    r"não|inte)\b|لا).{0,40}$"
)
STANDALONE_GRATITUDE_PATTERN = re.compile(
    r"^\s*(?:(?:okay|ok|alright|all right|great|perfect)[\s,.!\-]*)?"
    r"(?:thank you|thanks)(?:\s+(?:very much|so much))?[\s,.!\-]*$",
    re.I,
)
STANDALONE_NOTHING_ELSE_PATTERN = re.compile(
    r"^\s*(?:nothing|nothing else|no(?:thing)? more|no)"
    r"(?:[\s,.!\-]+(?:thank you|thanks)(?:\s+(?:very much|so much))?)?"
    r"[\s,.!\-]*$",
    re.I,
)
CALL_FAREWELLS = {
    "en": "Thank you for contacting {company_name}. It was a pleasure helping you. Have a wonderful day. Goodbye.",
    "ar": "شكراً لتواصلك مع {company_name}. سعدنا بخدمتك. نتمنى لك يوماً سعيداً. مع السلامة.",
    "da": "Tak, fordi du kontaktede {company_name}. Det var en fornøjelse at hjælpe dig. Hav en god dag. Farvel.",
    "de": "Vielen Dank für Ihren Anruf bei {company_name}. Wir haben Ihnen gerne geholfen. Einen schönen Tag noch. Auf Wiedersehen.",
    "es": "Gracias por contactar con {company_name}. Ha sido un placer ayudarle. Que tenga un buen día. Adiós.",
    "fr": "Merci d'avoir contacté {company_name}. Ce fut un plaisir de vous aider. Bonne journée. Au revoir.",
    "nl": "Bedankt dat u contact hebt opgenomen met {company_name}. We hielpen u graag. Fijne dag. Tot ziens.",
    "pt": "Obrigado por entrar em contato com {company_name}. Foi um prazer ajudar. Tenha um ótimo dia. Tchau.",
    "sv": "Tack för att du kontaktade {company_name}. Det var ett nöje att hjälpa dig. Ha en fin dag. Hej då.",
}


def _require_job_ctx(state: CallState) -> JobContext:
    if state.job_ctx is None:
        raise ToolError("This action is unavailable outside a live call.")
    return state.job_ctx


async def terminate_live_call(
    job_ctx: JobContext,
    session: AgentSession[CallState],
    reason: str,
) -> None:
    """Delete a dedicated call room, with a safe console-mode fallback."""
    if job_ctx.is_fake_job():
        session.shutdown(drain=True)
        job_ctx.shutdown(reason=reason)
        return
    try:
        await job_ctx.delete_room()
    except Exception:
        logger.exception("Could not delete the call room; falling back to job shutdown")
        session.shutdown(drain=True)
        job_ctx.shutdown(reason=reason)


def has_explicit_end_call_request(evidence: str) -> bool:
    """Accept a hang-up only when the caller's latest words clearly request it."""
    normalized = evidence.casefold().replace("’", "'")
    if STANDALONE_GRATITUDE_PATTERN.fullmatch(
        normalized
    ) or STANDALONE_NOTHING_ELSE_PATTERN.fullmatch(normalized):
        return True
    for pattern in END_CALL_PATTERNS:
        for match in pattern.finditer(normalized):
            prefix = normalized[max(0, match.start() - 48) : match.start()]
            if END_CALL_NEGATION_PATTERN.search(prefix) is None:
                return True
    return False


@function_tool(flags=llm.ToolFlag.IGNORE_ON_ENTER)
async def end_call(
    context: RunContext[CallState],
    caller_exact_request: str,
) -> str:
    """End only when the caller's latest exact words request goodbye or hang-up.

    This tool speaks the closing farewell itself. Never call it merely because a
    booking, question, or service request has been completed.
    """
    state = context.userdata
    evidence = state.last_user_text or caller_exact_request
    if not has_explicit_end_call_request(evidence):
        raise ToolError(
            "The caller did not ask to end the call. Continue helping with their latest request."
        )

    context.disallow_interruptions()
    job_ctx = _require_job_ctx(state)
    state.mark_terminal("completed", "caller requested end call")
    farewell_template = CALL_FAREWELLS.get(
        state.config.language_code,
        CALL_FAREWELLS["en"],
    )
    await context.session.say(
        farewell_template.format(company_name=state.config.company_name),
        allow_interruptions=False,
    )
    await context.wait_for_playout()
    await terminate_live_call(
        job_ctx,
        context.session,
        state.outcome or "caller requested end call",
    )
    return ""


TRANSFER_TARGET_PHRASES = (
    "human",
    "real person",
    "representative",
    "manager",
    "supervisor",
    "someone",
    "موظف",
    "شخص حقيقي",
    "مدير",
    "مشرف",
    "menneske",
    "medarbejder",
    "repræsentant",
    "nogen",
    "mensch",
    "echte person",
    "mitarbeiter",
    "vorgesetzter",
    "jemandem",
    "humano",
    "persona real",
    "representante",
    "agente",
    "gerente",
    "alguien",
    "humain",
    "vraie personne",
    "conseiller",
    "représentant",
    "responsable",
    "quelqu'un",
    "mens",
    "echt persoon",
    "medewerker",
    "vertegenwoordiger",
    "iemand",
    "pessoa real",
    "atendente",
    "alguém",
    "människa",
    "riktig person",
    "medarbetare",
    "representant",
    "någon",
)
TRANSFER_ACTION_PHRASES = (
    "speak",
    "talk",
    "connect",
    "transfer",
    "want",
    "need",
    "تحدث",
    "التحدث",
    "حولني",
    "تحويل",
    "أريد",
    "tale",
    "snakke",
    "forbind",
    "omstille",
    "sprechen",
    "reden",
    "verbinden",
    "weiterleiten",
    "möchte",
    "hablar",
    "conectar",
    "transferir",
    "quiero",
    "parler",
    "connecter",
    "transférer",
    "voudrais",
    "spreken",
    "praten",
    "doorverbinden",
    "overzetten",
    "falar",
    "conectar",
    "transferir",
    "quero",
    "tala",
    "prata",
    "koppla",
    "överföra",
    "vill",
)
TRANSFER_TARGET_PATTERNS = tuple(
    re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)") for phrase in TRANSFER_TARGET_PHRASES
)
TRANSFER_ACTION_PATTERNS = tuple(
    re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)") for phrase in TRANSFER_ACTION_PHRASES
)
TRANSFER_NEGATION_PATTERN = re.compile(
    r"(?:\b(?:do not|don't|dont|not|no|ikke|nicht|kein|keine|pas|niet|geen|"
    r"não|inte)\b|لا).{0,32}$"
)
TRANSFER_HOLD_MESSAGES = {
    "ar": "يرجى الانتظار بينما أوصلك بأحد أعضاء الفريق.",
    "da": "Vent venligst, mens jeg stiller dig om til en medarbejder.",
    "de": "Bitte warten Sie, während ich Sie mit einem Teammitglied verbinde.",
    "es": "Espere un momento mientras le comunico con un miembro del equipo.",
    "fr": "Veuillez patienter pendant que je vous mets en relation avec un membre de l’équipe.",
    "nl": "Een ogenblik alstublieft, terwijl ik u doorverbind met een medewerker.",
    "pt": "Aguarde um momento enquanto transfiro você para um membro da equipe.",
    "sv": "Vänta ett ögonblick medan jag kopplar dig till en medarbetare.",
}


def has_explicit_transfer_request(evidence: str) -> bool:
    normalized = evidence.casefold()
    has_action = any(pattern.search(normalized) for pattern in TRANSFER_ACTION_PATTERNS)
    if not has_action:
        return False

    for pattern in TRANSFER_TARGET_PATTERNS:
        for match in pattern.finditer(normalized):
            prefix = normalized[max(0, match.start() - 40) : match.start()]
            if TRANSFER_NEGATION_PATTERN.search(prefix) is None:
                return True
    return False


@function_tool(flags=llm.ToolFlag.IGNORE_ON_ENTER)
async def transfer_to_human(
    context: RunContext[CallState],
    caller_exact_request: str,
) -> str:
    """Transfer only after an explicit request for a human, manager, or supervisor."""
    state = context.userdata
    destination = state.config.human_transfer_number
    if destination is None:
        raise ToolError("Human transfer is not configured for this agent.")

    evidence = state.last_user_text or caller_exact_request
    if not has_explicit_transfer_request(evidence):
        raise ToolError("The caller did not explicitly request a human transfer.")

    job_ctx = _require_job_ctx(state)
    participant = job_ctx.room.remote_participants.get(state.participant_identity)
    if participant is None or participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        raise ToolError("Human transfer is available only for SIP phone calls.")

    context.disallow_interruptions()
    hold_message = TRANSFER_HOLD_MESSAGES.get(
        state.config.language_code,
        "Please hold while I connect you with a team member.",
    )
    await context.session.say(hold_message, allow_interruptions=False)
    state.transfer_finished.clear()
    state.status = "transferring"
    state.outcome = "caller requested human"
    try:
        await job_ctx.transfer_sip_participant(
            participant,
            destination,
            play_dialtone=True,
        )
    except Exception as error:
        logger.warning("SIP transfer failed", exc_info=True)
        if state.status == "transferring":
            if state.participant_identity in job_ctx.room.remote_participants:
                state.status = "active"
                state.outcome = None
            else:
                state.mark_terminal("failed", "call disconnected during transfer")
        raise ToolError("The transfer failed. Continue helping the caller yourself.") from error
    else:
        state.mark_terminal("transferred", "caller requested human")
    finally:
        state.transfer_finished.set()
    return "Transfer completed."


@function_tool(flags=llm.ToolFlag.IGNORE_ON_ENTER)
async def log_customer_sentiment(
    context: RunContext[CallState],
    sentiment: Literal["frustrated", "angry", "confused"],
    exact_customer_words: str,
) -> str:
    """Note sentiment only when the caller explicitly expresses that emotion."""
    state = context.userdata
    evidence = state.last_user_text or exact_customer_words
    normalized = evidence.casefold()
    evidence_markers = {
        "angry": ("angry", "furious", "mad", "غاضب", "غضبان"),
        "frustrated": ("frustrat", "fed up", "annoyed", "محبط", "منزعج"),
        "confused": (
            "confused",
            "don't understand",
            "do not understand",
            "unclear",
            "مرتبك",
            "لا أفهم",
            "غير واضح",
        ),
    }
    if not any(marker in normalized for marker in evidence_markers[sentiment]):
        raise ToolError("The caller's words do not explicitly support that sentiment.")
    responses = {
        "angry": "Acknowledge the caller's anger, apologize sincerely, and focus on resolution.",
        "frustrated": "Acknowledge the frustration and make the next step especially clear.",
        "confused": "Use simpler language and explain one step at a time.",
    }
    return responses[sentiment]


# Compatibility aliases for the previous single-file API.
_has_explicit_transfer_request = has_explicit_transfer_request
_terminate_live_call = terminate_live_call
