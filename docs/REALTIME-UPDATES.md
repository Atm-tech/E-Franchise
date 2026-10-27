# Automatic UI updates

The shared application shell synchronizes Admin, Franchise, POS, Guard, Delivery,
account and operations pages. Normal same-page POST forms use fetch and patch
the returned HTML; JSON mutations also invalidate the current view. Actions that
redirect to a different page still navigate normally. Login and logout retain
their full navigation flow.

`app/realtime.py` installs transactional database triggers at startup for business
tables in SQLite and the configured PostgreSQL schema. A committed insert,
update or delete changes an opaque revision. Rollbacks and unchanged updates do
not. Timestamps used only for bookkeeping are ignored. Driver GPS coordinates
use the existing map/location update channel instead of invalidating every page.

The authenticated SSE feed checks for changes every three seconds. A fifteen
second revision poll recovers when streaming is unavailable. Returning to the
app or reconnecting also requests current state. Notifications contain no business
records: HTML is fetched through each page's existing authorization and filters.

`ui-sync.js` uses the locally vendored MIT-licensed morphdom 2.7.8 to patch changed
DOM nodes without reloading or executing fetched scripts. It preserves selected
tabs, focus, scroll, open details, unsaved forms, file inputs and the active basket.
The franchise hydration hook updates client-side prices, stock, commission,
wallet and delivery calculations as well as rendered labels. A changed basket
quote returns to review before submission. Completed checkouts clear their own
basket, while unsuccessful writes retain entries. Writes are never retried
automatically after a network error.

Dynamic modules use delegated events or idempotent `app:updated` handlers for
new controls. Customer profile cards are reconciled separately to retain open
editors. New page modules should use the same event for derived client-side state
and provide stable `data-ui-key` attributes on changing record lists.

Verification:

- `python -m unittest discover -s tests -p test_realtime.py -v`
- `python -m unittest discover -s tests -p test_production.py -v`
- `python scripts/verify_realtime.py`
- `python scripts/verify_browser.py`

The realtime browser test uses an isolated temporary SQLite database and five
separate authenticated browser contexts. It covers catalogue and wallet changes,
draft preservation, form success/validation errors, customer editing, checkout,
POS claiming/scanning/billing/packing, guard receipt/weight checks, polling-only
clients and reconnect recovery. It also covers onboarding GPS capture and
membership cancellation: confirmation, authorization, unsettled wallet/open-order
guards, immediate removal, session/token revocation, history retention and replay
protection. Membership cancellation archives the franchise with `CANCELLED`
status; it does not erase historical business records. Production-mode startup and revision tracking
were also checked against a disposable local PostgreSQL 16 cluster.

The older `test_operations.py` fixture patches `app.main.DB_PATH`, although
database configuration moved to `app.core` during route extraction. The broad
suite currently fails in that existing fixture; it is not a passing regression
baseline for this change.
