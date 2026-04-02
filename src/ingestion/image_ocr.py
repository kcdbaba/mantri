"""
Image OCR with truncation resolution.

Two-step pipeline:
1. Gemini Flash vision call extracts text and flags potential truncations
2. Fuzzy matcher resolves truncated fragments against known entities

Output: raw OCR text (unmodified) + resolution mapping as metadata.
Downstream consumers apply resolutions during entity matching, not as
text replacement. This keeps the raw text clean and avoids fragile
string surgery.

Usage:
    result = process_image("/path/to/image.jpg")
    result.raw_text    # exactly what the vision model extracted
    result.resolutions # {"Ashish Chh...": "Ashish Chhabra", ...}
    result.category    # "payment_screenshot"
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"

VISION_PROMPT = """Extract ALL text visible in this image. Return a JSON object with:

1. "text": the full extracted text, preserving layout with newlines
2. "truncations": a list of names or words that appear truncated in the image.
   A name is truncated if it:
   - Ends with "..." or "…"
   - Is visibly cut off by a UI element or screen boundary
   - Appears abbreviated compared to what the full form would be

   For each truncation, provide:
   - "original": exactly what appears in the image
   - "context": surrounding text that might help identify the full form
   - "type": "name", "business", "address", or "other"

3. "category": one of "payment_screenshot", "invoice", "product_photo",
   "delivery_photo", "document", "chat_screenshot", "other"

Return ONLY valid JSON, no markdown fences."""


@dataclass
class OCRResult:
    """Result of image OCR with truncation resolution metadata."""
    raw_text: str                    # unmodified vision output
    category: str                    # image category
    truncations: list[dict] = field(default_factory=list)   # from vision model
    resolutions: dict = field(default_factory=dict)          # {truncated: full_name}
    description: str = ""


def process_image(image_path: str,
                  known_entities: dict[str, str] | None = None,
                  cache_path: str | None = None) -> OCRResult | None:
    """
    Process an image through OCR + truncation resolution.

    Returns OCRResult with raw_text (unmodified) and resolutions mapping.
    """
    if cache_path:
        cached = _load_cache(cache_path, image_path)
        if cached:
            return cached

    vision_result = _call_vision(image_path)
    if vision_result is None:
        return None

    raw_text = vision_result.get("text", "")
    truncations = vision_result.get("truncations", [])
    category = vision_result.get("category", "other")

    if not known_entities:
        known_entities = _build_known_entities()

    resolutions = _resolve_truncations(truncations, known_entities)

    result = OCRResult(
        raw_text=raw_text,
        category=category,
        truncations=truncations,
        resolutions=resolutions,
    )

    if cache_path:
        _save_cache(cache_path, image_path, result)

    log.info("OCR: %s → %d chars, %d truncations, %d resolved",
             Path(image_path).name, len(raw_text),
             len(truncations), len(resolutions))

    return result


def _resolve_truncations(truncations: list[dict],
                          known_entities: dict[str, str]) -> dict[str, str]:
    """
    Resolve truncated names by fuzzy matching against known entities.

    Returns {truncated_form: resolved_full_name} mapping.
    Does NOT modify any text — just produces the mapping.
    """
    resolutions = {}

    for trunc in truncations:
        original = trunc.get("original", "")
        context = trunc.get("context", "")
        if not original or len(original) < 3:
            continue

        fragment = original.rstrip(".…").strip()
        if not fragment:
            continue

        # Build candidate match strings — the fragment itself,
        # plus extended versions using context
        candidates = [fragment]
        if context:
            candidates.extend(_extend_from_context(fragment, context))

        # Try each candidate against known entities
        best_match = None
        best_score = 0

        for candidate in candidates:
            match, score = _best_entity_match(candidate.lower(), known_entities)
            if match and score > best_score:
                best_match = match
                best_score = score

        if best_match:
            resolutions[original] = best_match.title()
            log.info("Truncation resolved: '%s' → '%s' (score=%.0f)",
                     original, best_match.title(), best_score)

    return resolutions


def _extend_from_context(fragment: str, context: str) -> list[str]:
    """
    Generate extended match candidates using surrounding context.

    e.g., fragment="Chh", context="From: Army Stores Prop Ashish Chh..."
    → ["Army Stores Prop Ashish Chh", "Prop Ashish Chh", "Ashish Chh"]
    """
    ctx_clean = context.rstrip(".…").strip()
    # Strip common prefixes
    for prefix in ("from:", "to:", "from", "to"):
        if ctx_clean.lower().startswith(prefix):
            ctx_clean = ctx_clean[len(prefix):].strip()

    words = ctx_clean.split()
    # Find which word contains our fragment
    frag_idx = None
    for i, w in enumerate(words):
        if w.rstrip(".…").lower().endswith(fragment.lower()) or \
           w.rstrip(".…").lower() == fragment.lower():
            frag_idx = i
            break

    if frag_idx is None:
        return []

    # Generate progressively longer prefixes
    extensions = []
    for start in range(frag_idx, -1, -1):
        extended = " ".join(words[start:frag_idx + 1]).rstrip(".…").strip()
        if len(extended) > len(fragment):
            extensions.append(extended)

    return extensions


def _best_entity_match(candidate: str,
                        known_entities: dict[str, str]) -> tuple[str | None, float]:
    """Find best matching known entity for a candidate string."""
    best_match = None
    best_score = 0

    for entity_name in known_entities:
        # Prefix match: candidate is a prefix of entity name
        if entity_name.startswith(candidate):
            score = len(candidate) / len(entity_name) * 100
            if score >= 40 and score > best_score:
                best_score = score
                best_match = entity_name

        # Word-prefix match: candidate matches a word prefix in entity name
        for word in entity_name.split():
            if word.startswith(candidate) and len(word) >= len(candidate) + 1:
                score = len(candidate) / len(word) * 100
                if score >= 50 and score > best_score:
                    best_score = score
                    best_match = entity_name

        # Fuzzy prefix match
        if len(candidate) >= 4:
            score = fuzz.ratio(candidate, entity_name[:len(candidate)])
            if score >= 85 and score > best_score:
                best_score = score
                best_match = entity_name

    return best_match, best_score


def _call_vision(image_path: str) -> dict | None:
    """Call Gemini Flash vision API."""
    try:
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            log.warning("GOOGLE_API_KEY not set")
            return None

        client = genai.Client(api_key=api_key)
        image_bytes = Path(image_path).read_bytes()
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"

        config = types.GenerateContentConfig(
            max_output_tokens=2000,
            temperature=0.0,
            response_mime_type="application/json",
        )
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                VISION_PROMPT,
            ],
            config=config,
        )
        return json.loads(response.text or "")

    except Exception as e:
        log.warning("Vision call failed for %s: %s", image_path, e)
        return None


def _build_known_entities() -> dict[str, str]:
    """Build lookup of known entity names for truncation resolution."""
    entities = {}

    from src.conversation.scrap_detector import PRINCIPAL_ENTITIES
    for p in PRINCIPAL_ENTITIES:
        entities[p] = f"principal:{p}"

    try:
        from src.router.alias_dict import get_all_aliases
        for alias, eid in get_all_aliases().items():
            entities[alias] = eid
    except Exception:
        pass

    # Full principal names
    entities["ashish chhabra"] = "principal:ashish_chhabra"
    entities["uttam enterprise"] = "principal:uttam_enterprise"
    entities["army stores prop ashish chhabra"] = "principal:army_stores"

    return entities


def _load_cache(cache_path: str, image_path: str) -> OCRResult | None:
    try:
        cache = json.loads(Path(cache_path).read_text())
        key = Path(image_path).name
        data = cache.get("images", {}).get(key)
        if data:
            return OCRResult(
                raw_text=data.get("extracted_text", data.get("raw_text", "")),
                category=data.get("category", "other"),
                description=data.get("description", ""),
                resolutions=data.get("resolutions", {}),
            )
    except Exception:
        pass
    return None


def _save_cache(cache_path: str, image_path: str, result: OCRResult):
    try:
        path = Path(cache_path)
        cache = json.loads(path.read_text()) if path.exists() else {"images": {}}
        cache["images"][Path(image_path).name] = {
            "file": Path(image_path).name,
            "extracted_text": result.raw_text,
            "category": result.category,
            "truncations": result.truncations,
            "resolutions": result.resolutions,
        }
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    except Exception as e:
        log.warning("Failed to save OCR cache: %s", e)


def process_images_batch(image_paths: list[str],
                          known_entities: dict[str, str] | None = None,
                          cache_path: str | None = None) -> dict[str, OCRResult]:
    """Process multiple images. Returns {image_path: OCRResult}."""
    if not known_entities:
        known_entities = _build_known_entities()

    results = {}
    for path in image_paths:
        result = process_image(path, known_entities=known_entities,
                               cache_path=cache_path)
        if result:
            results[path] = result

    return results
