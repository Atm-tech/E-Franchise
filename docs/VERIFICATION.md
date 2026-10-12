# Verification record

## Current verification - 12 October 2026

- Android debug build completed successfully with Android SDK platform 36 and the Capacitor 8 background-location plugin. Current artifact: `output/apk/Aapoorti-E-Franchise-debug.apk` (8,205,498 bytes; SHA-256 `590DF296EFD3A6BD581D2F57912F253A46887A377EBF8FB0CDDCFD0A03DC3E93`). This is a debug-signed preview; physical-device GPS acceptance is outstanding.
- Vercel MCP project requests returned HTTP 403 for scope `atm-techs-projects`; the ignored project-local OIDC credential allowed the Vercel CLI to inspect the project successfully. Its GitHub link is `Atm-tech/E-Franchise`, production branch `main`, root directory `deploy/vercel`. The CLI confirms the repository is already connected, but this Vercel project deploys only the static proxy, not the FastAPI backend. A live GET check on 12 October returned HTTP 502 for `/health`, `/ready`, and `/login`. The last recorded successful public check (7 October) used a temporary Cloudflare tunnel hosted on this PC. Production backend hosting and OSRM route/ETA configuration remain outstanding; `OSRM_ROUTING_URL` is blank in the example configuration.
- `adb devices -l` found no connected Android device, so GPS permission/background behavior could not be accepted on hardware.
- Deployment retry on 12 October confirmed MCP still returns HTTP 403 for project scope. A project-local Vercel OIDC credential and project link are present under the ignored `deploy/vercel` directory; the CLI can inspect the project and reports the GitHub repo is already connected. No Cloudflare named-tunnel credentials or production `.env` are present. A fresh `/login` request still returned HTTP 502. No deployment or Git push was made.

## Verification recorded 7 October 2026

- **24 automated tests passed** on isolated temporary databases. Includes the previous suite plus fixed delivery pricing, franchise-specific promotions, outlet/franchise mode restrictions, held execution restrictions, required/automatic dockets, seal holds/audits, JWT role scopes, expiry/logout/credential-version revocation, and the real bridge sender's signed HTTP format/replay behavior against the isolated API.
- Real Chromium at 390 × 844 verified five role workspaces without horizontal overflow, streaming-triggered refresh, preserved Franchise Orders view, and preservation of unsaved buyer input. No JavaScript page errors. Screenshots: ignored `data/pending-review/`.
- Local `/login` and the repaired public `https://aapoorti-e-franchise.vercel.app/login` returned HTTP 200. Public proxy now points to a running temporary Cloudflare tunnel. This is PC-dependent hosting, not an independent always-on backend.
- Daily 02:00 Windows backup task registered; a scheduled run completed with result code 0 and online backup integrity verified. The interactive Windows user must be logged in for the task to run.
- Android debug APK built successfully; Android and iOS Capacitor preview source generated and synced. Mobile npm audit reports zero vulnerabilities after the compatible UUID override. iOS requires Mac/Xcode for build and signing. These preview shells are not app-store releases.
- Signed serial scale/counter bridge added. Physical serial protocol, hardware, real GPS/camera and live payment acceptance still require external commissioning. Bank and SMS activation remain deferred by user request.

The historical record below describes checks performed before these changes.

Date: 17 September 2026.

## Automated integration suite

Full suite: `python -m unittest discover -s tests -p "test_*.py" -v`

Operations subset: `python -m unittest discover -s tests -p test_operations.py -v`

13 integration tests passed on isolated temporary databases. The suite covers:

- Five role dashboards/workspaces, CSRF rejection, password hashing/change, persistent settings and session invalidation.
- POS claim/scan requirements, over-picking rejection and outlet isolation.
- Single-bill delivery with actual generated receiver-code verification.
- Multi-bill docket receipt, label PDF and code-confirmed pickup; repeated release blocked.
- Exactly-once order debit/refund under repeat and concurrent execution.
- Manual onboarding/advance replay protection and append-only ledger enforcement.
- Signed captured-payment webhook validation, wrong amount/signature rejection and duplicate credits prevented.
- Ambiguous payment creation, provider reconciliation and no automatic retry.
- Failed-delivery reopen, geofence requirement and authenticated image proof.
- Signed simulated scale/counter readings and quantity mismatch alert.
- Target rewards on delivered eligible sales, target administration and bank-profile masking.
- Charge ownership, saved-distance use, interstate invoice tax and PDF summary.
- OTP attempt lockout, login rate limit, backup integrity and overwrite refusal.

Provider requests are mocked in tests. No live payment, bank transfer, SMS, or hardware operation was performed. The existing business database was not used for test transactions.

Two additional fresh-process production tests passed (`python -m unittest discover -s tests -p test_production.py -v`): secure-cookie/bootstrap behavior without demo data, and rejection of placeholder session secrets. Total: **15 automated tests passed**.

A read-only backup of the existing business database was migrated in a temporary directory. Wallet balances, user count, product count and existing saved settings were preserved; SQLite integrity check passed and legacy credentials became hashes in the copy. The original database was not modified.

## Browser verification

An isolated development server was started on port 8011 with runtime data under ignored `data/review-runtime/`.

- Login rendered and role quick-login navigation worked.
- Admin Work Desk and franchise Work Desk rendered with expected controls.
- Mobile viewport: 390 × 844; no horizontal document overflow observed.
- Header layout was corrected after the mobile visual check.
- Browser error inspection reported no JavaScript page errors for the checked screens.
- Actual browser offline mode was enabled on an open franchise catalogue. A draft was saved, then submitted after reconnecting; an executed order was created in the isolated database and the draft queue cleared.
- Screenshots are local verification artifacts under `data/review-runtime/`.

## Limits

These checks do not certify external deployment, physical devices, statutory tax compliance, production load capacity, or live provider behavior. Docker is unavailable in this environment, so the image/Compose configuration has not been runtime-validated. Public hosting and app-store packages are not claimed as verified here.
