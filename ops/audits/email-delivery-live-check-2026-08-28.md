# LectureSift live email delivery check

Checked at: 2026-08-28 00:49 Europe/Istanbul (2026-08-27 21:49 UTC)

## Public DNS

- `send.mail.lecturesift.com` MX record resolves to the Resend/Amazon SES feedback endpoint.
- SPF is published for `send.mail.lecturesift.com`.
- DKIM is published at `resend._domainkey.mail.lecturesift.com`.
- `lecturesift.com` resolves to Netlify's apex address.
- `www.lecturesift.com` is a CNAME to `clever-horse-22b1a8.netlify.app`.

## Live backend

- `GET /health`: HTTP 200, backend version `4.1`.
- `GET /billing/health`: HTTP 200.
- Billing database: connected, persistent, PostgreSQL.
- `email_delivery_configured`: `true`.

## Controlled delivery

A unique `@resend.dev` delivery-test recipient was registered through the production endpoint.

- `POST /billing/register`: HTTP 200 on the first attempt.
- Response returned `ok: true` and `verification_required: true`.
- The verification token and six-digit code were not exposed in the API response.
- The production backend reported that the verification message was accepted by Resend.

Result: **PASS — LectureSift's production registration and email-verification delivery flow is active.**
