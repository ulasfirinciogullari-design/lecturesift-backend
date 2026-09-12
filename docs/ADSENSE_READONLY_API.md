# AdSense read-only API connection

This connection lets the private administration API read the AdSense account,
site, alert and policy state. It does not turn ads on, approve a site, edit
inventory or replace the consent/CMP checks. It also cannot publish or repair
`ads.txt`, publish a certified CMP message, or start/expedite an AdSense site
review. Those remain provider and site operations. Ad serving remains
controlled by the separate `LECTURESIFT_ADSENSE_ENABLED` and
`LECTURESIFT_ADSENSE_CMP_READY` flags.

The implementation fixes the Google authorization endpoint, token endpoint and
API origin in source. The OAuth grant requests only
`https://www.googleapis.com/auth/adsense.readonly`. Google documents this scope
as read-only access to AdSense data and supports PKCE with a loopback redirect
for Desktop app OAuth clients:

- <https://developers.google.com/adsense/management/reference/rest/v2/accounts/get>
- <https://developers.google.com/identity/protocols/oauth2/native-app>

Both the initial authorization helper and every runtime token refresh require
Google to return exactly that one scope and an explicit `Bearer` token type
(the OAuth token-type value is case-insensitive).
Missing, broader or additional scopes fail closed before any AdSense API call.

## Create the private grant

1. In a dedicated Google Cloud project, enable **AdSense Management API**,
   configure its OAuth consent screen and create an OAuth client with
   application type **Desktop app**. Download its JSON configuration. Do not
   send that file or its values through chat.
2. Run the helper on the same trusted desktop where the browser will open. On
   Windows, use WSL with the files stored in the Linux home directory; the
   helper deliberately refuses files on platforms where Unix mode `0600`
   cannot be enforced. Run it inside the WSL terminal and keep both paths under
   `~`, not `C:\` or `/mnt/c`. Current WSL normally forwards Windows browser
   requests to a loopback listener in WSL. If the consent page opens but its
   callback cannot connect, update/check WSL localhost forwarding or use a
   trusted Linux/macOS desktop; do not bind the callback to a network address.
   Microsoft documents Windows-to-WSL `localhost` forwarding here:
   <https://learn.microsoft.com/en-us/windows/wsl/networking#accessing-linux-networking-apps-from-windows-localhost>
3. Place both the downloaded JSON and the output outside the Git checkout:

   ```bash
   install -d -m 0700 ~/.config/lecturesift
   install -m 0600 /path/to/downloaded-client.json ~/.config/lecturesift/adsense-desktop-client.json
   python3 deploy/adsense_oauth_loopback.py \
     --client-config "$HOME/.config/lecturesift/adsense-desktop-client.json" \
     --output "$HOME/.config/lecturesift/adsense-api.env" \
     --account-name accounts/pub-7608481350058806 \
     --site-domain lecturesift.com
   ```

The helper binds to `127.0.0.1` on a random port, verifies OAuth `state`, uses
PKCE `S256`, refuses redirects during the token exchange and suppresses HTTP
callback logging. It stores no access token. The refresh token and client
secret are written atomically only to the selected mode-`0600` file; they are
never printed. The generated fragment keeps
`LECTURESIFT_ADSENSE_API_ENABLED=false` so obtaining credentials cannot activate
the integration.

For an **External** OAuth consent screen left in Google's **Testing** publishing
status, a grant that includes this AdSense scope can produce a refresh token
that expires after seven days. Move the consent screen through the appropriate
Google production/publishing process before treating the credential as a
durable production connection, and rotate/revoke grants through Google rather
than trying to extend their lifetime in LectureSift. See Google's
[refresh-token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).

The helper and deployment examples default to the known public resource name
`accounts/pub-7608481350058806`. The status reader still selects that exact
account with a direct `accounts.get` request and separately requires the exact
`lecturesift.com` site from the site list. A grant for another account fails
closed with `account_not_found` or `permission_denied`, and a missing domain
fails with `site_not_found`. If the owning AdSense account shows a different
resource name, keep the feature off and confirm the exact `accounts/pub-...`
value in Google's account details or authenticated API Explorer before
activation. Do not guess between Google Ads and AdSense account identifiers.
The runtime also requires the numeric part of that account resource to match
the configured ad-serving publisher ID (`accounts/pub-X` ↔ `ca-pub-X`). A
mismatch is invalid configuration and no Google request is sent.

## Install on Render

In the `lecturesift-backend` service's private environment, set these three
credential values from a trusted local password manager or editor:

- `LECTURESIFT_ADSENSE_API_CLIENT_ID`
- `LECTURESIFT_ADSENSE_API_CLIENT_SECRET`
- `LECTURESIFT_ADSENSE_API_REFRESH_TOKEN`

The blueprint supplies the known non-secret account selector
`LECTURESIFT_ADSENSE_API_ACCOUNT_NAME=accounts/pub-7608481350058806`; replace it
only when the owning AdSense account details or an authenticated Google API
Explorer request proves a different exact name.

Keep `LECTURESIFT_ADSENSE_API_ENABLED=false` for the credential-only deploy.
Then change only that API flag to `true` and verify the private admin readiness
endpoint at `/billing/admin/advertising-readiness`. This enables status reads;
it does not enable ad serving, whose two
separate flags remain closed. Never paste the private fragment into a deploy
command, issue, commit, build log or Codex conversation. The worker and
Instagram services must not receive any of these credentials.

The default cold-check budget is 10 seconds, with a five-second ceiling on
each Google request. Successful summaries are cached for 300 seconds; failed
checks are cached for at most 60 seconds so a provider outage cannot cause an
admin-page request storm or hide recovery for a full success-cache interval.

## Install and recover on the VPS

Copy the fragment's assignments into the root-owned mode-`0600`
`/etc/lecturesift/runtime.env`, leave the API flag false, and run the normal
preflight. `deploy/generate_role_envs.py` admits the client secret and refresh
token only to the API role; its explicit worker and Instagram allowlists exclude
them. The normal encrypted configuration snapshot already includes
`runtime.env` and `api.env`, so no new backup payload or database schema is
needed. The local helper fragment is intentionally outside that snapshot and
can be removed after the secrets are installed and backed up.

After any restore or credential rotation, keep ad serving disabled, enable the
read-only status integration and require one authenticated admin probe to
succeed before considering the connection restored. Google revocation removes
the whole project's grant and invalidates every access and refresh token issued
for that user and project. Do not install a replacement token from the same
project and then revoke the old token, because that also revokes the replacement.
For a planned same-project rotation, disable the integration, revoke the grant,
rerun the helper with `--replace`, install the complete new credential set, and
then re-enable and probe it. A no-downtime planned rotation requires a separate
dedicated Google Cloud project/client: authorize and verify the replacement
first, then revoke the old project's grant. If a host or token leak is suspected,
disable the integration and revoke the affected project grant immediately;
authorize and install a fresh credential only after containment.
