# LectureSift search and advertising readiness

## Current check (12 September 2026)

- Merge `07d560ea` (PR #88) was verified on GitHub `main`, both Render services and Netlify production. This records that release only; a later source change, passing CI or healthy endpoint is not evidence that its corresponding revision has reached every production service.

- The current task cannot read the owner's signed-in AdSense tab. Its URL is visible in ambient UI, but the available tools include no browser read/control capability. The previous panel observations below are historical, not today's approval status. Re-enabling Browser has not exposed the missing tools; do not repeatedly request the same action or copy browser cookies.
- Live GA4 measurement is enabled with `G-4L2CBDSZ48`, while advertising signals remain disabled. Google Ads conversion ID and signup/purchase labels are absent, so the application cannot currently report those conversions. Event arrival in Google Analytics has not been verified in Realtime.
- AdSense/display-ad activation flags are off in production, `/ads/config` reports no active provider or Auto ads, and the production HTML does not load the AdSense script. The public [`ads.txt`](https://lecturesift.com/ads.txt) is reachable and contains exactly `google.com, pub-7608481350058806, DIRECT, f08c47fec0942fa0`; this authorizes the seller record but does not prove site approval, CMP readiness or ad serving.
- The application now distinguishes new Lite public-page advertising, new Plus home-page-only advertising and ad-free entitlements. Earlier paid-plan snapshots keep their original advertising terms; Pro, Max, legacy ad-free plans and permanent ad-free purchases/overrides suppress publisher inventory. The workspace and private pages remain outside publisher placements.
- The cookie, privacy, terms and admin-readiness copy uses that same plan model in all 13 languages; it no longer describes every paid plan as ad-free.
- Measurement recognizes clean and legacy public URLs in all 13 languages, including the three course guides. Account and registration conversion events do not include their query or fragment in the configured page URL. Consent and provider configuration are still required. Synthetic CI checks do not prove events arrived in Google.
- The host policy includes the documented core Google Ads script, image, connection and frame endpoints and the Turkish regional beacon. Before a campaign launch, check the actual target-country beacon domains in Tag Assistant; Google country domains must be named individually in CSP. No campaign is launched by this change. [Google tag CSP guidance](https://developers.google.com/tag-platform/security/guides/csp)
- **AdSense activation has a separate CSP prerequisite.** Deploy previews stage a fresh per-response nonce in report-only mode for the 52 public ad-eligible localized routes. Production applies the same report-only transformation only to `/about`; it does not replace the enforced static CSP or enable ads. On the verified base release, two production `/about` responses had distinct nonces and each response's HTML/header nonce matched, while `/`, `/en/about` and `/account` had no report-only/debug nonce header. The protected preview and actual Google integration still have not been inspected through an authenticated browser, and violation-log evidence remains required before expansion. Do not enable AdSense merely by extending the host list, using a fixed nonce, or treating a successful build as an integration check. [AdSense CSP integration](https://support.google.com/adsense/answer/16283098?hl=en-GB), [Netlify nonce plugin](https://github.com/netlify/plugin-csp-nonce)
- The custom site privacy dialog is not evidence of a Google-certified consent platform. Before setting `LECTURESIFT_ADSENSE_CMP_READY=true`, verify the live Google-certified CMP message and its current IAB TCF behavior for the EEA, United Kingdom and Switzerland. Site approval, CMP publication and consent-string/event behavior are separate checks.
- Google Publisher Tag does not provide signed server-side verification for web rewarded ads; Google documents SSV for mobile apps only. The web flow therefore uses an explicitly named `client_event_limited` mode: the server requires ordered `presented → granted → redeemed` transitions, but a modified client can still imitate them. Activation additionally requires an operator risk-acceptance flag, a positive global daily reward budget, a verified account, one open session, expiry, cooldown and daily user attempt/reward caps. The defaults require a 24-hour-old account, use a two-minute session and apply a five-minute cooldown; operators can tune those three values. These controls bound exposure; they do not prove that a person watched an ad. Keep the feature disabled when per-view cryptographic proof is required. [Google rewarded ads for web](https://support.google.com/admanager/answer/9116812?hl=en), [Google Publisher Tag events](https://developers.google.com/publisher-tag/reference)
- The verified base release sends `Referrer-Policy: strict-origin-when-cross-origin`, which Google lists as supported for publisher messages; cross-origin requests receive only the origin rather than the page path or query. This header is necessary but does not itself prove that a CMP message works. [Google publisher-message tagging](https://support.google.com/admanager/answer/10114216?hl=en)

### Read-only account connection alternative

AdSense Management API v2 can read site approval state, Auto ads state, alerts, policy issues and reporting with `https://www.googleapis.com/auth/adsense.readonly`. It needs a Google OAuth client and consent from the account that owns `lecturesift.com`; a public publisher ID is not authentication. AdSense does not support service accounts. No OAuth client/refresh credentials were found in the inspected application configuration, and no connection was created. Use the installed-application flow and private credential storage, never a password, browser-cookie copy or secret in chat. [AdSense authorization](https://developers.google.com/adsense/management/direct_requests), [site state reference](https://developers.google.com/adsense/management/reference/rest/v2/accounts.sites)

Google Ads API separately requires OAuth and an approved developer token from the manager account's API Center. Read-only account access would allow checking existing campaigns and reporting; it does not grant authorization to start spending. [Google Ads authentication](https://developers.google.com/google-ads/api/rest/auth)

### Connected-mailbox evidence (12 September 2026)

- The available Gmail connection is for `ulasweb3@gmail.com`, not the owner-mentioned `ulasfirinciogullari@gmail.com`. These findings therefore cover only the connected mailbox and do not establish what the other mailbox received.
- Google Ads mail says the existing account is paused until advertiser-verification tasks are completed. It also records rejection of two “Youtube Promotion” campaigns under the Financial Services Verification policy. No campaign should be relaunched until the account owner completes Google's requested identity/business steps and resolves the policy classification in Google Ads.
- Google states that a paused account can be unpaused only after advertiser verification. Its Türkiye policy also provides a verification route for non-financial advertisers whose campaigns reach audiences associated with financial-service searches; LectureSift should describe itself there as an education software service and must not claim a financial licence. [Advertiser-verification timing](https://support.google.com/adspolicy/answer/15588490?hl=en), [Türkiye financial-services verification](https://support.google.com/adspolicy/answer/15332527?co=GENIE.CountryCode%3DTR&hl=en)
- LectureSift's Turkish use of “kredi” for application usage credits may have contributed to an automated financial-services classification. This is an inference from the site wording and rejection category, not a reason supplied by Google.
- No AdSense message or message confirming an `8,000 TL` promotional balance was found in the connected mailbox. The promotion remains unverified; advertising spend and AdSense publisher income are separate products.

## Historical panel check (8 September 2026; not reverified since)

- AdSense site details for `lecturesift.com` still show **Preparing** ("Hazırlanıyor"), with site ownership verified and the review request received. This is not an approval to serve ads or evidence of revenue.
- The sites table still reports `ads.txt` not found with a **28 August** crawl date. That is a stale provider observation: the current public file was rechecked on 12 September and returns the exact publisher line recorded above. Do not overwrite the publisher ID or resubmit identity details merely to clear this label.
- In this release, both `LECTURESIFT_ADSENSE_ENABLED` and `LECTURESIFT_ADSENSE_CMP_READY` must be true, alongside consent, eligible public-page placement and an ad-eligible account. Keep inventory disabled until approval and a current consent-flow check are confirmed.
- Rewarded-minute inventory remains disabled. Web GPT has no provider-signed per-view completion callback, and this release keeps both the explicit client-event risk acceptance and global reward budget closed. Referral rewards are a separate application feature.
- No new campaign, budget, payment-profile submission, ad unit or provider setting was created during this check.

## Previous account observations (7 September 2026; not reverified today)

- The `lecturesift.com` domain property is verified in Google Search Console.
- `https://lecturesift.com/sitemap.xml` is successful. The current release contains 169 indexable language/page combinations; Search Console may take time to refresh its discovered-page total.
- The latest Indexing report snapshot shows 171 indexed and 132 non-indexed URLs. Most exclusions are intentional `noindex`, alternate-language canonical, or redirect URLs; the duplicate-URL cluster is being consolidated by the clean-URL migration below.
- The prior report recorded AdSense connectivity, `ads.txt` authorisation and Auto ads configuration. The current Preparing state above supersedes any inference that ads are already approved or serving.
- One European-regulations consent message is active in AdSense Privacy & Messaging.
- The current legal payment address has been submitted in Google Payments and is under review. Do not submit the older verified address to AdSense while this review is pending.
- A later connected-mailbox check confirms an existing Google Ads account, currently paused for advertiser verification, and two rejected campaigns. Google Ads conversion identifiers are still absent from the application, and no ad spend is activated by this repository.
- The Google Ad Manager signup currently reports `uncheckedAdsenseAccount`. Banner and rewarded inventory must remain disabled until Google finishes the AdSense account/application review.
- The live worker, private object storage, managed database backup, object-retention confirmation, and restore drill all report ready.

## Already implemented in the application

- One extensionless canonical URL and 13 reciprocal language alternates for every public page, including the distance-sales contract.
- Legacy `.html` aliases permanently redirect to their clean canonical URL instead of serving a second indexable `200` response.
- A 169-URL sitemap and robots rules that keep account, payment-result, verification, and admin pages out of search.
- Non-indexable pages remain crawlable long enough for search engines to read their explicit `noindex`; `robots.txt` is not used as an access-control mechanism.
- Search-visible content is prerendered in all 13 languages instead of depending on client-side translation.
- Open Graph, Twitter cards, Organization, WebSite, WebPage, SoftwareApplication, Article, breadcrumb, and eligible FAQ structured data.
- The production build fails automatically when a generated language page is missing its title, description, canonical URL, H1, language alternates, or valid JSON-LD.
- Search previews are blocked from indexing; the production domain is indexable.
- Analytics and advertising code is consent-gated.
- Google Analytics 4 loads only after analytics consent, limits automatic page views to public pages, and disables advertising signals. Token-bearing verification and password-reset pages are excluded.
- Google Ads signup and verified-purchase conversion events are prepared separately from analytics and run only after advertising consent. Duplicate purchase conversions are suppressed per browser session.
- AdSense loading is conditional on explicit configuration and advertising consent, limited to selected public pages, and hidden for ad-free entitlements. Prior vignette settings have not been reverified in this check.
- Rewarded ads are voluntary and default off. The prepared web flow requires a real provider unit, certified-CMP readiness, explicit acceptance of client-event assurance, a positive global reward budget, verified and aged accounts, ordered events, single-open-session enforcement, expiry, cooldown and daily caps.

## Deployment configuration is staged, not activated

The Render blueprint now declares every application setting needed to configure
AdSense, GA4 and Google Ads conversions without another source-code change. The
two AdSense attestations and all measurement IDs/labels are declared as
operator-supplied values. Their application defaults remain `false` or empty.
Production currently leaves both AdSense attestations off, so it does not load
Google publisher ads, and its Google Ads ID/labels are absent, so it does not
report those conversions. The blueprint's GA4 default remains off, but the live
operator configuration overrides it: GA4 is enabled with `G-4L2CBDSZ48` and
advertising signals disabled.

- AdSense Auto ads requires a valid `LECTURESIFT_ADSENSE_PUBLISHER_ID`, plus
  both `LECTURESIFT_ADSENSE_ENABLED=true` and
  `LECTURESIFT_ADSENSE_CMP_READY=true`. The first Boolean records a current
  provider-side site approval check. The second records a live certified-CMP
  and TCF integration check. Neither value is inferred from the public
  publisher ID, `ads.txt`, an old panel observation or the site's own consent
  dialog.
- Keep `LECTURESIFT_DISPLAY_ADS_ENABLED=false` when using AdSense Auto ads. A
  valid enabled GPT banner path takes precedence over Auto ads and is a separate
  inventory mode. This change requires
  `LECTURESIFT_ADSENSE_CMP_READY=true` for either Google Publisher Tag mode and
  for rewarded GPT inventory; the attestation no longer protects only AdSense.
- Rewarded GPT remains unavailable unless
  `LECTURESIFT_REWARDED_AD_CLIENT_EVENT_RISK_ACCEPTED=true` and
  `LECTURESIFT_REWARDED_AD_GLOBAL_DAILY_LIMIT_MINUTES` is at least one reward.
  Defaults are `false` and `0`. This is a deliberate operator acknowledgement
  of Google's web limitation, not a provider attestation. Active sessions
  reserve budget before the ad is shown, and issuance/redemption serialize the
  global budget with one PostgreSQL transaction lock order. The browser accepts
  Google's documented close-before-grant ordering for only a bounded grace
  period, and a lost successful claim response can be retried idempotently.
  A global attempt ceiling derived from that minute budget also bounds daily
  ledger growth; status reads stay read-only and ignore expired reservations.
- The production nonce canary is limited to `/about`, remains report-only and
  leaves all publisher-ad enable flags false. After deployment, verify that two
  responses carry different nonces, each HTML response and its report-only
  policy use the same nonce, the existing enforced CSP remains present, and the
  Netlify violation endpoint receives no unexplained application-script errors.
  Do not broaden the route list or switch to enforcement from source checks
  alone.
- GA4 requires `LECTURESIFT_ANALYTICS_ENABLED=true` and a valid
  `LECTURESIFT_GA_MEASUREMENT_ID`. The browser still waits for analytics
  consent, and advertising signals remain disabled.
- Google Ads conversion delivery requires a valid `LECTURESIFT_GOOGLE_ADS_ID`
  and the matching signup or purchase label. The browser still waits for
  advertising consent. Configuring an identifier does not create a campaign,
  authorize spend or prove that Google received an event.

Before changing either AdSense attestation to `true`, record the current site
status from the owning AdSense account, verify the published certified CMP and
TCF flow in the target regions, and complete the nonce-based CSP integration
check on a protected preview and the production `/about` report-only canary.
After any activation, verify the public config,
eligible and ineligible routes, ad-free entitlements, consent grant/revocation,
and actual provider requests. Roll back by restoring the relevant enable flag
to `false`; identifiers may remain staged privately.

## External setup still required

1. Monitor Search Console indexing, Core Web Vitals, manual actions, and security issues while the newly submitted data is processed.
2. Keep the live GA4 measurement ID configured in Render and verify a consented page view in Realtime; live configuration proves the tag is enabled, not that Google received an event.
3. Wait for the existing site review to move from preparing to ready. Check the payment profile for any explicit outstanding action; do not repeat the submission already completed by the user without a new provider request.
4. Reverify the European-regulations consent message and test it before enabling ad inventory; do not rely only on the prior report.
5. Complete the existing Google Ads account's advertiser-verification tasks and resolve the Financial Services Verification rejection before relaunching either campaign. If Google continues to require the Türkiye financial-services form, use its non-financial-advertiser route and describe the actual education-software business accurately. Then use the verified public `AW-...` ID and signup/purchase conversion labels in the matching Render variables and verify them with Tag Assistant before spending. Do not assume an unverified promotional balance is available or can be spent without an additional payment.
6. Create Google Ad Manager/AdSense inventory and obtain the real banner and rewarded unit paths. For web rewards, choose explicitly between keeping the feature off or accepting the documented `client_event_limited` model with a small global budget; Google does not offer web SSV. Use a native mobile-app integration if signed per-view verification is mandatory. Never publish placeholder unit paths.
7. Create Google Ads campaigns only after a budget, target countries, conversion definitions, and landing pages are approved. Advertising spend is never activated by a code deployment.
8. Publish useful course-specific landing pages and original guides, earn reputable links, and review search performance monthly. No implementation can guarantee a first-place Google ranking.

## Release checks

- Validate structured data with Google Rich Results Test.
- Confirm each localized page has the intended title, description, canonical, and language alternate.
- Confirm advertising requests are absent before advertising consent and for all ad-free entitlements; Plus is limited to the home page and the workspace remains ad-free.
- Confirm the production `/about` canary has a per-response nonce and report-only policy while `/`, localized `/about` routes and private pages remain outside the production edge-function scope.
- Confirm an empty ad response leaves no blank banner.
- Confirm a direct `issued → claim` request fails, ordered browser-event transitions are required, close-without-grant cannot redeem, duplicate/racing claims remain single-use, and user/global budgets stop further redemption. Record that these checks limit abuse but cannot turn a browser event into provider-signed proof.
- Confirm legal/operator identity and privacy disclosures are complete before payments or ads are enabled.
