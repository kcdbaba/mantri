"""
API Guard — monkey-patches cloud LLM SDK methods to enforce PERMIT_API.

Patches:
  - anthropic.resources.messages.Messages.create
  - google.genai.models.Models.generate_content
  - mistralai.Mistral.chat.complete (if installed)

Import this module early in any entry point (worker, replay, scripts).
When PERMIT_API=False (default), all LLM API calls raise PermissionError.
When PERMIT_API=True (production, --run-live), calls pass through to originals.

The guard lives UNDER our code — no amount of bugs in our codebase can
bypass it. The only way to make API calls is to set PERMIT_API=True.
"""

import logging

log = logging.getLogger(__name__)

_patched = False


class APICallBlocked(PermissionError):
    """Raised when an LLM API call is attempted with PERMIT_API=False."""
    pass


def activate():
    """Patch all known LLM SDK methods. Safe to call multiple times."""
    global _patched
    if _patched:
        return
    _patched = True

    _patch_anthropic()
    _patch_genai()
    _patch_mistral()

    from src.config import PERMIT_API
    log.info("API guard activated: PERMIT_API=%s", PERMIT_API)


def _patch_anthropic():
    """Patch anthropic.resources.messages.Messages.create"""
    try:
        from anthropic.resources.messages import Messages
    except ImportError:
        return

    _original_create = Messages.create

    def _guarded_create(self, *args, **kwargs):
        from src.config import PERMIT_API
        if not PERMIT_API:
            model = kwargs.get("model", "unknown")
            raise APICallBlocked(
                f"Anthropic API call blocked (model={model}). "
                f"Set MANTRI_PERMIT_API=true for live runs."
            )
        return _original_create(self, *args, **kwargs)

    Messages.create = _guarded_create
    log.debug("Patched anthropic.Messages.create")


def _patch_genai():
    """Patch google.genai.models.Models.generate_content"""
    try:
        from google.genai.models import Models
    except ImportError:
        return

    _original_generate = Models.generate_content

    def _guarded_generate(self, *args, **kwargs):
        from src.config import PERMIT_API
        if not PERMIT_API:
            model = kwargs.get("model", args[0] if args else "unknown")
            raise APICallBlocked(
                f"Gemini API call blocked (model={model}). "
                f"Set MANTRI_PERMIT_API=true for live runs."
            )
        return _original_generate(self, *args, **kwargs)

    Models.generate_content = _guarded_generate
    log.debug("Patched genai.Models.generate_content")


def _patch_mistral():
    """Patch mistralai.Mistral.chat.complete"""
    try:
        from mistralai import Mistral
        # Mistral SDK structure: client.chat is a ChatCompletions object
        from mistralai.chat import Chat
    except ImportError:
        return

    _original_complete = Chat.complete

    def _guarded_complete(self, *args, **kwargs):
        from src.config import PERMIT_API
        if not PERMIT_API:
            model = kwargs.get("model", "unknown")
            raise APICallBlocked(
                f"Mistral API call blocked (model={model}). "
                f"Set MANTRI_PERMIT_API=true for live runs."
            )
        return _original_complete(self, *args, **kwargs)

    Chat.complete = _guarded_complete
    log.debug("Patched mistralai.Chat.complete")
