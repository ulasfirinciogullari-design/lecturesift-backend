# Google Ads read-only API connection

This optional connection lets the private administration API read the target
Google Ads account's status, reporting totals and applied-incentive summary. It
does not create, edit, enable, pause or remove campaigns, change budgets, fetch
offers, apply promotions or authorize advertising spend. Browser conversion
tags remain controlled by the separate analytics settings.

The client is intentionally limited to:

- `https://oauth2.googleapis.com/token` for a service-account JWT exchange or
  an existing user OAuth refresh grant;
- the exact OAuth scope `https://www.googleapis.com/auth/adwords`;
- `POST https://googleads.googleapis.com/v25/customers/{customer}/googleAds:search`;
- fixed source-controlled GAQL `SELECT` queries.

There is no configurable provider URL, manager-account header or arbitrary
query input. The application sends no `developer-token` header. Google
[sunset developer tokens on 9 September 2026](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)
and now assigns API access to the Cloud project that owns the service account
or OAuth client.
Google's v25 documentation recommends omitting the retired header.

Google documents both the
[service-account access workflow](https://developers.google.com/google-ads/api/docs/oauth/service-accounts)
and the
[server-to-server JWT exchange](https://developers.google.com/identity/protocols/oauth2/service-account).
The service account must be added directly to the target Ads account with the
**Read-only** access level. This integration deliberately does not support a
manager-account impersonation path.

## Choose one authentication mode

Configure either service-account JSON or all three user OAuth values: client
ID, client secret and refresh token. Leave the other mode empty. Partial or
mixed credentials are rejected before any network request. The existing
service-account path remains supported; user OAuth is optional.

Both modes require Google Ads API to be enabled in the credentials' Cloud
project with production API access. A published OAuth consent screen is a
separate setting and does not grant this access. On the project's
[Google Ads API Overview page](https://console.cloud.google.com/google/ads-apis/overview),
check for **Explorer**, **Basic** or **Standard** access. **Test** only permits
test accounts. If the project has Test access, apply for Explorer; Google may
approve it automatically, but approval is not guaranteed. See Google's
[project setup](https://developers.google.com/google-ads/api/docs/oauth/cloud-project)
and [access-level requirements](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).
Application code or an OAuth refresh request cannot upgrade the project's
access level.

## Service-account connection

1. Create or select a dedicated Google Cloud project, enable Google Ads API and
   complete the required API-access registration for the production Ads
   account. Create a service account and one JSON key.
2. In Google Ads, open **Admin → Access and security**, invite the
   `client_email` from that JSON key, and select **Read-only**. Do not send the
   key file or its contents through chat, email, an issue or a commit.
3. Copy the target account's ten-digit customer ID without hyphens. The client
   sends every query to that exact account and verifies the returned customer
   ID before presenting results.
4. Store the complete service-account document in the API service's secret
   environment as either minified JSON or standard base64. Base64 only makes a
   multiline value easier to transport; it is not encryption. For example, on
   a trusted PowerShell session:

   ```powershell
   $bytes = [IO.File]::ReadAllBytes("$HOME\Downloads\service-account.json")
   [Convert]::ToBase64String($bytes)
   ```

5. Stage these values while the feature remains disabled:

   ```dotenv
   LECTURESIFT_GOOGLE_ADS_API_ENABLED=false
   LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON=<private base64 or minified JSON>
   LECTURESIFT_GOOGLE_ADS_API_CLIENT_ID=
   LECTURESIFT_GOOGLE_ADS_API_CLIENT_SECRET=
   LECTURESIFT_GOOGLE_ADS_API_REFRESH_TOKEN=
   LECTURESIFT_GOOGLE_ADS_API_CUSTOMER_ID=<10 digits, no hyphens>
   LECTURESIFT_GOOGLE_ADS_API_CACHE_SECONDS=300
   LECTURESIFT_GOOGLE_ADS_API_TIMEOUT_SECONDS=10
   ```

6. Deploy the credential-only configuration, change only
   `LECTURESIFT_GOOGLE_ADS_API_ENABLED` to `true`, and open the authenticated
   administration advertising-readiness view. Enabling this status reader does
   not enable campaigns, conversion tags, AdSense or site ads.

The JSON validator requires the normal Google `service_account` type, the fixed
Google token URI, matching project/client email values, a numeric client ID and
an unencrypted 2048–4096-bit RSA private key. Invalid material fails before any
Google request. The private key, signed assertion and access token are never
logged or returned. The secret is admitted only to the API role; worker,
Instagram and isolated media-probe environments exclude it.

## Existing-client user OAuth connection

Google's [single-user authentication workflow](https://developers.google.com/google-ads/api/docs/oauth/single-user-authentication)
supports a Desktop OAuth client regardless of the type of application that
will use the resulting refresh token. An existing Desktop client, including
the client used to set up AdSense, can be reused without creating another
client or service-account key. Its Cloud project must meet the production
access prerequisites above.

1. Request a **new**, offline authorization for exactly
   `https://www.googleapis.com/auth/adwords`, using a Google user with direct
   access to the target Ads account. The user completes Google's consent and
   any required passkey challenge. Do not combine this grant with AdSense or
   Cloud management scopes. A refresh exchange does not obtain new consent or
   expand the authorized scopes.
2. Keep the existing AdSense environment and refresh token unchanged. Reusing
   the OAuth client does **not** mean reusing its `adsense.readonly` token;
   that grant cannot read Google Ads. Store the newly issued Ads token in
   separate API-role secrets, with the reader disabled:

   ```dotenv
   LECTURESIFT_GOOGLE_ADS_API_ENABLED=false
   LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON=
   LECTURESIFT_GOOGLE_ADS_API_CLIENT_ID=<existing OAuth client ID>
   LECTURESIFT_GOOGLE_ADS_API_CLIENT_SECRET=<private OAuth client secret>
   LECTURESIFT_GOOGLE_ADS_API_REFRESH_TOKEN=<new private adwords-only refresh token>
   LECTURESIFT_GOOGLE_ADS_API_CUSTOMER_ID=<10 digits, no hyphens>
   LECTURESIFT_GOOGLE_ADS_API_CACHE_SECONDS=300
   LECTURESIFT_GOOGLE_ADS_API_TIMEOUT_SECONDS=10
   ```

3. Deploy the staged configuration, enable only the status reader, and verify
   the authenticated readiness result. A configured client or a successful
   consent screen is not evidence of a successful Ads API connection.

The refresh response must explicitly confirm exactly the `adwords` scope;
missing, different or additional scopes stop the check before Ads queries.
User OAuth permissions follow the user's Google Ads role; the integration
still sends only its fixed reporting queries and does not expose mutation
endpoints. Client secrets and refresh tokens remain API-only, never appear in
readiness responses or settings representations, and are excluded from
isolated media probes. Do not paste them into chat or commit populated files.
Google's [user authentication requirements](https://developers.google.com/google-ads/api/docs/oauth/security-requirements)
describe when a passkey or two-step verification is required.

## Returned summary

The authenticated admin endpoint `/billing/admin/advertising-readiness`
returns only a reduced schema:

- account status, three-letter currency and time zone;
- cost in micros, impressions, clicks and conversions for today, the previous
  seven complete days, and the current month;
- campaign counts grouped as enabled, paused, removed and other;
- applied-incentive counts, states and monetary totals grouped by currency.

Customer IDs, account names, campaign names/IDs, resource names, coupon codes,
provider request IDs and raw error messages are omitted. Successful checks are
cached for 300 seconds by default; failed connections and unavailable
performance/campaign sections for at most 60 seconds. A cold
check uses a shared ten-second request-start deadline and a five-second HTTP
inactivity timeout, not a hard wall-clock cancellation guarantee. Concurrent
cold admin requests within one process share one provider check. The admin
route starts this reader and the independent AdSense reader in parallel.

Account verification remains required. After it succeeds, a failed period
is represented as `periods.<period>=null` with an allowlisted error code in
`period_errors.<period>`; other periods remain available. A failed or
incomplete campaign query produces `campaigns=null` and a fixed
`campaigns_error_code`. These optional failures do not disconnect the
verified account or invent zero cost, clicks, conversions or campaigns.
Raw provider exceptions and response bodies remain excluded.

Google marks its
[Incentives feature as allowlist-only](https://developers.google.com/google-ads/api/docs/billing/incentives).
LectureSift only queries the read-only `AppliedIncentive` resource. It never
calls `FetchIncentive` or `ApplyIncentive`. When Google returns the typed
`CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE` reason, the incentive section
reports `unsupported`; other incentive-only failures report `unavailable`.
Neither failure hides otherwise valid account, performance and campaign
reporting. An unsupported result does not prove that a promotion exists or
does not exist; verify the offer and its terms in the owning Google Ads panel.

## Rotation and rollback

For normal service-account rotation, keep the reader disabled, create a new service-account
key, replace the entire secret, deploy, then enable and verify one authenticated
admin check before deleting the old key in Google Cloud. If compromise is
suspected, disable the integration and delete the affected key immediately,
then create and install a fresh key after containment. Rollback is simply
`LECTURESIFT_GOOGLE_ADS_API_ENABLED=false`; campaign and ad-serving state is
unaffected.

For user OAuth rotation, disable the reader, obtain a replacement Ads-only
grant with the intended existing client, update the separate Ads credentials,
then enable and verify the reader. Credential changes invalidate the process
cache. Do not replace AdSense's grant or leave service-account JSON populated
when switching to user OAuth. Revoking an OAuth application's access can affect
its other grants; treat that as separate from replacing the Ads token stored
in this application.
