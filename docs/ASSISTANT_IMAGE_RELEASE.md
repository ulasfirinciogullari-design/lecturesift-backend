# Assistant image creation

The separate `POST /assistant/image` action generates one JPEG using
`gpt-image-1.5`, explicit medium quality and 1024x1024 size. The UI shows 220
credits before submission, in all 13 languages. No image action is offered to
guests. Both the assistant schema capability/runtime switch and the independent
`ASSISTANT_IMAGES_ENABLED=true` switch are required. Production provider access
is not yet verified.

The 1,000 UTF-8-byte prompt limit, fixed format/quality and one image per request
bound normal cost. OpenAI's reviewed September 8 pricing is $5/M input tokens and
$32/M image output tokens; medium square image output is approximately $0.034,
plus prompt input. Source: [model pricing](https://developers.openai.com/api/docs/models/gpt-image-1.5)
and [Image API](https://developers.openai.com/api/reference/python/resources/images/methods/generate).
At the USD 10,000-credit pack price, 220 credits represent about $0.66 of pack
revenue. This is gross revenue, not profit after tax, payment, hosting, included
credits and refunds. Regional prices are deliberate offers, not FX quotes.

The shared wallet reserves 220 credits before any provider request. Actual image
usage is recorded separately at the image rates; the shared daily spend ceiling
uses those costs, not chat token weights. Drift beyond the reservation closes
that day's budget. Unknown failures retain their conservative platform budget
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

Before live activation, verify model access/organization verification in the real
release account, one real image, measured usage, retry delivery, cache cleanup and
refund behavior. Existing synthetic CI tests verify the contract without making
paid image requests. Video generation needs a different durable provider after
the announced Sora API shutdown and remains unavailable.
