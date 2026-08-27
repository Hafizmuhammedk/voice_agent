from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from zoneinfo import ZoneInfo

from backend.app.core.config import SUPPORTED_VOICE_LANGUAGE_CODES
from backend.voice_agent import call_tools, config, prompts, session, worker
from backend.voice_agent.persistence import BackendClient
from backend.voice_agent.reservation import ReservationDraft
from backend.voice_agent.state import CallState


class ConfigTests(unittest.TestCase):
    def test_api_and_worker_support_the_same_languages(self) -> None:
        self.assertEqual(config.SUPPORTED_LANGUAGE_CODES, SUPPORTED_VOICE_LANGUAGE_CODES)

    def test_voice_policy_prevents_repetition_loops(self) -> None:
        policy = prompts.VOICE_POLICY.casefold()

        self.assertIn("do not repeat the same completed question or answer", policy)
        self.assertIn("do not guess", policy)
        self.assertIn("never repeat the same clarification sentence in a loop", policy)
        self.assertIn("ask exactly one question and then wait", policy)
        self.assertIn("could you tell me that once more", policy)
        self.assertIn("never restart the request from the beginning", policy)

    def test_hotel_prompt_knows_the_opening_was_already_spoken(self) -> None:
        loaded = config.load_agent_config(
            {
                "company_name": "Grand Hayat",
                "instructions": "Act as the hotel front desk.",
            }
        )
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="web",
            participant_identity="test-participant",
        )

        instructions = prompts.build_agent_instructions(loaded, state).casefold()

        self.assertIn("the application speaks the opening greeting", instructions)
        self.assertIn("do not introduce yourself", instructions)
        self.assertIn("hotel or company name: grand hayat", instructions)
        self.assertIn("never ask what the caller wants to order", instructions)
        self.assertIn("relative dates using these values", instructions)
        self.assertIn("accept them as complete dates", instructions)

    def test_relative_checkout_answer_is_resolved_without_reasking(self) -> None:
        loaded = config.load_agent_config(
            {
                "company_name": "Grand Hayat",
                "language": "en-US",
                "timezone": "Asia/Kolkata",
                "instructions": "Act as the hotel front desk.",
            }
        )

        hint = prompts.build_reservation_turn_hint(
            loaded,
            "Could you please clarify the date you wish to check out?",
            "Day after tomorrow,",
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        expected = (
            datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=2)
        ).isoformat()
        self.assertIn("check-out", hint)
        self.assertIn(expected, hint)
        self.assertIn("do not ask for check-out again", hint.casefold())

    def test_both_relative_reservation_dates_are_retained(self) -> None:
        loaded = config.load_agent_config(
            {
                "language": "en-US",
                "timezone": "Asia/Kolkata",
            }
        )

        hint = prompts.build_reservation_turn_hint(
            loaded,
            "When would you like to arrive?",
            "I will arrive tomorrow, and I will check out day after tomorrow.",
        )

        self.assertIsNotNone(hint)
        assert hint is not None
        self.assertIn("check-in", hint)
        self.assertIn("check-out", hint)
        self.assertIn("continue with the next missing reservation detail", hint.casefold())

    def test_phone_transcript_variants_resolve_as_day_after_tomorrow(self) -> None:
        loaded = config.load_agent_config(
            {"language": "en-US", "timezone": "Asia/Kolkata"}
        )
        expected = (
            datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=2)
        ).isoformat()

        for transcript in ("Today after tomorrow.", "After tomorrow."):
            with self.subTest(transcript=transcript):
                resolved = prompts.resolve_relative_reservation_dates(
                    loaded,
                    "And what date would you like to check out?",
                    transcript,
                )
                self.assertEqual(resolved, {"check-out": expected})

    def test_outbound_prompt_uses_entered_number_without_requesting_it_again(self) -> None:
        loaded = config.load_agent_config(
            {
                "company_name": "Grand Hayat",
                "instructions": "Act as the hotel front desk.",
            }
        )
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="outbound",
            participant_identity="test-callee",
            phone_number="+919876543210",
        )

        instructions = prompts.build_agent_instructions(loaded, state).casefold()

        self.assertIn("proposed contact number, ending in 3210", instructions)
        self.assertIn("never ask the caller to say the full number", instructions)
        self.assertIn("confirm it once", instructions)

    def test_confirmation_detection_is_strict(self) -> None:
        self.assertTrue(prompts.is_affirmative_reply("Yes, please."))
        self.assertTrue(prompts.is_affirmative_reply("That's correct."))
        self.assertFalse(prompts.is_affirmative_reply("Yes, but the date is wrong."))
        self.assertFalse(prompts.is_affirmative_reply("I am not sure."))
        self.assertTrue(
            prompts.asked_to_confirm_contact_number(
                "May I use the contact number ending in 3210?"
            )
        )
        self.assertTrue(
            prompts.asked_for_final_reservation_confirmation(
                "Please confirm that you would like me to record this reservation request?"
            )
        )

    def test_agent_retains_resolved_dates_and_does_not_offer_end_call(self) -> None:
        loaded = config.load_agent_config(
            {"language": "en-US", "timezone": "Asia/Kolkata"}
        )
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="outbound",
            participant_identity="test-callee",
            last_assistant_text="And what date would you like to check out?",
        )
        agent = session.VoiceAgent(state)
        turn_context = session.llm.ChatContext.empty()
        message = session.llm.ChatMessage(role="user", content=["After tomorrow."])

        asyncio.run(agent.on_user_turn_completed(turn_context, message))

        expected = (
            datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=2)
        ).isoformat()
        self.assertEqual(state.reservation.check_out, expected)
        self.assertTrue(turn_context.messages())
        self.assertNotIn("end_call", {tool.id for tool in agent.tools})

    def test_structured_reservation_retains_each_answer(self) -> None:
        draft = ReservationDraft(contact_phone="+919876543210")

        self.assertEqual(
            draft.capture_answer("How many guests will be staying?", "Two adults and one child."),
            {"guest count"},
        )
        self.assertEqual(draft.guest_count, 3)
        self.assertEqual(
            draft.capture_answer("How many rooms would you like?", "One room."),
            {"room count"},
        )
        self.assertEqual(
            draft.capture_answer("What room type would you prefer?", "A king room, please."),
            {"room preference"},
        )
        self.assertEqual(
            draft.capture_answer("What name should I use for the booking?", "My name is Hafiz."),
            {"guest name"},
        )

        self.assertEqual(draft.room_count, 1)
        self.assertEqual(draft.room_type, "king")
        self.assertEqual(draft.guest_name, "Hafiz")
        self.assertIn("check-in date", draft.missing_fields)
        self.assertIn("contact confirmation", draft.missing_fields)

    def test_structured_reservation_requires_every_final_field(self) -> None:
        draft = ReservationDraft(
            check_in="2026-08-26",
            check_out="2026-08-27",
            guest_count=2,
            room_count=1,
            room_type="double",
            guest_name="Hafiz",
            contact_phone="+919876543210",
            contact_confirmed=True,
        )

        self.assertTrue(draft.ready_for_final_confirmation)
        self.assertEqual(draft.missing_fields, ())

    def test_call_state_seeds_entered_contact_and_customer_name(self) -> None:
        loaded = config.load_agent_config({})
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="outbound",
            participant_identity="test-callee",
            phone_number="+919876543210",
            customer_name="Hafiz",
        )

        self.assertEqual(state.reservation.contact_phone, "+919876543210")
        self.assertEqual(state.reservation.guest_name, "Hafiz")

    def test_processing_stays_silent_instead_of_speaking_fillers(self) -> None:
        policy = prompts.VOICE_POLICY.casefold()

        self.assertFalse(hasattr(session, "THINKING_FILLERS"))
        self.assertFalse(hasattr(session, "THINKING_FILLER_DELAY_SECONDS"))
        self.assertIn("remain silent until a substantive response is ready", policy)
        self.assertIn("never speak holding fillers", policy)

    def test_legacy_aliases_and_prompt_placeholders(self) -> None:
        loaded = config.load_agent_config(
            {
                "customer_name": "Sam",
                "agent_config": {
                    "agent_name": "Alex",
                    "company_name": "Example Support",
                    "system_prompt": (
                        "Help {customer_name} at {company_name}. You are {agent_name}."
                    ),
                    "tts_voice_id": "test-voice",
                    "llm_temperature": 0,
                },
            }
        )

        self.assertEqual(loaded.voice_id, "test-voice")
        self.assertEqual(loaded.temperature, 0)
        self.assertEqual(
            loaded.business_instructions,
            "Help Sam at Example Support. You are Alex.",
        )

    def test_defaults_are_general_purpose(self) -> None:
        with patch.dict(
            "os.environ",
            {"VOICE_AGENT_NAME": "", "AGENT_INSTRUCTIONS": ""},
        ):
            loaded = config.load_agent_config({})

        self.assertEqual(loaded.agent_name, "Alex")
        self.assertNotIn("hotel", loaded.business_instructions.casefold())
        self.assertNotIn("booking", loaded.business_instructions.casefold())

    def test_runtime_personality_speed_and_custom_instructions(self) -> None:
        loaded = config.load_agent_config(
            {
                "agent_config": {
                    "provider": "livekit-inference",
                    "personality": "professional",
                    "speaking_speed": 1.2,
                    "noise_suppression_level": 0.9,
                    "custom_instructions": "Explain difficult ideas simply.",
                }
            }
        )

        self.assertEqual(loaded.provider, "livekit-inference")
        self.assertEqual(loaded.personality, "professional")
        self.assertEqual(loaded.speaking_speed, 1.2)
        self.assertEqual(loaded.noise_suppression_level, 0.9)
        self.assertEqual(loaded.business_instructions, "Explain difficult ideas simply.")

    def test_nested_null_falls_back_to_top_level(self) -> None:
        loaded = config.load_agent_config(
            {
                "temperature": 0,
                "enable_background_audio": True,
                "agent_config": {
                    "temperature": None,
                    "enable_background_audio": None,
                },
            }
        )

        self.assertEqual(loaded.temperature, 0)
        self.assertTrue(loaded.enable_background_audio)

    def test_invalid_nested_values_fall_back_to_valid_top_level(self) -> None:
        loaded = config.load_agent_config(
            {
                "temperature": 0.8,
                "enable_background_audio": True,
                "language": "ar",
                "timezone": "UTC",
                "agent_config": {
                    "temperature": True,
                    "enable_background_audio": "maybe",
                    "language": "not-a-language",
                    "timezone": "../UTC",
                },
            }
        )

        self.assertEqual(loaded.temperature, 0.8)
        self.assertTrue(loaded.enable_background_audio)
        self.assertEqual(loaded.language, "ar")
        self.assertEqual(loaded.timezone, "UTC")

    def test_invalid_language_and_timezone_fall_back(self) -> None:
        loaded = config.load_agent_config({"language": "not-a-language", "timezone": "../UTC"})

        self.assertEqual(loaded.language, "en-US")
        self.assertEqual(loaded.timezone, config.DEFAULT_TIMEZONE)

    def test_call_log_id_is_strictly_positive_integer(self) -> None:
        self.assertEqual(config.positive_int(42), 42)
        self.assertEqual(config.positive_int("42"), 42)
        self.assertIsNone(config.positive_int(True))
        self.assertIsNone(config.positive_int(42.9))
        self.assertIsNone(config.positive_int("42.9"))


class TransferRequestTests(unittest.TestCase):
    def test_explicit_request_is_recognized_in_every_supported_language(self) -> None:
        examples = (
            "I want to speak to a human",
            "أريد التحدث مع موظف",
            "Jeg vil tale med en medarbejder",
            "Ich möchte mit einem Mitarbeiter sprechen",
            "Quiero hablar con un representante",
            "Je voudrais parler à un conseiller",
            "Ik wil met een medewerker spreken",
            "Quero falar com um atendente",
            "Jag vill prata med en medarbetare",
        )

        for request in examples:
            with self.subTest(request=request):
                self.assertTrue(call_tools.has_explicit_transfer_request(request))

    def test_negated_request_is_rejected(self) -> None:
        examples = (
            "I do not want to speak to a human",
            "A human is not needed",
            "لا أريد التحدث مع موظف",
            "Ich möchte nicht mit einem Mitarbeiter sprechen",
        )

        for request in examples:
            with self.subTest(request=request):
                self.assertFalse(call_tools.has_explicit_transfer_request(request))

    def test_substrings_are_not_treated_as_transfer_targets(self) -> None:
        self.assertFalse(
            call_tools.has_explicit_transfer_request(
                "I need the dimensions of the room",
            )
        )

    def test_hold_message_is_localized(self) -> None:
        non_english_languages = config.SUPPORTED_LANGUAGE_CODES - {"en"}
        self.assertTrue(non_english_languages <= call_tools.TRANSFER_HOLD_MESSAGES.keys())


class EndCallRequestTests(unittest.TestCase):
    def test_explicit_goodbye_or_hangup_is_accepted(self) -> None:
        examples = (
            "Thanks, that's all. Goodbye.",
            "Okay. Thank you.",
            "Nothing. Thank you.",
            "Nothing else, thanks.",
            "No, thank you.",
            "Thanks!",
            "Thank you very much.",
            "Please end the call",
            "No, nothing else",
            "هذا كل شيء، مع السلامة",
            "Danke, auf Wiedersehen",
            "Gracias, adiós",
        )

        for request in examples:
            with self.subTest(request=request):
                self.assertTrue(call_tools.has_explicit_end_call_request(request))

    def test_service_request_cannot_end_the_call(self) -> None:
        examples = (
            "I want to book a spa appointment at 10 AM.",
            "Please reserve a room for tomorrow.",
            "Thank you for checking the availability.",
            "I do not want to end the call.",
            "Don't hang up yet.",
        )

        for request in examples:
            with self.subTest(request=request):
                self.assertFalse(call_tools.has_explicit_end_call_request(request))

    def test_every_supported_language_has_a_farewell(self) -> None:
        self.assertTrue(call_tools.CALL_FAREWELLS.keys() >= config.SUPPORTED_LANGUAGE_CODES)


class FullDuplexSessionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _session(language: str):
        loaded = config.load_agent_config({"language": language})
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="web",
            participant_identity="test-participant",
        )
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "unit-test-google-key"}):
            return session.create_session(loaded, state)

    async def test_english_uses_flux_with_adaptive_interruption(self) -> None:
        session = self._session("en-US")

        self.assertIsNotNone(session.stt)
        assert session.stt is not None
        self.assertEqual(session.stt.model, "deepgram/flux-general-en")
        self.assertEqual(session.stt.capabilities.aligned_transcript, "word")
        self.assertEqual(session._interruption_detection, "adaptive")
        self.assertIsNotNone(session.vad)
        self.assertEqual(session.options.max_tool_steps, 5)
        self.assertEqual(session.options.endpointing.get("mode"), "dynamic")
        self.assertEqual(session.options.endpointing.get("min_delay"), 0.35)
        self.assertEqual(session.options.endpointing.get("max_delay"), 2.0)
        self.assertFalse(session.options.interruption.get("resume_false_interruption"))
        self.assertFalse(session.options.preemptive_generation.get("preemptive_tts"))
        self.assertEqual(session.options.preemptive_generation.get("max_retries"), 2)

        self.assertIsNotNone(session.llm)
        assert session.llm is not None
        self.assertEqual(session.llm.model, "gemini-3.5-flash-lite")
        self.assertIsNotNone(session.tts)
        assert session.tts is not None
        self.assertEqual(session.tts.model, "cartesia/sonic-3")
        self.assertTrue(session.tts.capabilities.streaming)

    async def test_arabic_uses_supported_aligned_model(self) -> None:
        session = self._session("ar")

        self.assertIsNotNone(session.stt)
        assert session.stt is not None
        self.assertEqual(session.stt.model, "deepgram/nova-3")
        self.assertEqual(session.stt.capabilities.aligned_transcript, "word")
        self.assertEqual(session._interruption_detection, "adaptive")

    async def test_phone_audio_accepts_quiet_short_answers_faster(self) -> None:
        loaded = config.load_agent_config({"language": "en-US"})
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="outbound",
            participant_identity="test-callee",
        )
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "unit-test-google-key"}):
            phone_session = session.create_session(loaded, state)

        self.assertEqual(phone_session.options.endpointing.get("min_delay"), 0.25)
        self.assertEqual(phone_session.options.endpointing.get("max_delay"), 1.2)
        assert phone_session.vad is not None
        vad_options = cast(Any, phone_session.vad)._opts
        self.assertEqual(vad_options.activation_threshold, 0.32)
        self.assertEqual(vad_options.prefix_padding_duration, 0.35)
        self.assertGreaterEqual(vad_options.min_silence_duration, 0.25)

    async def test_standalone_thanks_plays_farewell_then_ends(self) -> None:
        class FakeJobContext:
            shutdown = MagicMock()

            @staticmethod
            def is_fake_job() -> bool:
                return True

        loaded = config.load_agent_config(
            {"language": "en-US", "company_name": "Grand Hayat"}
        )
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=cast(Any, FakeJobContext()),
            direction="outbound",
            participant_identity="test-callee",
        )
        agent = session.VoiceAgent(state)
        speech = MagicMock()
        speech.wait_for_playout = AsyncMock()
        fake_session = MagicMock()
        fake_session.say.return_value = speech
        turn_context = session.llm.ChatContext.empty()
        message = session.llm.ChatMessage(role="user", content=["Nothing. Thank you."])

        with patch.object(
            session.VoiceAgent,
            "session",
            new_callable=PropertyMock,
            return_value=fake_session,
        ), self.assertRaises(session.llm.StopResponse):
            await agent.on_user_turn_completed(turn_context, message)

        fake_session.say.assert_called_once()
        speech.wait_for_playout.assert_awaited_once()
        fake_session.shutdown.assert_called_once_with(drain=True)
        self.assertEqual(state.status, "completed")

    async def test_final_confirmation_speaks_closing_before_ending(self) -> None:
        class FakeJobContext:
            shutdown = MagicMock()

            @staticmethod
            def is_fake_job() -> bool:
                return True

        loaded = config.load_agent_config(
            {"language": "en-US", "company_name": "Grand Hayat"}
        )
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=cast(Any, FakeJobContext()),
            direction="outbound",
            participant_identity="test-callee",
            phone_number="+919876543210",
            reservation=ReservationDraft(
                check_in="2026-08-25",
                check_out="2026-08-26",
                guest_count=2,
                room_count=1,
                room_type="king",
                guest_name="Hafiz",
                contact_phone="+919876543210",
                contact_confirmed=True,
            ),
            last_assistant_text=(
                "Please confirm that you would like me to record this reservation request?"
            ),
        )
        agent = session.VoiceAgent(state)
        speech = MagicMock()
        speech.wait_for_playout = AsyncMock()
        fake_session = MagicMock()
        fake_session.say.return_value = speech
        turn_context = session.llm.ChatContext.empty()
        message = session.llm.ChatMessage(role="user", content=["Yes, please."])

        with patch.object(
            session.VoiceAgent,
            "session",
            new_callable=PropertyMock,
            return_value=fake_session,
        ), self.assertRaises(session.llm.StopResponse):
            await agent.on_user_turn_completed(turn_context, message)

        closing = fake_session.say.call_args.args[0]
        self.assertIn("Thank you for confirming", closing)
        self.assertIn("Grand Hayat", closing)
        self.assertIn("Goodbye", closing)
        speech.wait_for_playout.assert_awaited_once()
        fake_session.shutdown.assert_called_once_with(drain=True)
        self.assertEqual(state.status, "completed")
        self.assertTrue(state.reservation.final_confirmed)

    async def test_room_remains_available_for_worker_recovery(self) -> None:
        with patch.object(session.ai_coustics, "audio_enhancement") as enhancement:
            options = session.make_room_options(
                "test-participant",
                noise_suppression_level=0.85,
            )

        self.assertTrue(options.close_on_disconnect)
        self.assertFalse(options.delete_room_on_close)
        self.assertIsInstance(options.text_output, session.room_io.TextOutputOptions)
        assert isinstance(options.text_output, session.room_io.TextOutputOptions)
        self.assertTrue(options.text_output.sync_transcription)
        model_parameters = enhancement.call_args.kwargs["model_parameters"]
        self.assertEqual(model_parameters.enhancement_level, 0.85)

    async def test_english_greeting_names_hotel_and_purpose_once(self) -> None:
        class FakeSession:
            calls: list[tuple[str, dict[str, Any]]]

            def __init__(self) -> None:
                self.calls = []

            async def say(self, text: str, **kwargs: Any) -> None:
                self.calls.append((text, kwargs))

        loaded = config.load_agent_config(
            {
                "agent_name": "Ava",
                "company_name": "Grand Hayat",
                "language": "en-US",
                "instructions": "Act as the hotel front desk.",
            }
        )
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="web",
            participant_identity="test-participant",
        )
        fake_session = FakeSession()

        await session.greet_caller(cast(Any, fake_session), state)

        self.assertEqual(len(fake_session.calls), 1)
        greeting = fake_session.calls[0][0]
        self.assertIn("Ava", greeting)
        self.assertIn("Grand Hayat", greeting)
        self.assertIn("reservations", greeting)
        self.assertEqual(greeting.count("?"), 1)

    async def test_background_audio_uses_office_and_keyboard_clips(self) -> None:
        class FakeJobContext:
            room = object()

        loaded = config.load_agent_config({"enable_background_audio": True})
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=cast(Any, FakeJobContext()),
            direction="web",
            participant_identity="test-participant",
        )
        fake_session = cast(Any, object())
        player = AsyncMock()

        with patch.object(
            session,
            "BackgroundAudioPlayer",
            return_value=player,
        ) as player_class:
            await session.start_background_audio(state, fake_session)

        ambient = player_class.call_args.kwargs["ambient_sound"]
        thinking = player_class.call_args.kwargs["thinking_sound"]
        self.assertEqual(ambient.source, session.BuiltinAudioClip.OFFICE_AMBIENCE)
        self.assertEqual(ambient.volume, session.AMBIENT_OFFICE_VOLUME)
        self.assertLess(ambient.volume, 0.1)
        self.assertEqual(len(thinking), 2)
        self.assertEqual(thinking[0].source, session.BuiltinAudioClip.KEYBOARD_TYPING)
        self.assertEqual(thinking[0].volume, session.THINKING_KEYBOARD_VOLUME)
        self.assertEqual(thinking[1].source, session.BuiltinAudioClip.KEYBOARD_TYPING2)
        self.assertEqual(thinking[1].volume, session.THINKING_KEYBOARD2_VOLUME)
        assert state.job_ctx is not None
        player.start.assert_awaited_once_with(
            room=state.job_ctx.room,
            agent_session=fake_session,
        )
        self.assertIs(state.background_audio, player)

    async def test_state_cleanup_does_not_wait_forever_for_background_audio(self) -> None:
        class HangingBackgroundAudio:
            async def aclose(self) -> None:
                await asyncio.Event().wait()

        loaded = config.load_agent_config({})
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="web",
            participant_identity="test-participant",
        )
        state.background_audio = cast(Any, HangingBackgroundAudio())

        with patch(
            "backend.voice_agent.state.BACKGROUND_AUDIO_CLOSE_TIMEOUT_SECONDS",
            0.01,
        ):
            await asyncio.wait_for(state.aclose("test shutdown"), timeout=0.5)

        self.assertIsNone(state.background_audio)
        self.assertEqual(state.status, "completed")


class OutboundPickupTests(unittest.IsolatedAsyncioTestCase):
    async def test_answered_call_returns_immediately_for_greeting(self) -> None:
        loaded = config.load_agent_config({"language": "en-US"})
        state = CallState(
            config=loaded,
            backend=BackendClient(),
            job_ctx=None,
            direction="outbound",
            participant_identity="callee-test",
        )
        sip = AsyncMock()
        sip.create_sip_participant.return_value = object()
        fake_context = type(
            "FakeContext",
            (),
            {
                "api": type("FakeApi", (), {"sip": sip})(),
                "room": type("FakeRoom", (), {"name": "outbound-test"})(),
                "wait_for_participant": AsyncMock(return_value=object()),
            },
        )()

        with patch.dict("os.environ", {"SIP_OUTBOUND_TRUNK_ID": "ST_test"}):
            result = await worker.place_outbound_call(
                cast(Any, fake_context),
                cast(Any, object()),
                state,
                "+919876543210",
            )

        self.assertEqual(result, "greet")
        cast(Any, fake_context).wait_for_participant.assert_awaited_once_with(
            identity="callee-test"
        )
        request = sip.create_sip_participant.await_args.args[0]
        self.assertTrue(request.wait_until_answered)


class CallTerminationTests(unittest.IsolatedAsyncioTestCase):
    async def test_console_job_uses_explicit_shutdown(self) -> None:
        class FakeJobContext:
            shutdown_reason: str | None = None

            @staticmethod
            def is_fake_job() -> bool:
                return True

            @staticmethod
            async def delete_room() -> None:
                raise AssertionError("delete_room must not be used for a console job")

            def shutdown(self, *, reason: str = "") -> None:
                self.shutdown_reason = reason

        class FakeSession:
            shutdown_called = False

            def shutdown(self, *, drain: bool = True) -> None:
                self.shutdown_called = drain

        job_context = FakeJobContext()
        session = FakeSession()

        await call_tools.terminate_live_call(
            cast(Any, job_context),
            cast(Any, session),
            "test complete",
        )

        self.assertTrue(session.shutdown_called)
        self.assertEqual(job_context.shutdown_reason, "test complete")


if __name__ == "__main__":
    unittest.main()
