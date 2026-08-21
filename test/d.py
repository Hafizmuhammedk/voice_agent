"""Real-Time Voice Agent - Clean Implementation"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
import aiohttp

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    JobRequest,
    WorkerOptions,
    cli,
    inference,
    function_tool,
    RunContext,
    AutoSubscribe,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
)
from livekit.agents.voice import room_io
from livekit.plugins import silero, cartesia, deepgram, noise_cancellation, openai as openai_stt, google

# Import from config module
from backend.agent_config import (
    load_agent_config,
    get_customer,
    update_call_log,
    create_inbound_call_log,
)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

logger = logging.getLogger("voice-agent")
logger.setLevel(logging.INFO)

# Environment config
OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID")


# =============================================================================
# VOICE AGENT
# =============================================================================
class VoiceAgent(Agent):
    """Voice agent with tool functions"""

    def __init__(self, *, instructions: str, call_log_id: int | None = None):
        super().__init__(instructions=instructions)
        self.call_log_id = call_log_id

    @function_tool()
    async def end_call(self, ctx: RunContext, reason: str = "conversation_complete"):
        """End the call. Call this ONLY after you have already said goodbye verbally.
        
        Args:
            reason: Brief reason for ending (e.g., "customer said bye", "booking complete", "conversation_complete")
        """
        logger.info(f"Ending call - reason: {reason}")
        if self.call_log_id:
            await update_call_log(self.call_log_id, status="completed", outcome=reason)
        
        # Wait briefly for TTS to finish, then disconnect immediately
        import asyncio
        await asyncio.sleep(1.5)
        try:
            await ctx.room.disconnect()
        except Exception:
            pass
        
        # Return empty - don't make agent say anything else
        return ""

    @function_tool()
    async def transfer_to_human(self, ctx: RunContext, caller_exact_request: str):
        """Transfer to a human agent. Only call this when caller explicitly requests to speak with a manager, supervisor, or human.
        
        Args:
            caller_exact_request: The exact words the caller used to request a human. Must contain "manager", "supervisor", "human", or "speak to someone". If caller did not explicitly ask for a human, do NOT call this function.
        """
        from livekit.agents import ToolError
        
        valid_keywords = ["manager", "supervisor", "human", "person", "someone else", "real person", "speak to someone"]
        
        if not caller_exact_request or not any(kw in caller_exact_request.lower() for kw in valid_keywords):
            logger.warning(f"transfer rejected - caller said: '{caller_exact_request}' which is not a request for human")
            raise ToolError(f"Cannot transfer. '{caller_exact_request}' is not a request for a human. Only transfer when caller explicitly asks for a manager, supervisor, or human. Continue helping the caller yourself.")
            
        logger.info(f"Transfer requested: {caller_exact_request}")
        if self.call_log_id:
            await update_call_log(self.call_log_id, status="transferred", outcome=f"transfer:{caller_exact_request}")
        return "Let me connect you with a team member who can better assist you. Please hold."

    @function_tool()
    async def detected_voicemail(self, ctx: RunContext):
        """Call this only if you hear an automated voicemail greeting saying "leave a message after the beep" or similar. Do not use for silence or human voices."""
        logger.info("Voicemail detected")
        if self.call_log_id:
            await update_call_log(self.call_log_id, status="voicemail", outcome="no_answer")
        try:
            await ctx.room.disconnect()
        except Exception:
            pass

    @function_tool()
    async def log_customer_sentiment(self, ctx: RunContext, sentiment: str, exact_customer_words: str):
        """Track customer emotion ONLY when they EXPLICITLY express frustration in clear words.
        
        ONLY call this when the customer CLEARLY says phrases like:
        - "This is frustrating" / "I'm frustrated" / "هذا محبط"
        - "I'm angry" / "This is ridiculous" / "أنا غاضب"
        - "I don't understand" / "لا أفهم"
        
        DO NOT call this for:
        - Unintelligible speech, garbled audio, or random characters
        - Speech in a different language than expected
        - Background noise, silence, or unclear audio
        - Normal questions, statements, or greetings
        - When you simply don't understand what was said
        
        Args:
            sentiment: One of: "frustrated", "angry", "confused"
            exact_customer_words: The EXACT words the customer said. Must be real words, not garbled text.
        """
        from livekit.agents import ToolError
        
        if not exact_customer_words or len(exact_customer_words) < 3:
            raise ToolError("Cannot log sentiment without the customer's exact words. Do not call this for unclear audio.")
        
        logger.info(f"Customer sentiment: {sentiment} - {exact_customer_words}")
        
        if sentiment == "angry":
            return "Customer is angry. Sincerely apologize and prioritize resolving their issue."
        elif sentiment == "frustrated":
            return "Customer is frustrated. Acknowledge their feelings and be extra helpful."
        elif sentiment == "confused":
            return "Customer is confused. Explain things more simply."
        else:
            return "Continue helping the customer normally."

    @function_tool()
    async def save_booking_details(
        self,
        ctx: RunContext,
        client_name: str | None = None,
        check_in_date: str | None = None,
        check_out_date: str | None = None,
        room_type: str | None = None,
        num_guests: int | None = None,
        special_requests: str | None = None,
    ):
        """Save or update booking details collected from the conversation.
        
        Call this function whenever you collect ANY booking information from the customer.
        You can call it multiple times as you gather more details.
        
        Args:
            client_name: Customer's name (e.g., "John Smith")
            check_in_date: Check-in date (e.g., "January 15, 2026")
            check_out_date: Check-out date (e.g., "January 18, 2026")
            room_type: Type of room (e.g., "deluxe", "suite", "standard")
            num_guests: Number of guests
            special_requests: Any special requests or notes
        """
        if not self.call_log_id:
            logger.warning("Cannot save booking - no call_log_id")
            return "Booking information noted."
        
        try:
            api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
            async with aiohttp.ClientSession() as http:
                data = {}
                if client_name:
                    data["client_name"] = client_name
                if check_in_date:
                    data["check_in_date"] = check_in_date
                if check_out_date:
                    data["check_out_date"] = check_out_date
                if room_type:
                    data["room_type"] = room_type
                if num_guests:
                    data["num_guests"] = num_guests
                if special_requests:
                    data["special_requests"] = special_requests
                
                if data:
                    await http.post(
                        f"{api_base}/api/booking/{self.call_log_id}",
                        json=data,
                        timeout=aiohttp.ClientTimeout(total=5)
                    )
                    logger.info(f"Saved booking details: {data}")
        except Exception as e:
            logger.warning(f"Failed to save booking details: {e}")
        
        return "Booking information saved. Continue assisting the customer."


# =============================================================================
# AGENT LIFECYCLE
# =============================================================================
def prewarm(proc: JobProcess):
    """Preload VAD model with balanced settings"""
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.1,
        min_silence_duration=0.45,
        prefix_padding_duration=0.15,
        activation_threshold=0.5,
    )


async def entrypoint(ctx: JobContext):
    """Main agent entry point"""
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Detect call direction - inbound calls have SIP participant already in room
    # Wait briefly for participants to appear (SIP participant may join slightly after)
    is_inbound = False
    caller_number = None
    
    # Log current participants for debugging
    logger.info(f"🔍 Checking for SIP participants... remote_participants count: {len(ctx.room.remote_participants)}")
    
    # Wait up to 2 seconds for SIP participant to appear
    for attempt in range(10):
        for participant in ctx.room.remote_participants.values():
            logger.debug(f"Found participant: identity={participant.identity}")
            # Check if participant is a SIP caller (identity starts with sip_)
            if participant.identity.startswith("sip_"):
                is_inbound = True
                caller_number = participant.identity[4:]  # Remove "sip_" prefix
                logger.info(f"📞 INBOUND CALL detected from: {caller_number}")
                break
        if is_inbound:
            break
        await asyncio.sleep(0.2)  # Wait 200ms between checks
    
    if not is_inbound:
        logger.info(f"No SIP participant found after wait. Treating as outbound call.")
    
    # Parse metadata (for outbound calls)
    try:
        metadata = ctx.job.metadata
        if isinstance(metadata, str):
            dial_info = {"phone_number": metadata} if metadata.startswith('+') else (json.loads(metadata) if metadata else {})
        else:
            dial_info = metadata or {}
    except (json.JSONDecodeError, TypeError):
        dial_info = {}

    phone_number = caller_number if is_inbound else dial_info.get("phone_number")
    # Use mutable container so call_log_id can be updated later (for inbound calls)
    call_ctx = {"call_log_id": dial_info.get("call_log_id")}
    prompt_id = dial_info.get("prompt_id")
    is_self_review = dial_info.get("is_self_review", False)
    participant_identity = phone_number or "self-review-user"
    
    logger.info(f"Call: {'INBOUND' if is_inbound else 'OUTBOUND'}, Phone: {phone_number}, prompt_id: {prompt_id}")
    logger.info(f"Metadata received: {dial_info}, initial call_log_id: {call_ctx['call_log_id']}")

    # Get customer name
    customer = await get_customer(phone_number) if phone_number and not is_self_review else None
    customer_name = customer.name if customer else dial_info.get("customer_name", "there")

    # Load configuration from database (prompt or settings)
    config = await load_agent_config(
        prompt_id=prompt_id,
        customer_name=customer_name,
        is_inbound=is_inbound
    )

    logger.info(f"Call started: customer={customer_name}, agent={config.agent_name}")

    # Add language-specific instruction to ensure agent responds in the correct language
    stt_language = config.language[:2] if config.language else "en"
    if stt_language == "ar":
        # Add smart conversation rules for Arabic with date awareness
        current_date_ar = datetime.now().strftime("%d/%m/%Y")  # e.g., "04/01/2026"
        current_date_en = datetime.now().strftime("%B %d, %Y")  # For internal reference
        tomorrow_date_ar = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        language_instruction = f"""## تعليمات المحادثة بالعربية
تحدث بالعربية فقط في جميع ردودك.

### التاريخ الحالي (مهم جداً):
- تاريخ اليوم: {current_date_ar}
- تاريخ الغد: {tomorrow_date_ar}
- اقبل الحجوزات فقط لتاريخ اليوم أو المستقبل
- إذا ذكر المتصل تاريخاً في الماضي، قل بلطف: "عذراً، هذا التاريخ قد مضى. اليوم هو {current_date_ar}. هل تريد الحجز لتاريخ قادم؟"

### قواعد حاسمة - لا تكرر الأسئلة أبداً:
- تذكر جميع المعلومات التي قدمها المتصل
- إذا لم تفهم شيئاً بوضوح، استخدم السياق لفهم المقصود
- لا تسأل نفس السؤال مرتين أبداً - اعترف بما فهمته وتابع
- إذا كنت غير متأكد من تفصيل معين، اسأل مرة واحدة فقط ثم تابع

### التعامل مع الكلام غير الواضح:
- قد تحتوي النصوص على أخطاء إملائية بسبب التحويل الصوتي
- حاول فهم المقصود من السياق بدلاً من طلب التكرار
- إذا قال المتصل "أريد غرفة" أو ما يشبهها، افهم أنه يريد حجز غرفة
- للأسماء، اقبل التقريبات الصوتية ولا تطلب التهجئة

### كن ذكياً وفعالاً:
- اعترف بكل إجابة قبل طرح السؤال التالي
- لخص ما فهمته للتأكيد
- تقدم بالمحادثة بثقة

"""
    else:
        # Add smart conversation rules for English with date awareness
        current_date = datetime.now().strftime("%B %d, %Y")  # e.g., "January 04, 2026"
        tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%B %d, %Y")
        language_instruction = f"""## SMART CONVERSATION RULES
Today's date is: {current_date}

### CRITICAL - Never Repeat Questions:
- Keep track of information the user has already provided in this conversation
- If STT captured something unclear, make reasonable assumptions from context
- NEVER ask the same question twice - acknowledge what you understood and move forward
- If unsure about a detail, confirm it ONCE then proceed

### Date Validation (VERY IMPORTANT):
- Today is {current_date}. Tomorrow is {tomorrow_date}.
- ONLY accept bookings for TODAY or FUTURE dates
- If user mentions ANY past date (before {current_date}), politely say: "I'm sorry, that date has already passed. Today is {current_date}. Would you like to book for an upcoming date instead?"
- Parse relative dates naturally: "tomorrow" means {tomorrow_date}, "next week" means 7 days from today

### Handling Unclear Speech:
- If user says something unclear, use context clues to understand intent
- Ask clarifying questions only for critical booking info (dates, room type, number of guests)
- For names, accept phonetic approximations - don't ask to spell
- If you hear a number that sounds reasonable, accept it

### Be Smart and Efficient:
- Acknowledge each response before asking the next question
- Summarize what you understood to confirm
- Move the conversation forward with confidence

"""
    
    # Initialize agent with instructions including language directive
    full_instructions = language_instruction + config.instructions
    agent = VoiceAgent(instructions=full_instructions, call_log_id=call_ctx["call_log_id"])

    # Create session with STT, LLM, TTS
    # Google STT for Arabic - excellent accuracy
    # Cartesia ink-whisper for English - fast and efficient
    if stt_language == "ar":
        # Get credentials path from environment variable
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        stt_instance = google.STT(
            model="long",
            languages=["ar-SA"],
            spoken_punctuation=False,
            credentials_file=credentials_path,
        )
        logger.info(f"Using Google Cloud STT for Arabic (credentials: {credentials_path})")
    else:
        # Deepgram Flux for English - designed for real-time conversational AI
        stt_instance = deepgram.STTv2(
            model="flux-general-en",
            eager_eot_threshold=0.4,  # Better turn detection
        )
        logger.info("Using Deepgram Flux STT for conversational AI")
    
    # Determine TTS speed based on language (slower for Arabic for clarity)
    tts_speed = 0.9 if stt_language == "ar" else 1.0
    
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=stt_instance,
        llm=inference.LLM(
            model="google/gemini-2.5-flash-lite",
            extra_kwargs={"max_completion_tokens": 500, "temperature": config.temperature}
        ),
        tts=cartesia.TTS(
            model="sonic-3",
            voice=config.voice_id,
            language=config.language[:2] if config.language else "en",
            speed=tts_speed,
            volume=1.5
        ),
        allow_interruptions=True,
        min_endpointing_delay=0.5,
        preemptive_generation=True,
    )

    # ==========================================================================
    # LIVE TRANSCRIPTION CAPTURE
    # ==========================================================================
    api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
    
    async def save_transcript(speaker: str, text: str, emotion: str = "neutral"):
        """Save transcript to database via API"""
        current_call_log_id = call_ctx["call_log_id"]
        logger.info(f"📝 Saving transcript: speaker={speaker}, call_log_id={current_call_log_id}, text_len={len(text) if text else 0}")
        if not current_call_log_id:
            logger.warning(f"❌ Cannot save transcript - call_log_id is None")
            return
        if not text or not text.strip():
            logger.warning(f"❌ Cannot save transcript - text is empty")
            return
        try:
            async with aiohttp.ClientSession() as http:
                resp = await http.post(
                    f"{api_base}/api/transcripts",
                    json={
                        "call_log_id": current_call_log_id,
                        "speaker": speaker,
                        "text": text,
                        "timestamp": datetime.now().timestamp(),
                        "emotion": emotion,
                    },
                    timeout=aiohttp.ClientTimeout(total=5)
                )
                logger.info(f"✅ Transcript saved: status={resp.status}")
        except Exception as e:
            logger.warning(f"❌ Failed to save transcript: {e}")
    
    @session.on("agent_speech_committed")
    def on_agent_speech(message):
        """Capture agent speech and save to database"""
        logger.debug(f"🎤 Agent speech event: {type(message)}, has_content={hasattr(message, 'content')}")
        if hasattr(message, 'content') and message.content:
            logger.info(f"🎤 Agent said: {message.content[:100]}...")
            asyncio.create_task(save_transcript("agent", message.content))
    
    @session.on("user_speech_committed")
    def on_user_speech(message):
        """Capture user speech and save to database"""
        logger.debug(f"👤 User speech event: {type(message)}, has_content={hasattr(message, 'content')}")
        if hasattr(message, 'content') and message.content:
            logger.info(f"👤 User said: {message.content[:100]}...")
            asyncio.create_task(save_transcript("customer", message.content, "neutral"))
    
    @session.on("close")
    def on_session_close():
        """Mark call as completed when session ends"""
        final_call_log_id = call_ctx["call_log_id"]
        if final_call_log_id:
            async def mark_completed():
                await update_call_log(final_call_log_id, status="completed")
                logger.info(f"Call {final_call_log_id} marked as completed")
            asyncio.create_task(mark_completed())

    # Background audio
    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=1.0),
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.5),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.4),
        ],
    )

    # Start session
    session_task = asyncio.create_task(
        session.start(
            room=ctx.room,
            agent=agent,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=noise_cancellation.BVC(),
                    # noise_cancellation=noise_cancellation.BVCTelephony(),
                ),
            ),
        )
    )

    await background_audio.start(room=ctx.room, agent_session=session)

    async def greet():
        if stt_language == "ar":
            if is_inbound:
                await session.generate_reply(
                    instructions=f"اشكر المتصل على اتصاله بـ {config.company_name}. قدم نفسك باسم {config.agent_name}. اسأل كيف يمكنك مساعدتهم اليوم. كن موجزًا ودافئًا. تحدث بالعربية فقط."
                )
            else:
                await session.generate_reply(
                    instructions=f"حيِّ {customer_name}. قدم نفسك باسم {config.agent_name} من {config.company_name}. اسأل كيف يمكنك المساعدة. كن موجزًا. تحدث بالعربية فقط."
                )
        else:
            if is_inbound:
                await session.generate_reply(
                    instructions=f"Thank the caller for calling {config.company_name}. Introduce yourself as {config.agent_name}. Ask how you can help them today. Keep it brief and warm."
                )
            else:
                await session.generate_reply(
                    instructions=f"Greet {customer_name}. Introduce yourself as {config.agent_name} from {config.company_name}. Ask how you can help. Keep it brief."
                )

    # Handle call based on direction
    if is_inbound:
        logger.info(f"📞 Handling inbound call from {phone_number}")
        
        # Create call log for inbound call
        if phone_number:
            new_call_log_id = await create_inbound_call_log(phone_number, ctx.room.name)
            if new_call_log_id:
                call_ctx["call_log_id"] = new_call_log_id  # Update container
                agent.call_log_id = new_call_log_id
                logger.info(f"Created inbound call log: {new_call_log_id}")
        
        await session_task
        asyncio.create_task(greet())
        
    elif phone_number and OUTBOUND_TRUNK_ID:
        # OUTBOUND CALL - Need to dial out
        try:
            logger.info(f"📱 Dialing outbound to {phone_number}...")
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=OUTBOUND_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=participant_identity,
                    krisp_enabled=True,
                )
            )
            await session_task
            await ctx.wait_for_participant(identity=participant_identity)
            
            if call_ctx["call_log_id"]:
                await update_call_log(call_ctx["call_log_id"], status="connected")
            
            asyncio.create_task(greet())
        except Exception as e:
            logger.error(f"Outbound call failed: {e}")
            if call_ctx["call_log_id"]:
                await update_call_log(call_ctx["call_log_id"], status="failed")
            ctx.shutdown()
    else:
        # SELF-REVIEW / WEB CALL - Wait for participant to join
        try:
            await asyncio.wait_for(
                ctx.wait_for_participant(identity=participant_identity),
                timeout=15.0
            )
            await session_task
            asyncio.create_task(greet())
        except asyncio.TimeoutError:
            logger.warning("No participant joined")
            ctx.shutdown()


async def send_heartbeat():
    """Send periodic heartbeat to backend API"""
    import aiohttp
    api_url = os.getenv("BACKEND_API_URL", "http://localhost:8000")
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{api_url}/api/agent/heartbeat") as resp:
                    if resp.status == 200:
                        logger.debug("Heartbeat sent successfully")
        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")
        
        await asyncio.sleep(10)  # Send heartbeat every 10 seconds


async def on_shutdown():
    """Notify backend when agent shuts down"""
    import aiohttp
    api_url = os.getenv("BACKEND_API_URL", "http://localhost:8000")
    
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(f"{api_url}/api/agent/shutdown")
    except Exception:
        pass


async def request_handler(req: JobRequest) -> None:
    """Accept all incoming job requests (inbound & outbound calls)"""
    logger.info(f"📥 Job request received: room={req.room.name}, metadata={req.job.metadata}")
    await req.accept()


if __name__ == "__main__":
    # Start heartbeat in background
    import threading
    
    def run_heartbeat():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_heartbeat())
    
    heartbeat_thread = threading.Thread(target=run_heartbeat, daemon=True)
    heartbeat_thread.start()
    
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            request_fnc=request_handler,
            agent_name="voice-agent",
        )
    )