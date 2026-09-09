"""One priced, authenticated image request; no provider URLs or prompt storage."""
import base64
import hashlib
import io

from openai import OpenAI
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from . import assistant_catalog as catalog, assistant_wallet as wallet, config
from .costs import record_cost
from .errors import LectureSiftError

PREFIX = 'Create one clear educational illustration following this description. Preserve the requested language for any labels.\n'


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{16,64}$')
    prompt: str = Field(min_length=1, max_length=1000)


def generate(user_id, payload: ImageRequest):
    if not catalog.images_enabled() or not config.OPENAI_API_KEY:
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
        if (not isinstance(usage.input_tokens, int) or usage.input_tokens < 0
                or not isinstance(usage.output_tokens, int) or usage.output_tokens < 1):
            raise ValueError('Invalid usage')
        encoded = response.data[0].b64_json
        if not isinstance(encoded, str) or len(encoded) > 1_500_000:
            raise ValueError('Image exceeds response budget')
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.format != 'JPEG' or picture.size != (1024, 1024):
                raise ValueError('Unexpected output format')
            picture.verify()
        # Text-only generation: $5/M input, $32/M image output, reviewed 2026-09-08.
        # One platform budget credit represents $0.0002 of provider cost. The
        # customer price is fixed and distinct from this metered cost estimate.
        cost_units = (usage.input_tokens * 5 + usage.output_tokens * 32 + 199) // 200
        for direction, amount, rate in [('input', usage.input_tokens, 5), ('output', usage.output_tokens, 32)]:
            record_cost(provider='openai', service='site_assistant_image', resource=catalog.IMAGE_MODEL,
                        quantity=amount, unit='token', price_usd=rate, price_basis=1_000_000,
                        pricing_source='https://developers.openai.com/api/docs/models/gpt-image-1.5',
                        pricing_effective_at='2026-09-08', metadata={'direction': direction}, user_id=user_id)
        result = {'kind': 'image', 'image': 'data:image/jpeg;base64,' + encoded,
                  'width': 1024, 'height': 1024, 'action': 'none'}
    except Exception:
        wallet.settle(user_id, key, unknown_cost=True)
        raise LectureSiftError('LS-ASSIST-07', 'Görsel tamamlanamadı; kredi düşülmedi.', status_code=503) from None
    result = wallet.settle(user_id, key, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                           response=result, fixed_charge=catalog.IMAGE_CREDITS, provider_cost_credits=cost_units)
    result['balance'] = wallet.status(user_id)['balance']
    return result
