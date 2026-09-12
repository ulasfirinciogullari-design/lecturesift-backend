# LectureSift search and advertising readiness

## Current check (12 September 2026)

- GitHub `main` and both Render services are on merge `a24c161d`, but Netlify production is still serving `83fcdcd`. Production deploy `6aa1beb80a99dc0008a30257` for `a24c161d` failed before publication even though the identical source tree produced a successful deploy preview and GitHub CI passed. The public Netlify API does not expose the build error without authenticated log access. The next main-branch release must be treated as a deployment retry and verified by commit-specific public asset hashes and the live CSP header before PR80 is called fully live.
- Netlify reported no platform incident for 9 September. That does not identify a project-level failure; inspect the authenticated deploy log if the next production attempt also fails.

- The current task cannot read the owner's signed-in AdSense tab. Its URL is visible in ambient UI, but the available tools include no browser read/control capability. The previous panel observations below are historical, not today's approval status. Re-enabling Browser has not exposed the missing tools; do not repeatedly request the same action or copy browser cookies.
- AdSense and Google Ads conversion identifiers are not active in the inspected application configuration. The claimed Google Ads promotional balance has not been verified; advertising spend and AdSense publisher income are separate products.
- The application now distinguishes new Lite public-page advertising, new Plus home-page-only advertising and ad-free entitlements. Earlier paid-plan snapshots keep their original advertising terms; Pro, Max, legacy ad-free plans and permanent ad-free purchases/overrides suppress publisher inventory. The workspace and private pages remain outside publisher placements.
- Measurement recognizes clean and legacy public URLs in all 13 languages, including the three course guides. Account and registration conversion events do not include their query or fragment in the configured page URL. Consent and provider configuration are still required. Synthetic CI checks do not prove events arrived in Google.
- The host policy includes the documented core Google Ads script, image, connection and frame endpoints and the Turkish regional beacon. Before a campaign launch, check the actual target-country beacon domains in Tag Assistant; Google country domains must be named individually in CSP. No campaign is launched by this change. [Google tag CSP guidance](https://developers.google.com/tag-platform/security/guides/csp)
- **AdSense activation has a separate CSP prerequisite.** Google supports a strict CSP with a fresh per-response nonce; the site's current static host allowlist is not that implementation. Do not enable AdSense merely by extending the host list, use a fixed nonce, or claim the current static policy is fully AdSense compatible. A response-level nonce integration and a remote consent/ad integration check are required before activation. [AdSense CSP integration](https://support.google.com/adsense/answer/16283098?hl=en-GB)
- The custom site privacy dialog is not evidence of a Google-certified European consent message. Reverify the published provider message and the real consent flow before setting `ADSENSE_CMP_READY`.

### Read-only account connection alternative

AdSense Management API v2 can read site approval state, Auto ads state, alerts, policy issues and reporting with `https://www.googleapis.com/auth/adsense.readonly`. It needs a Google OAuth client and consent from the account that owns `lecturesift.com`; a public publisher ID is not authentication. AdSense does not support service accounts. No OAuth client/refresh credentials were found in the inspected application configuration, and no connection was created. Use the installed-application flow and private credential storage, never a password, browser-cookie copy or secret in chat. [AdSense authorization](https://developers.google.com/adsense/management/direct_requests), [site state reference](https://developers.google.com/adsense/management/reference/rest/v2/accounts.sites)

Google Ads API separately requires OAuth and an approved developer token from the manager account's API Center. Read-only account access would allow checking existing campaigns and reporting; it does not grant authorization to start spending. [Google Ads authentication](https://developers.google.com/google-ads/api/rest/auth)

## Historical panel check (8 September 2026; not reverified on 9 September)

- AdSense site details for `lecturesift.com` still show **Preparing** ("Hazırlanıyor"), with site ownership verified and the review request received. This is not an approval to serve ads or evidence of revenue.
- The sites table still reports `ads.txt` not found with a **28 August** crawl date. That is a stale provider observation; it must be distinguished from checking the current public file. Do not overwrite the publisher ID or resubmit identity details merely to clear this label.
- In the proposed release, both `ADSENSE_ENABLED` and `ADSENSE_CMP_READY` must be true, alongside consent, eligible public-page placement and an ad-eligible account. Keep inventory disabled until approval and a current consent-flow check are confirmed.
- Rewarded-minute inventory remains disabled without real provider-verified completion. Referral rewards are a separate application feature, not a substitute for ad verification.
- No new campaign, budget, payment-profile submission, ad unit or provider setting was created during this check.

## Previous account observations (7 September 2026; not reverified today)

- The `lecturesift.com` domain property is verified in Google Search Console.
- `https://lecturesift.com/sitemap.xml` is successful. The current release contains 169 indexable language/page combinations; Search Console may take time to refresh its discovered-page total.
- The latest Indexing report snapshot shows 171 indexed and 132 non-indexed URLs. Most exclusions are intentional `noindex`, alternate-language canonical, or redirect URLs; the duplicate-URL cluster is being consolidated by the clean-URL migration below.
- The prior report recorded AdSense connectivity, `ads.txt` authorisation and Auto ads configuration. The current Preparing state above supersedes any inference that ads are already approved or serving.
- One European-regulations consent message is active in AdSense Privacy & Messaging.
- The current legal payment address has been submitted in Google Payments and is under review. Do not submit the older verified address to AdSense while this review is pending.
- No Google Ads account exists under the connected Google account yet, so campaign and conversion identifiers are not available. No ad spend is activated by this repository.
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
- Rewarded ads are voluntary, rate-limited, and disabled until a real provider unit is configured.

## External setup still required

1. Monitor Search Console indexing, Core Web Vitals, manual actions, and security issues while the newly submitted data is processed.
2. Keep the GA4 measurement ID configured in Render and verify the first consented page view in Realtime after deployment.
3. Wait for the existing site review to move from preparing to ready. Check the payment profile for any explicit outstanding action; do not repeat the submission already completed by the user without a new provider request.
4. Reverify the European-regulations consent message and test it before enabling ad inventory; do not rely only on the prior report.
5. Inspect the owner's existing Google Ads account before considering any new account or conversion actions. Use the verified public `AW-...` ID and signup/purchase conversion labels in the matching Render variables. Verify them with Tag Assistant before spending; do not assume a promotional credit can be spent without an additional payment.
6. Create Google Ad Manager/AdSense inventory, obtain the real banner and rewarded unit paths, and configure them in Render. Never publish placeholder unit paths.
7. Create Google Ads campaigns only after a budget, target countries, conversion definitions, and landing pages are approved. Advertising spend is never activated by a code deployment.
8. Publish useful course-specific landing pages and original guides, earn reputable links, and review search performance monthly. No implementation can guarantee a first-place Google ranking.

## Release checks

- Validate structured data with Google Rich Results Test.
- Confirm each localized page has the intended title, description, canonical, and language alternate.
- Confirm advertising requests are absent before advertising consent and for all ad-free entitlements; Plus is limited to the home page and the workspace remains ad-free.
- Confirm an empty ad response leaves no blank banner.
- Confirm legal/operator identity and privacy disclosures are complete before payments or ads are enabled.
