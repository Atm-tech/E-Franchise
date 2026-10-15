# Configuration and deployment

## Live deployment — 15 October 2026

E-Franchise runs on Render at `https://aapoorti-e-franchise.onrender.com` from
`Atm-tech/E-Franchise`. The Vercel entry point
`https://aapoorti-e-franchise.vercel.app/login` proxies to that backend through
`deploy/vercel/vercel.json`. PostgreSQL uses only the `e_franchise` schema in the
existing `aapoorti-db` database. The existing B CONNECT backend is the separate
`Atm-tech/b2b` repository; the nested local commerce checkout is a different app.

The Free Render instance sleeps when idle, so profit checks resume when it wakes.
Continuous scheduled checks require an always-on instance. Production disables
demo top-ups and uses the delayed profit mode.

Temporary public quick login is off by default. To enable a short testing window,
set `settings.testing_quick_login_until` to a Unix timestamp no more than 48 hours
ahead (24 hours is recommended). The login page then offers only the five named
test accounts `adm`, `frn`, `pos`, `grd`, `dlv`; it never embeds their passwords.
Anyone reaching that page can access those roles during the window. Setting the
value to `0` disables the buttons and invalidates quick-login sessions immediately;
expiry also invalidates them. Normal password sign-in is unaffected. Do not enable
this window when the application contains live business data.

`scripts/migrate_setup_postgres.py` rehearses a clean setup import by default;
`--commit` applies it. It preserves the bootstrap admin and imports local role
accounts with unique generated passwords, outlets, franchise configuration,
catalogue, pricing rules, target definitions and opening stock. It refuses
nonempty business tables and excludes orders, customers, balances, financial
history and test switches. Backups, generated logins and reports belong under
ignored `data/`; never commit them. Passwords from local demo accounts are not
valid for the migrated production accounts.

## Controls added on 7 October 2026

Admin Operations includes fulfilment availability per outlet/franchise, optional/required/auto docket policy and required seal inspection. Admin Pricing selects fixed or per-km distance charge; the existing standard base/instant premium and free-delivery quota still apply. Promotions can target one franchise. Guard must record an intact seal before release under the default policy; a broken seal always blocks release.

### Scheduled backups on this Windows PC

`scripts/install_backup_task.ps1` registers `Aapoorti-E-Franchise-Daily-Backup` for 02:00 local time, with missed-run handling. The task uses the signed-in Windows user's session and writes verified online SQLite backups to `data/backups/`. A real scheduled run was started and completed with result code 0. Backups are retained; copy them off-PC and exercise restore separately. On other hosts, run `python scripts/scheduled_backup.py` from the platform's scheduler.

### Serial scale/counter bridge

Install `requirements-device.txt`. Configure the existing `DEVICE_KEYS_JSON` server registration and set the bridge's matching secret in `DEVICE_BRIDGE_SECRET` without placing it in command arguments. Example for a stable scale line `ST 1.250 kg`:

```powershell
python scripts/device_bridge.py --port COM3 --baud 9600 --device-id guard-scale --kind BILL --entity-id 123
```

The default serial regex accepts only `ST <weight> kg`. Set `--pattern` to the manufacturer's stable-frame format with named groups `weight_kg` and/or `item_count`. Count must be integer and weight must be in kg. One invocation captures one reading for the selected dispatch. For HTTP/network ambiguity, retries use the same request ID/body to prevent duplicate readings. Reuse `--request-id` when retrying the same measurement across processes; never reuse it for a different reading. Guard reviews/applies readings and records seal inspection separately. Physical hardware and vendor protocol have not been commissioned.

### Hosting and mobile API

### Current development phase: local backend

Use the local FastAPI backend and its SQLite database while building and accepting the core workflows. No Render application service is required in this phase. Start it from the repository root with `python -m uvicorn app.main:app --reload --port 8000` and open `http://127.0.0.1:8000/login`. Keep `DATABASE_URL` unset for this local run; the app uses `data/e_franchise.db`. Do not use the demo database for production data.

At the final deployment stage, deploy E-Franchise from its own Git repository as its own web service, and point it at the already-existing Render PostgreSQL instance with `E_FRANCHISE_DB_SCHEMA=e_franchise`. This reuses the database service only; it does not replace or combine the B2B application. Do not create a new database or attach a service disk for this setup. The database URL and production credentials must be entered in Render's Environment settings, not committed to Git.

On 12 October 2026, the production backend was prepared to reuse the existing Render PostgreSQL service `aapoorti-db` (database `aapoorti_b2b`, Ohio). The database already contains other Aapoorti tables, so E-Franchise uses its own `e_franchise` schema; the existing database role has permission to create that schema. Create the backend directly from the Render Dashboard at [New Web Service](https://dashboard.render.com/web/new): choose GitHub repo `Atm-tech/E-Franchise`, branch `main`, Docker runtime/Dockerfile, Ohio region, plan `0.5c-512mb`, and health check `/ready`. Do not attach a disk or create another database. Add `DATABASE_URL` using the **Internal Database URL** from `aapoorti-db` → Connect, plus `E_FRANCHISE_DB_SCHEMA=e_franchise`, `APP_ENV=production`, `SEED_DEMO=0`, a random `SESSION_SECRET` (32+ characters), `BOOTSTRAP_ADMIN_LOGIN=admin`, and a unique `BOOTSTRAP_ADMIN_PASSWORD` (12+ characters). Keep the database URL and passwords in Render Environment only; never commit or paste them into chat. The E-Franchise PostgreSQL adapter creates its own tables on first startup; it does not import local SQLite development data. Existing local backup/restore scripts are SQLite-only and are not PostgreSQL backup tooling.

The last recorded public check was 7 October 2026: the Vercel proxy returned HTTP 200 for `/login` while forwarding to a temporary Cloudflare tunnel hosted from this PC. This is not always-on hosting; the tunnel can change or stop. On 12 October, Vercel MCP project requests returned HTTP 403, but the project-local Vercel CLI credential successfully inspected the project. The Vercel project is already connected to GitHub repository `Atm-tech/E-Franchise`, production branch `main`; the CLI confirms it is connected. Its root directory is `deploy/vercel`, so Git deployments publish the static proxy only, not the FastAPI backend. The current proxy points at the temporary tunnel, which returned HTTP 502 on 12 October. Docker/Caddy remains the prepared independent-host option for the backend.

The Android debug preview was rebuilt on 12 October 2026 with Android SDK platform 36 and the Capacitor 8 background-location plugin. Artifact: `output/apk/Aapoorti-E-Franchise-debug.apk`. It is signed with the debug key and still needs physical-device acceptance. `OSRM_ROUTING_URL` is blank, so the map can show driver/destination and a direct-line fallback, but road route and ETA stay unavailable until an operator configures a routing service.

Commit `26b5f07` was pushed to the connected GitHub `main` branch. Vercel created a production deployment with status `READY`, but its proxy still returns HTTP 502 because the Cloudflare tunnel backend is offline. The proxy build being ready does not mean the FastAPI backend is running.

Revocable mobile JWT endpoints and Android/iOS preview instructions: [mobile/README.md](../mobile/README.md). Bank/SMS activation is deferred. Live payment credentials and acceptance testing remain external requirements.

Daily app usage aur staff training ke liye [How to use](HOW-TO-USE.md) guide dekhein.

## Local development

```powershell
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `/login`. A fresh development database seeds demo users. Existing users, passwords, settings and catalogue are no longer reset each startup. Migration initializes during startup, not Python import. A pre-upgrade backup is created before upgrading an existing database below schema version 2.

`E_FRANCHISE_DATA_DIR` changes the runtime directory (database, proofs, backups, development session secret). `E_FRANCHISE_DB` overrides just the database path.

The default development accounts are listed in README. Change passwords through Account before sharing access. Local mode stores a random session secret under the ignored runtime directory. Production requires an explicit random secret.

Production startup rejects a database that still contains unchanged default demo credentials. Change them while running in development before moving existing data to production.

## Production container configuration

1. Copy `.env.example` to `.env` and replace placeholders. Plain Uvicorn does not auto-load `.env`; Docker Compose reads it.
2. Set a real domain, a random `SESSION_SECRET` of 32+ characters, and unique initial admin credentials (12+ character password).
3. Point DNS to the chosen host and allow ports 80/443 for Caddy TLS.
4. Run `docker compose --env-file .env config` and review the resolved settings privately; do not publish secret output.
5. Run `docker compose up --build -d` on the intended deployment host after validation.
6. Verify `/health` and `/ready`; sign in and create outlets before onboarding staff/franchises.

The container uses a non-root account, one Uvicorn worker and a persistent SQLite volume. Caddy terminates HTTPS. Secure cookies are enabled when `APP_ENV=production`; HTTP-only production browsing will not maintain login.

This configuration targets one application instance. It is not a horizontally scaled deployment. Load testing and a database migration strategy are required before expanding beyond this model. Docker image build and public deployment have not been executed in this workspace unless specifically recorded in VERIFICATION.md.

## Payment provider

Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`.

- Configure webhook URL: `https://YOUR_DOMAIN/integrations/razorpay/webhook`.
- Subscribe to `payment.captured` and `payment.failed`.
- Configure capture in your provider account; authorized-only payments do not credit wallets.
- Use test mode first. Verify a successful capture, failed attempt, delayed webhook, duplicate event, wrong amount and reconciliation.
- Local payment receipt is `EF-<transaction ID>`. Unknown order-creation timeouts require Admin reconciliation; do not blindly create another charge.
- Provider credits are 100% wallet top-ups. Manual advances use the configured advance allocation percentage.
- Payouts and payment-provider refunds are not automatically initiated by this release. Order cancellation refunds are internal wallet entries before billing only.

Reference: [Razorpay Standard Checkout integration](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/) and [webhook verification](https://razorpay.com/docs/webhooks/validate-test/).

## Receiver OTP / SMS

The receiver can generate an app code in their own authenticated franchise account. No external provider is needed for that flow.

Optional SMS requires `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, plus applicable sender/template configuration in the provider account. No real SMS was sent during automated tests. Test actual delivery and the approved message template before enabling SMS for operations.

Reference: [Twilio message resource](https://www.twilio.com/docs/messaging/api/message-resource).

## Maps and driving distance

The franchise dispatch view includes a live OpenStreetMap map. The driver's native Capacitor app can keep sharing GPS in the background after the driver starts tracking and grants permission; a persistent notification indicates tracking. The browser fallback needs the delivery page open. For the in-app road route and ETA, set `OSRM_ROUTING_URL` to an explicitly chosen OSRM service origin. No public demo server is automatically used. Driver coordinates and franchise destination coordinates are sent to that routing provider to calculate route and ETA; the ETA does not include live traffic.

Admin saves outlet/franchise coordinates, calculates a route and stores the driving distance. New orders use that server-side distance. Coordinate changes invalidate it; outlet remapping makes it ineligible until recalculated. When no saved route exists, the existing manually entered checkout distance remains in use and must be supervised operationally.

Geofence radius is configured in Operations. Zero disables it. A positive radius requires franchise coordinates and a driver location captured within five minutes. This uses straight-line distance and browser GPS; it is not tamper-proof location attestation.

Reference: [OSRM route API](https://project-osrm.org/docs/v5.24.0/api/#route-service).

## Scale/counter bridge

Set `DEVICE_KEYS_JSON` with a distinct secret and outlet for each hardware bridge:

```json
{"scale-01":{"secret":"YOUR_UNIQUE_RANDOM_DEVICE_SECRET","outlet_id":1}}
```

Send JSON to `POST /integrations/device/reading`:

```json
{"request_id":"unique-reading-123","entity_type":"BILL","entity_id":7,"weight_kg":1.03,"item_count":1}
```

Headers:

- `X-Device-Id`: registered device ID.
- `X-Device-Timestamp`: Unix timestamp, within 120 seconds.
- `X-Device-Signature`: hex HMAC-SHA256 of `timestamp + "." + raw_body`, keyed by the device secret.

The reading is restricted to the registered outlet. Duplicate identical request IDs return the original record; conflicting content is rejected. Guard reviews/applies the reading. Wrong weight or quantity raises an alert. Hardware serial/USB SDK adapters must be supplied for the actual machine model; the HTTP boundary does not itself read a physical scale.

## R2 images and delivery proof

Existing `R2_*` configuration continues to serve product images. Delivery photos/signatures are stored in the protected runtime `proofs/` directory and served only to authorized users. Back up this directory together with the database. Product image objects remain in R2 and need their own retention policy.

## Backup, restore and monitoring

```powershell
python scripts/backup_database.py
python scripts/backup_database.py --source data/e_franchise.db --destination data/backups/manual-20260917.db
python scripts/restore_database.py --backup data/backups/manual-20260917.db --destination data/restored-20260917.db
```

Backup uses SQLite's online backup API and checks integrity. Restore refuses to overwrite an existing destination. To switch to a restored database, stop the app, point `E_FRANCHISE_DB` at the restored file, then restart and check `/ready` plus a sample ledger/order.

Schedule backup execution in your host's task scheduler/cron. Also back up proofs and keep encrypted off-host copies with tested retention. The scripts do not install a scheduler or copy data off-host automatically.

`/health` checks the process; `/ready` checks database availability. Uvicorn logs, application audit records and the container health check are available. Configure uptime/error monitoring in the chosen hosting environment. Do not expose `.env`, runtime data, logs or database files through static hosting.

## Launch acceptance still required

- Real provider test-mode transactions/SMS, followed by approved live configuration.
- Physical scanner, camera, printer, scale/counter and target phone/tablet checks.
- Supplier tax identity/rates/place-of-supply/accounting review; no statutory filing/e-invoice integration is claimed.
- Backups and recovery on the deployment host, expected-concurrency load test, and independent security review.
- Separate native Android/iOS builds if app-store distribution is required; this release delivers the PWA.


## Automatic profit credit worker

The backend starts a profit worker automatically. It checks persisted due dates immediately on startup and every 60 seconds, credits eligible profit after return adjustments, and marks each order released in the same transaction. Missed credits are processed after restart. Failures roll back and retry on the next check.

Keep the backend running continuously; a sleeping or stopped host cannot run background checks. `PROFIT_CHECK_INTERVAL_SECONDS` changes the interval (minimum 1 second). `PROFIT_WORKER_ENABLED=0` disables the worker. Test environments disable it automatically. SQLite uses its existing write transaction; PostgreSQL release checks use a transaction advisory lock to serialize checks across processes. No extra database or queue is required.

For local flow testing, run `python scripts/profit_credit_mode.py immediate`. This credits existing eligible completed orders immediately and new completed orders at completion. Restore normal per-order due dates with `python scripts/profit_credit_mode.py delayed`. Both modes retain the worker. Already credited profits remain credited; switching modes never reverses credits. Immediate mode is disabled for production and remote databases.
