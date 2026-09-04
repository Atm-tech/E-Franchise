# Aapoorti E-Franchise

A zero-inventory assisted-commerce platform: local franchise partners take customer orders on mobile, earn commission on every basket, and fulfil through their assigned Aapoorti outlet by pickup or distance-priced delivery.

Role-based web application starter for:
- Desktop-first `Admin`
- Mobile-first `Franchisee`, `Outlet POS`, `Delivery Guard`, `Delivery Man`

## Features Implemented
- Login with role routing
- Commerce-style franchise mobile experience with catalogue search and categories
- Multi-product customer baskets and assisted checkout
- Customer buyer records, outlet mapping, franchise pickup, and per-kilometre delivery to the franchisee's registered address
- Live commission visibility, wallet balance, and order tracking
- Multi-outlet data model with a dedicated outlet fulfilment queue
- Admin onboarding, product setup, and operational settings
- Franchise order placement with wallet check (`EXECUTED` vs forced `ON_HOLD`)
- POS billing from executed orders
- Docket creation for multi-bill flow
- POS to guard handover
- Guard weight check with tolerance-based `PASS/ALERT`
- Guard handover to franchise pickup or delivery task
- Delivery task status updates to `DELIVERED`
- SQLite persistence
- Cloudflare R2 product picture uploads and catalogue images
- Installable mobile/tablet PWA shell with standalone display and safe-area support
- Franchise banking profile, outlet mapping, activation controls, and staff role management
- Advance capture with configurable wallet allocation and immutable wallet ledger
- Manual wallet credit/debit, low-balance settings, held-order execution, cancellation refunds, and audit logs
- Product and department commission rules with product-priority resolution
- Date-based product or department promotions with fixed/percentage discounts
- Configurable free-delivery threshold/quota, per-km pricing, and delivery charge ownership
- Individual bill packing, same-franchise docket validation, and complete custody tracking
- Guard-side weight pass/alert workflow with configurable blocking
- Delivery acceptance, pickup, trip status, OTP/proof capture, and final order completion
- Full Admin, Franchisee, POS, Guard, and Delivery mobile/tablet dashboards

## Verification

Run the disposable-database end-to-end lifecycle test:

```powershell
Copy-Item data/e_franchise.db data/e2e_test.db -Force
$env:E_FRANCHISE_TEST_DB=(Resolve-Path data/e2e_test.db).Path
python tests/e2e_flow.py
```

The test covers admin onboarding/configuration, wallet debit/refund integrity, single-bill delivery with proof, and two-bill docket pickup.

## Run
1. Install dependencies:
```powershell
python -m pip install -r requirements.txt
```

2. Start server:
```powershell
python -m uvicorn app.main:app --reload --port 8000
```

3. Open:
- `http://127.0.0.1:8000/login`

## Install on a Phone or Tablet

The interface is an installable Progressive Web App (PWA) with a standalone app window, mobile safe-area support, an app icon, and a cached offline shell.

- Android/Chrome: open the deployed HTTPS address, then tap **Install app** (or browser menu → **Add to Home screen**).
- iPhone/iPad/Safari: open the deployed HTTPS address, tap **Share**, then **Add to Home Screen**.
- For same-Wi-Fi testing, start Uvicorn with `--host 0.0.0.0` and open the computer's LAN address from the device. Full installation and service-worker behavior require HTTPS outside localhost.

## Cloudflare R2 Product Pictures
Create an R2 bucket and an Object Read & Write API token scoped to that bucket. Enable a public development URL for testing or connect a custom domain for production. Set these variables before starting the server:

```powershell
$env:R2_ACCOUNT_ID="your-cloudflare-account-id"
$env:R2_ACCESS_KEY_ID="your-r2-access-key-id"
$env:R2_SECRET_ACCESS_KEY="your-r2-secret-access-key"
$env:R2_BUCKET_NAME="your-bucket-name"
$env:R2_PUBLIC_BASE_URL="https://your-public-bucket-domain" # optional
```

`R2_ENDPOINT_URL` can be supplied instead of `R2_ACCOUNT_ID`. `R2_PUBLIC_BASE_URL` is optional; without it, images are served through the authenticated application. Product uploads accept JPEG, PNG, and WebP files up to 5 MB.

For local development, E-Franchise also reuses the R2 credentials from the sibling `Sales managment/.env` file when its own environment variables are not set. Set `R2_CONFIG_FILE` to use a different shared credential file. Objects are isolated under the `e-franchise/products/` prefix and are served through the authenticated application when `R2_PUBLIC_BASE_URL` is not configured.

## Demo Users
- Admin: `adm / adm@123`
- Franchisee: `frn / frn@123`
- POS: `pos / pos@123`
- Guard: `grd / grd@123`
- Delivery: `dlv / dlv@123`

## Notes
- Data file: `data/e_franchise.db`
- Delivery rate, standard charge, and priority charge are configurable from Admin.
- This remains a demo-grade authentication setup; production deployment needs credential hashing, CSRF protection, API hardening, and proper maps/distance integration.
