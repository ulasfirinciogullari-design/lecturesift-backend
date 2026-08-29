# LectureSift cost monitoring

The admin **Costs** tab separates app-observed variable usage from configured
monthly expenses. It never stores API keys, prompts, uploaded document text,
card data, email addresses, IP addresses, or other personal data.

## Continuously metered

- OpenAI token usage returned by the API, attributed to the job and account.
- A duration-based transcription estimate when the API omits token usage.
- Cloudflare R2 application-observed read and write operations.
- Per-job totals in USD and an indicative TRY conversion using the TCMB daily
  USD selling rate, with a configurable fallback when TCMB is unavailable.

These are gross list-price estimates. Free tiers, taxes, discounts, storage
GB-month, and provider-specific contracts can make the invoice differ.

## Monthly configuration

Set these on the Render web service without committing private account data:

- `LECTURESIFT_COST_RENDER_MONTHLY_USD`: total of the current Render web,
  worker, database, Key Value, and cron plans.
- `LECTURESIFT_COST_NETLIFY_MONTHLY_USD`: current Netlify plan; the blueprint
  defaults to `20` for the known Pro plan.
- `LECTURESIFT_COST_RESEND_MONTHLY_USD`: `0` while within the free plan, or the
  current monthly email plan.
- `LECTURESIFT_COST_OTHER_MONTHLY_USD`: domains and any other fixed monthly
  service that should be included.
- `LECTURESIFT_COST_USD_TRY_FALLBACK`: fallback conversion rate only; live TCMB
  data is preferred.

## Invoice-only items

iyzico/PayTR commissions, refunds and payment fees depend on the merchant
agreement. Google Ads spend depends on the advertising account. Those amounts
must be reconciled from the provider dashboards and invoices; the admin panel
labels them separately instead of presenting a fabricated estimate.

## Pricing sources

- OpenAI model pages: <https://developers.openai.com/api/docs/models>
- Cloudflare R2: <https://developers.cloudflare.com/r2/pricing/>
- Render: <https://render.com/pricing>
- Netlify: <https://www.netlify.com/pricing/>
- Resend: <https://resend.com/pricing>

Update the rate catalog and its effective date whenever a provider changes its
public price. Provider invoices and payment reconciliations remain the final
accounting source.
