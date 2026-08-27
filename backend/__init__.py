"""Standalone FastAPI backend for the voice-agent platform."""

from .app.main import create_app

__all__ = ["create_app"]
