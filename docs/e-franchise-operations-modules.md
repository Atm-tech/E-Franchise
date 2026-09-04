# E-Franchise Operations Modules (Admin + POS + Guard + Delivery)

## 1) Objective
Define operational modules and handover flow from outlet billing to final delivery/pickup for:
- Single bill dispatch
- Multiple bills packed individually and grouped into one docket

This specification is aligned with later mobile rollout (Android APK + iOS).

## 2) Modules

## 2.1 Admin Module
Responsibilities:
- Franchise onboarding and profile management
- Product creation/upload and updates
- Commission configuration (department/product level)
- Special deals/promotions setup
- Delivery and dispatch rule setup
- Outlet, delivery guard, and delivery man user management

Key configurable settings:
- Delivery mode availability by franchise/outlet:
  - `FRANCHISE_PICKUP`
  - `DELIVERY_MAN`
  - `BOTH`
- Docket policy:
  - Auto-generate docket for multi-bill
  - Optional docket for single bill
- Maximum bills per docket (optional control)
- Deal eligibility rules by product/department/franchise/date

## 2.2 Outlet POS Module
Responsibilities:
- Support multiple POS users/devices inside one outlet
- Create/finalize bill(s) for franchise order(s)
- Scan products directly from floor/rack while picking
- Mark items packed status
- For multi-bill scenario:
  - Ensure each bill is packed individually
  - Bundle all packed bills under one docket
- Transfer custody to delivery guard with scan/OTP confirmation

POS actions:
1. Select franchise and open pending executed orders.
2. Pick items from racks and scan SKU on floor using handheld/mobile POS.
3. Generate bill per order.
4. Mark each bill as `PACKED_INDIVIDUALLY`.
5. Create `DOCKET` (single or multi-bill).
6. Print/label docket with docket number and bill numbers.
7. Handover to guard (`HANDED_TO_GUARD`) with option to hand over all or selected bills.

## 2.3 Delivery Guard Module
Responsibilities:
- Receive packed bills/dockets from POS
- Verify counts, seal status, and measured weight
- Hold custody until pickup
- Handover to:
  - Franchisee (if pickup selected), or
  - Delivery man (if delivery selected)

Guard actions:
1. Accept handover from POS (scan docket/bill code + optional OTP).
2. Status becomes `AT_GUARD_DESK`.
3. Measure actual weight and enter value (manual in phase 1, machine in phase 2).
4. System compares expected vs actual weight using tolerance rules.
5. If above tolerance, raise alert: "Check quantity/items before dispatch."
6. Wait for assigned receiver.
7. Execute handover confirmation with receiver authentication.

## 2.4 Delivery Man Module
Responsibilities:
- Receive assignment for bill or docket
- Pickup from guard desk
- Navigate to franchise location
- Deliver and capture proof of delivery

Delivery man actions:
1. Accept assigned task.
2. Pickup from guard (`PICKED_BY_DELIVERY_MAN`).
3. Start trip with map directions.
4. Deliver to franchise and collect POD (OTP/photo/signature).
5. Mark `DELIVERED`.

## 2.5 Franchisee App Module
Responsibilities:
- View assigned bill number(s)/docket number
- Choose fulfillment mode (pickup or delivery) at order time (if enabled)
- Track status and expected readiness
- Confirm pickup/delivery with OTP/PIN

Display requirements:
- Show both `docket_number` and underlying `bill_numbers` for multi-bill
- Show pickup point and receiver instructions

## 3) Dispatch Entities

## 3.1 Bill
- `bill_id`
- `bill_number`
- `franchisee_id`
- `outlet_id`
- `amount`
- `expected_weight_kg`
- `actual_weight_kg` (captured at guard desk)
- `weight_variance_kg`
- `weight_check_status` (PENDING/PASS/ALERT)
- `pack_status` (UNPACKED/PACKED_INDIVIDUALLY)
- `dispatch_status`

## 3.2 Docket
- `docket_id`
- `docket_number`
- `franchisee_id`
- `outlet_id`
- `docket_type` (SINGLE_BILL/MULTI_BILL)
- `bill_count`
- `total_amount`
- `expected_weight_kg`
- `actual_weight_kg`
- `weight_variance_kg`
- `weight_check_status`
- `created_by_pos_user`
- `created_at`

## 3.3 Docket-Bill Mapping
- `id`
- `docket_id`
- `bill_id`
- `sequence_no`

## 3.4 Handover Log
- `id`
- `entity_type` (BILL/DOCKET)
- `entity_id`
- `from_role` (POS/GUARD/DELIVERY_MAN/FRANCHISEE)
- `to_role` (GUARD/DELIVERY_MAN/FRANCHISEE)
- `from_user_id`
- `to_user_id`
- `handover_mode` (SCAN/OTP/MANUAL_OVERRIDE)
- `timestamp`
- `remarks`

## 4) State Machine

## 4.1 Dispatch Statuses
- `READY_FOR_BILLING`
- `PICKING_ON_FLOOR`
- `BILLED`
- `PACKED_INDIVIDUALLY`
- `DOCKET_CREATED` (if grouped)
- `HANDED_TO_GUARD`
- `AT_GUARD_DESK`
- `WEIGHT_CHECK_PENDING`
- `WEIGHT_CHECK_ALERT`
- `WEIGHT_CHECK_PASSED`
- `PICKED_BY_FRANCHISEE` or `PICKED_BY_DELIVERY_MAN`
- `OUT_FOR_DELIVERY` (delivery-man path)
- `DELIVERED`

## 4.2 Guardrails
- Multi-bill docket creation allowed only if all bills are `PACKED_INDIVIDUALLY`.
- A bill linked to a docket cannot be handed over separately unless docket is split/cancelled.
- Handover requires acknowledgement from receiver.
- Franchisee app must always show latest custody owner.
- Expected weight is computed from product master data and billed quantity.
- If weight variance exceeds tolerance, dispatch is blocked or override-required (admin configurable).

## 5) Workflows

## 5.1 Single Bill, Franchise Pickup
1. POS bills and packs order.
2. POS optionally creates single-bill docket or uses bill directly.
3. POS hands to guard.
4. Franchise receives bill/docket number in app.
5. Franchise picks from guard and confirms OTP.

## 5.2 Single Bill, Delivery Man
1. POS bills and packs order.
2. POS hands to guard.
3. Guard hands to delivery man.
4. Delivery man delivers to franchise and captures POD.

## 5.3 Multi-Bill, Franchise Pickup
1. POS creates multiple bills for same franchise.
2. Each bill is packed separately.
3. POS creates one docket containing all bills.
4. Guard receives one docket.
5. Franchise picks up using docket number and sees all bill numbers in app.

## 5.4 Multi-Bill, Delivery Man
1. POS packs all bills individually.
2. POS wraps and labels single docket.
3. Guard hands one docket to delivery man.
4. Delivery man delivers docket; app marks all mapped bills delivered.

## 6) Admin UI Requirements (Additional)
- Dispatch dashboard:
  - By outlet, franchise, status, docket number
- Docket tracking view:
  - bill list, current custody, timestamps
- Weight compliance view:
  - expected weight, actual weight, tolerance, variance, alert reason
- Operational controls:
  - Reassign delivery man
  - Reopen failed delivery
  - Force close with audit reason
- Outlet POS concurrency controls:
  - active POS devices/users per outlet
  - conflict prevention for same order/bill editing

## 6.1 Product Weight Configuration
- Product upload should include:
  - base unit weight
  - unit of measure
  - pack quantity multiplier (e.g., pack of 3, pack of 5)
- Expected line weight formula:
  - `line_weight = base_unit_weight * pack_multiplier * billed_qty`
- Bill expected weight = sum of line weights
- Docket expected weight = sum of mapped bill weights

## 7) Suggested APIs
- `POST /pos/bills`
- `PATCH /pos/bills/{id}/pack-status`
- `POST /pos/dockets`
- `POST /dispatch/handover`
- `GET /dispatch/dockets/{docketNumber}`
- `GET /franchisee/dispatches`
- `POST /delivery/tasks/{id}/accept`
- `POST /delivery/tasks/{id}/pod`

## 8) Mobile Readiness (APK + iOS)
- Keep APIs channel-agnostic (same backend for web/admin and mobile apps)
- Use role-based JWT scopes for Admin/POS/Guard/Delivery/Franchise
- Implement event/websocket updates for status changes
- Ensure barcode/QR and OTP flow is supported on both Android and iOS
- Ensure floor-scan UX works on handheld/mobile POS form factor

## 9) Decisions to Freeze
- Is single-bill docket mandatory or optional?
- Is OTP required at every handover or only final handover?
- Can one docket include bills from multiple franchises? (recommended: no)
- What is the SLA timer from `AT_GUARD_DESK` to pickup?
- Is dispatch blocked on weight alert or only warning with supervisor override?
- What tolerance model to use (absolute kg, percentage, or both)?

## 10) Phase-2 Machine Integrations
- Weighing machine integration at guard desk for automatic `actual_weight_kg`
- Counting machine integration for auto quantity mismatch detection
- Combined validation model:
  - Weight mismatch alert
  - Quantity mismatch alert
  - Escalation workflow before final handover
