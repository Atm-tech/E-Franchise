# E-Franchise Functional Specification (Phase 1 + Phase 2 Hooks)

## 1) Scope
This document converts the business flow into implementable system requirements for:
- Admin onboarding and control panel
- Franchisee wallet and order execution rules
- Product and commission management
- Delivery charge logic
- Outlet settlement logic

Phase 1 focuses on manual/controlled operations with configurable rules.  
Phase 2 hooks are included for payment gateway, UPI wallet top-up, GST, and geofencing.

## 2) User Roles
- Admin
- Franchisee
- Outlet POS
- Delivery Guard
- Delivery Man

## 3) Core Business Flow
1. Admin onboards franchisee with profile, shop, and banking details.
2. Admin records franchisee advance amount.
3. 75% of advance is credited to franchisee wallet.
4. Franchisee places orders (Instant or Day Delivery).
5. Order can start as `ON_HOLD` or `EXECUTED`.
6. Outlet packs/delivers only `EXECUTED` orders.
7. Executed order total must be `<= wallet balance`; else force wallet recharge.
8. Commission is applied (department-level or product-level).
9. Franchisee payable to outlet = Bill Price - Commission.
10. Delivery pricing is configurable:
   - Free delivery up to N times/day when bill >= X
   - Otherwise per-km or minimum delivery charge rule

## 4) Phase-wise Features

## 4.1 Phase 1 (Build Now)
- Franchisee onboarding (without geofencing)
- Advance capture (manual entry)
- Auto wallet allocation (75% rule)
- Wallet ledger and balance checks
- Order management with hold/executed states
- Dispatch operations (POS billing, docketing, guard handover, delivery assignment)
- Multi-POS per outlet with floor picking + on-spot scanning/billing
- Guard-side manual weight entry and tolerance-based alert
- Delivery type pricing rules
- Product catalog upload (CSV/XLSX)
- Product/department commission setup
- Admin-level full configurability for thresholds/rates

## 4.2 Phase 2 (Next)
- Geofencing
- GST handling and invoicing
- Payment gateway for advance/top-up
- UPI wallet recharge automation
- Weighing machine integration for auto weight capture
- Counting machine integration for quantity mismatch checks

## 5) Data Model (Suggested)

## 5.1 Franchisee
- `id`
- `name`
- `mobile_number`
- `shop_name`
- `address`
- `area`
- `bank_account_number`
- `ifsc_code`
- `bank_name`
- `gst_number` (nullable in phase 1)
- `status` (ACTIVE/INACTIVE)
- `created_at`, `updated_at`

## 5.2 Wallet
- `id`
- `franchisee_id`
- `current_balance`
- `created_at`, `updated_at`

## 5.3 Wallet Ledger
- `id`
- `wallet_id`
- `entry_type` (ADVANCE_CREDIT, ORDER_DEBIT, MANUAL_CREDIT, MANUAL_DEBIT, TOPUP)
- `amount`
- `reference_type` (ADVANCE, ORDER, ADJUSTMENT)
- `reference_id`
- `remarks`
- `created_by`
- `created_at`

## 5.4 Franchise Advance
- `id`
- `franchisee_id`
- `advance_amount`
- `wallet_credit_percentage` (default 75)
- `wallet_credited_amount`
- `received_mode` (CASH/BANK/OTHER in phase 1)
- `received_at`
- `created_by`

## 5.5 Product
- `id`
- `sku_code`
- `product_name`
- `department`
- `brand`
- `image_url`
- `attributes_json`
- `unit_weight`
- `weight_unit` (G/KG)
- `pack_multiplier` (e.g., 1, 3, 5)
- `mrp`
- `rsp`
- `status`
- `created_at`, `updated_at`

## 5.6 Commission Rule
- `id`
- `scope` (DEPARTMENT/PRODUCT)
- `department` (nullable)
- `product_id` (nullable)
- `commission_type` (PERCENT/FIXED)
- `commission_value`
- `priority` (PRODUCT takes precedence over DEPARTMENT)
- `effective_from`
- `effective_to` (nullable)
- `status`

## 5.7 Order
- `id`
- `order_number`
- `franchisee_id`
- `outlet_id`
- `delivery_type` (INSTANT/DAY)
- `order_status` (ON_HOLD/EXECUTED/CANCELLED/DELIVERED)
- `item_subtotal`
- `commission_total`
- `delivery_charge`
- `grand_total`
- `wallet_debit_amount`
- `payable_to_outlet`
- `distance_km` (optional in phase 1)
- `created_at`, `updated_at`

## 5.8 Order Item
- `id`
- `order_id`
- `product_id`
- `qty`
- `unit_rsp`
- `line_total`
- `applied_commission`

## 5.9 Delivery Rule Config
- `id`
- `instant_premium_charge` (fixed or percent based on config)
- `day_delivery_charge`
- `free_delivery_threshold_amount` (X)
- `free_delivery_limit_per_day` (e.g., 3)
- `below_threshold_charge_mode` (PER_KM/FIXED)
- `below_threshold_rate_per_km`
- `below_threshold_fixed_charge`
- `weight_tolerance_mode` (ABSOLUTE/PERCENT/BOTH)
- `weight_tolerance_value`
- `weight_tolerance_percent`
- `active_from`
- `status`

## 5.10 Dispatch Weight Check
- `id`
- `entity_type` (BILL/DOCKET)
- `entity_id`
- `expected_weight`
- `actual_weight`
- `variance`
- `tolerance_applied`
- `check_status` (PENDING/PASS/ALERT)
- `checked_by`
- `checked_at`

## 6) Critical Business Rules

## 6.1 Wallet Allocation
- On advance receipt: `wallet_credit = advance_amount * 0.75`
- Remaining 25% handled as non-wallet amount (tracked in advance record only).

## 6.2 Order Execution Rule
- `ON_HOLD` orders do not trigger packing/delivery.
- Transition `ON_HOLD -> EXECUTED` allowed only if:
  - order total (or wallet debit amount) `<= wallet current_balance`
- If insufficient, block transition and show “Add wallet balance”.

## 6.3 Wallet Debit Timing
- Debit wallet only when order becomes `EXECUTED`.
- If order canceled before execution, no debit.

## 6.4 Commission Resolution
- Product-level commission overrides department-level commission.
- If neither exists, fallback commission = 0 (or admin default if configured).

## 6.5 Outlet Settlement
- `payable_to_outlet = bill_price - commission_total`
- Delivery charges treatment (who bears it) should be configurable:
  - Charged to franchisee
  - Charged to customer
  - Absorbed by business

## 6.6 Delivery Charge Logic
- If bill amount `>= X` and free delivery quota not exhausted for the day, delivery charge = 0.
- Else charge as configured (per km or fixed).
- Instant orders add premium delivery component.

## 6.7 Weight Validation Logic
- Expected weight is calculated from product master and billed quantity:
  - `expected_line_weight = unit_weight * pack_multiplier * qty`
  - `expected_bill_weight = sum(expected_line_weight)`
- Guard captures actual weight during handover.
- If variance exceeds configured tolerance, system raises alert for quantity/item recheck.
- Admin can configure if alert is blocking or warning-only.

## 7) Admin Panel Modules

## 7.1 Franchisee Management
- Create/edit franchisee
- View wallet balance and ledger
- Capture advance and auto-credit wallet (75%)
- Activate/deactivate franchisee

## 7.2 Wallet Management
- Wallet ledger viewer
- Manual credit/debit with reason
- Low-balance alert threshold

## 7.3 Product Management
- Single product create/edit
- Bulk upload via CSV/XLSX
- Image + attributes + MRP + RSP
- Department mapping
- Unit weight + pack multiplier setup for expected-weight calculations

## 7.4 Commission Management
- Checkbox/toggle for commission mode:
  - Department-level
  - Product-level
- Rule priority handling
- Effective date based activation

## 7.5 Delivery Rules
- Configure:
  - Free delivery threshold X
  - Free deliveries/day (default 3)
  - Below-threshold charge strategy
  - Instant premium pricing
  - Day delivery base charge

## 7.6 Order Control
- Order list by status (ON_HOLD / EXECUTED / DELIVERED / CANCELLED)
- Status transition controls with wallet validation
- Financial breakdown preview before execution

## 8) Franchisee App Requirements
- View catalog (product name, image, attributes, MRP, RSP)
- Place order with delivery type selection
- Choose hold or execute mode
- See wallet balance in real time
- See required top-up amount if insufficient balance
- See commission impact and payable amount

## 9) Outlet App Requirements
- View only `EXECUTED` orders for packing/delivery
- On-hold orders hidden from dispatch queue
- View payable and delivery notes
- Support multiple POS operators at same outlet
- Floor picking flow with scan-and-bill
- Selective handover of all or selected bills to guard

Detailed POS/Guard/Delivery workflows are defined in:
- `docs/e-franchise-operations-modules.md`

## 10) CSV/XLSX Product Upload Format
Use the template file at `docs/product_upload_template.csv`.

Mandatory columns:
- `sku_code`
- `product_name`
- `department`
- `mrp`
- `rsp`

Optional columns:
- `brand`
- `image_url`
- `attributes_json`
- `unit_weight`
- `weight_unit`
- `pack_multiplier`
- `commission_mode` (`DEPARTMENT` or `PRODUCT`)
- `commission_type` (`PERCENT` or `FIXED`)
- `commission_value`
- `status` (`ACTIVE`/`INACTIVE`)

Validation examples:
- `mrp >= rsp`
- `commission_value >= 0`
- `unit_weight > 0` when weight check is enabled
- `pack_multiplier >= 1`
- `commission_mode=PRODUCT` requires product-level commission fields

## 11) Blinkit-style Display Mapping
For product card display parity:
- Product image
- Product name
- Pack size/attributes
- MRP (struck through if needed)
- Selling price (RSP)
- Delivery ETA badge (Instant/Day)
- Commission-adjusted indicator (internal view only where required)

## 12) API Endpoints (Suggested)
- `POST /admin/franchisees`
- `POST /admin/franchisees/{id}/advance`
- `GET /admin/franchisees/{id}/wallet`
- `POST /admin/wallets/{id}/adjustments`
- `POST /admin/products/upload`
- `POST /admin/commissions`
- `POST /orders`
- `PATCH /orders/{id}/status`
- `GET /orders/{id}/pricing-breakdown`
- `POST /dispatch/weight-check`
- `GET /dispatch/weight-alerts`

## 13) Non-Functional Requirements
- Full audit trail for pricing, commissions, wallet entries, and status changes
- Role-based access control by module
- Config changes must be versioned with effective dates
- Idempotent wallet/order financial operations

## 14) Open Decisions to Freeze Before Development
- Tax basis for wallet debit and commission (pre-tax or post-tax)
- Exact party bearing delivery charge in each scenario
- Cancel/refund policy after execution
- Default commission when no rule exists
- Whether negative wallet is ever allowed (recommended: no)
