# LectureSift search and advertising readiness

## Verified account state (29 August 2026)

- The `lecturesift.com` domain property is verified in Google Search Console.
- `https://lecturesift.com/sitemap.xml` is successful and Google reports 130 discovered pages. Performance and indexing data are still being processed.
- The AdSense site is connected, `ads.txt` is authorised, and Auto ads are enabled for consented visitors on selected public pages.
- One European-regulations consent message is active in AdSense Privacy & Messaging.
- The current legal payment address has been submitted in Google Payments and is under review. Do not submit the older verified address to AdSense while this review is pending.
- No Google Ads account exists under the connected Google account yet, so campaign and conversion identifiers are not available. No ad spend is activated by this repository.

## Already implemented in the application

- One canonical URL and 13 language alternates for every public page, including the distance-sales contract.
- A 130-URL sitemap and robots rules that keep account, payment-result, verification, and admin pages out of search.
- Open Graph, Twitter cards, Organization, WebSite, WebPage, SoftwareApplication, breadcrumb, and eligible FAQ structured data.
- Search previews are blocked from indexing; the production domain is indexable.
- Analytics and advertising code is consent-gated.
- Google Analytics 4 loads only after analytics consent, limits automatic page views to public pages, and disables advertising signals. Token-bearing verification and password-reset pages are excluded.
- Google Ads signup and verified-purchase conversion events are prepared separately from analytics and run only after advertising consent. Duplicate purchase conversions are suppressed per browser session.
- AdSense Auto ads are enabled only after advertising consent, limited to selected public pages, and hidden for ad-free paid plans. Full-screen vignette ads are disabled in the AdSense site settings.
- Rewarded ads are voluntary, rate-limited, and disabled until a real provider unit is configured.

## External setup still required

1. Monitor Search Console indexing, Core Web Vitals, manual actions, and security issues while the newly submitted data is processed.
2. Keep the GA4 measurement ID configured in Render and verify the first consented page view in Realtime after deployment.
3. Wait for the current Google Payments address review, then submit that reviewed profile in AdSense and wait for the site review to move from preparing to ready.
4. Maintain the active European-regulations consent message and test it before enabling ad inventory.
5. Create a Google Ads account plus signup and purchase conversion actions; place the public `AW-...` ID and both conversion labels in the matching Render variables. Verify them with Tag Assistant before spending.
6. Create Google Ad Manager/AdSense inventory, obtain the real banner and rewarded unit paths, and configure them in Render. Never publish placeholder unit paths.
7. Create Google Ads campaigns only after a budget, target countries, conversion definitions, and landing pages are approved. Advertising spend is never activated by a code deployment.
8. Publish useful course-specific landing pages and original guides, earn reputable links, and review search performance monthly. No implementation can guarantee a first-place Google ranking.

## Release checks

- Validate structured data with Google Rich Results Test.
- Confirm each localized page has the intended title, description, canonical, and language alternate.
- Confirm advertising requests are absent before advertising consent and on every paid account.
- Confirm an empty ad response leaves no blank banner.
- Confirm legal/operator identity and privacy disclosures are complete before payments or ads are enabled.
