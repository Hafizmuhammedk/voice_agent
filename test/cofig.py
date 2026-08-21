"""Agent Configuration Module - Database operations and settings loading"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from backend.db.database_optimized import SessionLocal
from backend.api.models.customer import Customer
from backend.api.models.call_log import CallLog
from backend.api.models.prompt import Prompt
from backend.api.models.agent_settings import AgentSettings

logger = logging.getLogger("voice-agent")

# Thread pool for non-blocking database operations
_db_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db")

# Default voice ID (Cartesia - Zara)
DEFAULT_VOICE_ID = "f786b574-daa5-4673-aa0c-cbe3e8534c02"


@dataclass
class AgentConfig:
    """Configuration loaded from database"""
    agent_name: str
    company_name: str
    instructions: str
    voice_id: str
    temperature: float
    language: str
    allow_interruptions: bool
    enable_background_audio: bool


async def get_settings() -> dict | None:
    """Fetch agent settings from database"""
    def _query():
        db = SessionLocal()
        try:
            settings = db.query(AgentSettings).first()
            if settings:
                return {
                    "agent_name": settings.agent_name,
                    "company_name": settings.company_name,
                    "system_prompt": settings.system_prompt,
                    "tts_voice_id": settings.tts_voice_id,
                    "llm_temperature": settings.llm_temperature,
                    "language": settings.language,  # Add language from settings
                    "allow_interruptions": settings.allow_interruptions,
                    "enable_background_audio": settings.enable_background_audio,
                }
            return None
        finally:
            db.close()
    
    return await asyncio.get_event_loop().run_in_executor(_db_executor, _query)


async def get_customer(phone_number: str) -> Customer | None:
    """Fetch customer by phone number"""
    def _query():
        db = SessionLocal()
        try:
            return db.query(Customer).filter(Customer.phone_number == phone_number).first()
        finally:
            db.close()
    
    return await asyncio.get_event_loop().run_in_executor(_db_executor, _query)


async def get_prompt(prompt_id: int) -> dict | None:
    """Fetch prompt by ID"""
    def _query():
        db = SessionLocal()
        try:
            prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()
            if prompt:
                return {
                    "id": prompt.id,
                    "name": prompt.name,
                    "instructions": prompt.instructions,
                    "agent_name": prompt.agent_name,
                    "company_name": getattr(prompt, 'company_name', None),
                    "voice": prompt.voice,
                    "temperature": prompt.temperature,
                    "language": prompt.language,
                }
            return None
        finally:
            db.close()
    
    return await asyncio.get_event_loop().run_in_executor(_db_executor, _query)


async def update_call_log(call_log_id: int, **kwargs):
    """Update call log - fire and forget"""
    def _update():
        db = SessionLocal()
        try:
            call_log = db.query(CallLog).filter(CallLog.id == call_log_id).first()
            if call_log:
                for key, value in kwargs.items():
                    setattr(call_log, key, value)
                db.commit()
        except Exception as e:
            logger.error(f"DB update error: {e}")
            db.rollback()
        finally:
            db.close()
    
    asyncio.get_event_loop().run_in_executor(_db_executor, _update)


async def create_inbound_call_log(phone_number: str, room_name: str) -> int | None:
    """Create call log for inbound call"""
    def _create():
        db = SessionLocal()
        try:
            # Find customer by phone
            customer = db.query(Customer).filter(Customer.phone_number == phone_number).first()
            new_log = CallLog(
                customer_id=customer.id if customer else None,
                room_name=room_name,
                status="connected",
                direction="inbound",
                caller_number=phone_number,
            )
            db.add(new_log)
            db.commit()
            return new_log.id
        except Exception as e:
            logger.error(f"Failed to create call log: {e}")
            db.rollback()
            return None
        finally:
            db.close()
    
    return await asyncio.get_event_loop().run_in_executor(_db_executor, _create)


async def load_agent_config(
    prompt_id: int | str | None = None,
    customer_name: str = "there",
    is_inbound: bool = False
) -> AgentConfig:
    """
    Load agent configuration from database.
    Priority: prompt_id > settings > minimal fallback
    """
    logger.info(f"load_agent_config called: prompt_id={prompt_id} (type={type(prompt_id).__name__}), customer={customer_name}, inbound={is_inbound}")
    
    # Try to load from prompt first
    if prompt_id:
        # Convert to int if string
        try:
            prompt_id_int = int(prompt_id)
        except (ValueError, TypeError):
            logger.error(f"Invalid prompt_id: {prompt_id}")
            prompt_id_int = None
        
        if prompt_id_int:
            prompt_data = await get_prompt(prompt_id_int)
            logger.info(f"Prompt data fetched: {prompt_data}")
            
            if prompt_data and prompt_data.get("instructions"):
                instructions = prompt_data["instructions"]
                agent_name = prompt_data.get("agent_name") or "Assistant"
                company_name = prompt_data.get("company_name") or "Company"
                
                # Replace template variables
                instructions = instructions.replace("{customer_name}", customer_name)
                instructions = instructions.replace("{agent_name}", agent_name)
                instructions = instructions.replace("{company_name}", company_name)
                
                logger.info(f"✅ Loaded prompt: {prompt_data.get('name')} (ID: {prompt_id_int})")
                logger.info(f"Instructions preview: {instructions[:200]}...")
                
                # Settings language takes priority over prompt language
                settings = await get_settings()
                settings_language = settings.get("language") if settings else None
                if settings_language and settings_language.strip():
                    language = settings_language
                else:
                    # No settings language, use prompt language
                    prompt_language = prompt_data.get("language")
                    language = prompt_language if prompt_language and prompt_language.strip() else "en-US"
                
                return AgentConfig(
                    agent_name=agent_name,
                    company_name=company_name,
                    instructions=instructions,
                    voice_id=prompt_data.get("voice") or DEFAULT_VOICE_ID,
                    temperature=float(prompt_data.get("temperature") or 0.3),
                    language=language,
                    allow_interruptions=True,
                    enable_background_audio=True,
                )
    
    # Try to load from settings
    settings = await get_settings()
    if settings and settings.get("system_prompt"):
        instructions = settings["system_prompt"]
        agent_name = settings.get("agent_name") or "Assistant"
        company_name = settings.get("company_name") or "Company"
        
        # Replace template variables
        instructions = instructions.replace("{customer_name}", customer_name)
        instructions = instructions.replace("{agent_name}", agent_name)
        instructions = instructions.replace("{company_name}", company_name)
        
        logger.info("Loaded config from settings")
        
        return AgentConfig(
            agent_name=agent_name,
            company_name=company_name,
            instructions=instructions,
            voice_id=settings.get("tts_voice_id") or DEFAULT_VOICE_ID,
            temperature=float(settings.get("llm_temperature") or 0.3),
            language=settings.get("language") or "en",
            allow_interruptions=settings.get("allow_interruptions", True),
            enable_background_audio=settings.get("enable_background_audio", True),
        )
    
    # Minimal fallback - only if nothing is configured
    logger.warning("No prompt or settings configured - using minimal fallback")
    
    fallback_instructions = """You are a helpful voice assistant.

## Your Role
- Listen carefully to the caller
- Answer their questions helpfully
- Keep responses concise (1-2 sentences)

## Important Rules
- ONLY use the tools provided to you: end_call, transfer_to_human, detected_voicemail
- Do NOT make up or call any other functions
- Do NOT end the call unless the caller explicitly says goodbye or asks to hang up
- If the caller asks for a manager or is frustrated, use transfer_to_human
- After helping, ask "Is there anything else I can help with?"

## Conversation Flow
1. Greet the caller warmly
2. Listen to their request
3. Provide helpful responses
4. Continue the conversation until they say goodbye
5. Only then use end_call with reason "caller_goodbye"
"""
    
    return AgentConfig(
        agent_name="Assistant",
        company_name="Company",
        instructions=fallback_instructions,
        voice_id=DEFAULT_VOICE_ID,
        temperature=0.3,
        language="en-US",
        allow_interruptions=True,
        enable_background_audio=True,
    )