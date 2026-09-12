"""One priced, authenticated image request; no provider URLs or prompt storage."""
import base64
import hashlib
import io
from collections.abc import Mapping

from openai import OpenAI
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from . import assistant_catalog as catalog, assistant_wallet as wallet, config
from .costs import record_cost
from .errors import LectureSiftError

PREFIX = 'Create one clear educational illustration following this description. Preserve the requested language for any labels.\n'
PRICED_MODEL = catalog.IMAGE_PRICED_MODEL
PRICING_SOURCE = 'https://developers.openai.com/api/docs/models/gpt-image-1.5'
PRICING_EFFECTIVE_AT = '2026-09-12'
TEXT_INPUT_USD_PER_MILLION = 5
TEXT_OUTPUT_USD_PER_MILLION = 10
IMAGE_OUTPUT_USD_PER_MILLION = 32
MEDIUM_SQUARE_USD_PER_IMAGE = 0.034
MICRO_USD_PER_PLATFORM_CREDIT = 200


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,64}$')
    prompt: str = Field(min_length=1, max_length=1000)


def _field(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _token_count(value, name, *, minimum=0):
    amount = _field(value, name)
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < minimum:
        return None
    return amount


def _output_breakdown(usage, output_tokens):
    details = _field(usage, 'output_tokens_details')
    if details is None:
        return None
    image_tokens = _token_count(details, 'image_tokens', minimum=1)
    text_tokens = _token_count(details, 'text_tokens')
    if image_tokens is None or text_tokens is None or image_tokens + text_tokens != output_tokens:
        return None
    return image_tokens, text_tokens


def _record_token_cost(user_id, amount, rate, operation):
    record_cost(
        provider='openai', service='site_assistant_image', resource=catalog.IMAGE_MODEL,
        quantity=amount, unit='token', price_usd=rate, price_basis=1_000_000,
        pricing_source=PRICING_SOURCE, pricing_effective_at=PRICING_EFFECTIVE_AT,
        metadata={'operation': operation}, user_id=user_id,
    )


def _meter_provider_cost(user_id, usage, input_tokens, output_tokens):
    """Return an exact budget charge, or None when the response lacks a safe split."""
    _record_token_cost(user_id, input_tokens, TEXT_INPUT_USD_PER_MILLION, 'text_input')
    breakdown = _output_breakdown(usage, output_tokens)
    if breakdown is None:
        # The published per-image price is the safest auditable estimate when the
        # optional output split is absent or inconsistent. The wallet keeps the
        # full 200-credit reservation in this path, so this estimate never opens
        # extra daily budget or trips the over-reservation closure guard.
        record_cost(
            provider='openai', service='site_assistant_image', resource=catalog.IMAGE_MODEL,
            quantity=1, unit='image', price_usd=MEDIUM_SQUARE_USD_PER_IMAGE,
            pricing_source=PRICING_SOURCE, pricing_effective_at=PRICING_EFFECTIVE_AT,
            estimation='published_fallback',
            metadata={'operation': 'medium_square_output', 'fallback': True}, user_id=user_id,
        )
        return None
    image_tokens, text_tokens = breakdown
    _record_token_cost(user_id, image_tokens, IMAGE_OUTPUT_USD_PER_MILLION, 'image_output')
    _record_token_cost(user_id, text_tokens, TEXT_OUTPUT_USD_PER_MILLION, 'text_output')
    weighted_micro_usd = (
        input_tokens * TEXT_INPUT_USD_PER_MILLION
        + image_tokens * IMAGE_OUTPUT_USD_PER_MILLION
        + text_tokens * TEXT_OUTPUT_USD_PER_MILLION
    )
    return (weighted_micro_usd + MICRO_USD_PER_PLATFORM_CREDIT - 1) // MICRO_USD_PER_PLATFORM_CREDIT


def generate(user_id, payload: ImageRequest):
    if (not catalog.images_enabled() or not config.OPENAI_API_KEY
            or catalog.IMAGE_MODEL != PRICED_MODEL):
        raise LectureSiftError('LS-ASSIST-01', 'Görsel üretimi şu anda kullanılamıyor.', status_code=503)
    prompt = payload.prompt.strip()
    if not prompt or len(prompt.encode()) > 1000:
        raise LectureSiftError('LS-ASSIST-06', 'Görsel açıklamasını kısalt.', status_code=422)
    fingerprint = hashlib.sha256(('image-v1:' + payload.model_dump_json()).encode()).hexdigest()
    key, replay = wallet.reserve(user_id, payload.request_id, fingerprint, catalog.IMAGE_CREDITS)
    if replay is not None:
        replay['balance'] = wallet.status(user_id)['balance']
        return replay
    try:
        with OpenAI(api_key=config.OPENAI_API_KEY, timeout=85, max_retries=0) as client:
            response = client.images.generate(
                model=catalog.IMAGE_MODEL, prompt=PREFIX + prompt, n=1, quality='medium',
                size='1024x1024', output_format='jpeg', output_compression=85, moderation='auto',
            )
        if not response.usage or len(response.data or []) != 1:
            raise ValueError('Incomplete image result')
        usage = response.usage
        input_tokens = _token_count(usage, 'input_tokens')
        output_tokens = _token_count(usage, 'output_tokens', minimum=1)
        if input_tokens is None or output_tokens is None:
            raise ValueError('Invalid usage')
        encoded = response.data[0].b64_json
        if not isinstance(encoded, str) or len(encoded) > 1_500_000:
            raise ValueError('Image exceeds response budget')
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.format != 'JPEG' or picture.size != (1024, 1024):
                raise ValueError('Unexpected output format')
            picture.verify()
        # The API's aggregate output includes differently priced image and text
        # tokens. One platform budget credit represents $0.0002 of provider cost;
        # the fixed customer price remains independent of this metered estimate.
        cost_units = _meter_provider_cost(user_id, usage, input_tokens, output_tokens)
        result = {'kind': 'image', 'image': 'data:image/jpeg;base64,' + encoded,
                  'width': 1024, 'height': 1024, 'action': 'none'}
    except Exception:
        wallet.settle(user_id, key, unknown_cost=True)
        raise LectureSiftError('LS-ASSIST-07', 'Görsel tamamlanamadı; kredi düşülmedi.', status_code=503) from None
    result = wallet.settle(
        user_id, key, input_tokens=input_tokens, output_tokens=output_tokens,
        response=result, fixed_charge=catalog.IMAGE_CREDITS,
        provider_cost_credits=cost_units, unknown_cost=cost_units is None,
    )
    result['balance'] = wallet.status(user_id)['balance']
    return result
