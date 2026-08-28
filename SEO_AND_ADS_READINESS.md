# LectureSift search and advertising readiness

## Already implemented in the application

- One canonical URL and 13 language alternates for every public page.
- A 117-URL sitemap and robots rules that keep account and admin pages out of search.
- Open Graph, Twitter cards, Organization, WebSite, WebPage, SoftwareApplication, breadcrumb, and eligible FAQ structured data.
- Search previews are blocked from indexing; the production domain is indexable.
- Analytics and advertising code is consent-gated.
- Google Analytics 4 loads only after analytics consent, is limited to public pages, and disables advertising signals. Token-bearing verification and password-reset pages are excluded.
- Banner ads are disabled by default, limited to selected public pages, and hidden for ad-free paid plans.
- Rewarded ads are voluntary, rate-limited, and disabled until a real provider unit is configured.

## External setup still required

1. Verify `lecturesift.com` in Google Search Console with the DNS TXT value issued by Google.
2. Submit `https://lecturesift.com/sitemap.xml` and monitor indexing, Core Web Vitals, manual actions, and security issues.
3. Keep the GA4 measurement ID configured in Render and verify the first consented page view in Realtime after deployment.
4. Create Google Ad Manager/AdSense inventory, obtain the real banner and rewarded unit paths, and configure them in Render. Never publish placeholder publisher IDs.
5. Add the provider-issued `ads.txt` line only after the publisher account is approved.
6. Create Google Ads campaigns only after a budget, target countries, conversion definitions, and landing pages are approved. Advertising spend is never activated by a code deployment.
7. Publish useful course-specific landing pages and original guides, earn reputable links, and review search performance monthly. No implementation can guarantee a first-place Google ranking.

## Release checks

- Validate structured data with Google Rich Results Test.
- Confirm each localized page has the intended title, description, canonical, and language alternate.
- Confirm advertising requests are absent before advertising consent and on every paid account.
- Confirm an empty ad response leaves no blank banner.
- Confirm legal/operator identity and privacy disclosures are complete before payments or ads are enabled.
