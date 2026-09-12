# Assistant image creation

The separate `POST /assistant/image` action generates one JPEG using
`gpt-image-1.5`, explicit medium quality and 1024x1024 size. The UI shows 200
credits before submission, in all 13 languages. No image action is offered to
guests. Both the assistant schema capability/runtime switch and the independent
`ASSISTANT_IMAGES_ENABLED=true` switch are required. Real release-account access
was verified on September 9, 2026: one valid 46,344-byte JPEG, 15 input tokens
and 1,303 output tokens (1,056 image / 247 text). The API image switch is now
enabled and the live catalog displays its 200-credit price. This provider check
does not claim a real customer's wallet was charged.

The 1,000 UTF-8-byte prompt limit, fixed format/quality and one image per request
bound normal cost. OpenAI's reviewed September 12 pricing is $5/M text input,
$10/M text output and $32/M image output tokens; medium square image output is
approximately $0.034, plus prompt input. The response's aggregate `output_tokens`
must therefore not be priced entirely at the image-token rate. Source:
[model pricing](https://developers.openai.com/api/docs/models/gpt-image-1.5) and
[Image API](https://developers.openai.com/api/reference/resources/images).
At the USD 10,000-credit pack price, 200 credits represent about $0.60 of pack
revenue. This is gross revenue, not profit after tax, payment, hosting, included
credits and refunds. Regional prices are deliberate offers, not FX quotes.

The shared wallet reserves 200 credits before any provider request. Actual image
usage is recorded separately by modality. Output details are accepted only when
non-negative integer image/text counts add exactly to the aggregate output count.
The verified 15 text input / 1,056 image output / 247 text output example costs
36,337 micro-USD, or 182 platform budget credits after rounding up. The customer
charge remains exactly 200 credits.

If the optional output details are missing or inconsistent, the cost ledger uses
the published $0.034 medium-square image estimate plus measured text input. The
cost ledger marks the estimate as a published fallback, while the wallet
conservatively keeps the full 200-credit daily reservation. It neither refunds unverified budget nor sets the
daily budget to its closed ceiling. Measured detailed cost beyond the reservation
still closes that day's budget. Unknown failures also retain the platform budget
reservation while refunding the customer. One request per account may be active;
no SDK retries run. A retry of the same request ID replays its cached result
without another charge or provider call. The 85-second timeout is shorter than
the wallet's two-minute abandoned-request boundary.

Results are validated JPEGs bounded to 1.5 MB of base64. No external provider URL
is fetched or accepted. The assistant displays the image and an explicit download
link. Image bytes are kept only in the existing private 15-minute answer cache
for delivery/retry, not a permanent gallery. The API maintenance loop clears this
cache; account erasure deletes it even after chat is disabled. Account export
contains usage records without the cached image bytes. Ordinary encrypted backup
retention still applies; cache expiry is not immediate deletion from old backups.
No prompt, base64 or provider error body is written to operating-cost logs.

Real `gpt-image-1.5` access and measured image usage were verified before
activation. OpenAI now lists `gpt-image-2` as the current image-generation model,
and the Image API reference lists it on the same generation endpoint with the
request fields used here. The application does not switch models in this change:
release-account access, the returned usage split and output behavior have not been
verified without a paid request. Current [GPT-Image-2 pricing](https://developers.openai.com/api/docs/pricing)
lists $2.50/M text input and $15/M image output tokens, with no text-output rate
or published medium-square fallback price. A later migration must add those
model-specific rules and verify the release-account response before activation.
Synthetic CI checks verify detailed and fallback accounting, retry delivery,
cache cleanup and refund behavior without making paid image requests. Video
generation needs a different durable provider after the announced Sora API
shutdown and remains unavailable.
