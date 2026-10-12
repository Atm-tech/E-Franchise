# Core Order Flow

This is the base Aapoorti E-Franchise order journey. Build and verify this lifecycle before introducing optional earning, recognition or loyalty features.

## Main journey

| Step | Owner | Action and result | Franchise-facing status |
| --- | --- | --- | --- |
| 1. Build basket | Franchise partner | Select active catalogue products, enter the buyer's name and mobile, choose pickup or delivery to the registered franchise address, and review item total, delivery charge, wallet requirement and commission. | Draft stays in checkout. |
| 2. Confirm order | Franchise partner | Submit once. A sufficient purchase wallet executes the order and records its debit. An insufficient wallet leaves the order on hold without a debit. The request key prevents a repeat submission from creating a second order. | Order confirmed, or waiting for wallet balance. |
| 3. Execute a held order | Franchise partner or authorized admin | After wallet funding, execute the held order. The app validates that the selected fulfilment mode remains allowed, then debits the wallet once. | Order confirmed and sent to outlet. |
| 4. Pick and bill | Outlet POS | Claim the order, scan/pick the requested quantities, then create its bill. Billing is blocked until required picking is complete. | Outlet bill created. |
| 5. Pack dispatch | Outlet POS | Pack each bill. If required or useful, group compatible packed bills for one franchise and fulfilment mode into a docket. | Order packed; optionally grouped into a docket. |
| 6. Transfer to guard | Outlet POS | Hand the bill or docket to the guard. A bill already in a docket cannot travel separately. | Sent to guard desk. |
| 7. Receive and verify | Delivery guard | Acknowledge receipt, inspect and record the seal, record actual weight, and resolve any quantity or weight alert. Configured policy controls whether an alert blocks release. | Received by guard; weight passed or needs recheck. |
| 8a. Franchise pickup | Guard and franchise partner | Guard releases the intact, verified dispatch to the franchise partner after validating the short-lived pickup code. | Picked up, then delivered/complete. |
| 8b. Delivery to franchise | Guard and delivery staff | Guard assigns the dispatch to a delivery task after verification. The dispatch stays at the guard desk until staff accept and physically confirm pickup; after starting live sharing, the mobile app sends background GPS updates with a visible tracking notification. | Delivery assigned, accepted, collected, and on the way; the partner can follow the current location, route and provider ETA. |
| 9. Receive and close | Franchise partner and delivery staff | Partner generates a receiver code. Delivery staff validates it (and any required proof/geofence) before completing the task. The order is marked delivered and eligible target rewards are refreshed. | Delivered - order complete. Invoice is available after billing. |

## Exception paths

- **Wallet shortfall:** Keep the order on hold and do not debit it. Show the required and available balances, and offer execution after funding.
- **Cancellation:** Permit cancellation only while the order has not entered billing/fulfilment. Refund any executed wallet debit exactly once and record the reason in the order history.
- **Weight or seal issue:** Keep custody at the guard until rechecked. A broken seal blocks release. A weight alert follows the administrator's block/recheck policy.
- **Delivery failure:** Record the reason and preserve the last delivery stage. An administrator can reopen or reassign with an audit record; reassigning a failed task resumes it from its last valid stage. The order must not be marked delivered until receiver proof is verified.
- **Duplicate submissions:** Repeated order-submit requests with the same key return the original order. Financial mutations must remain idempotent.
- **Grouped dispatch:** A docket is the custody unit. Its bills move together through guard and delivery; do not complete only some of its orders from a shared handover.
- **Live tracking:** Driver must start location sharing and grant location/notification permission. The native mobile app can continue in the background with its visible notification; browser GPS needs the active delivery page open. The franchise dispatch screen refreshes every 10 seconds and marks a location older than 90 seconds as stale. The route/ETA needs configured `OSRM_ROUTING_URL`; it is a provider estimate without live traffic. If GPS or routing is unavailable, the order status and receiver-code flow remain available.

## Status history

Every meaningful transition should create an order event with actor, time and a user-safe description. The franchise order desk presents these events newest first, while the dispatch screen identifies the current bill/docket custody state. Financial effects remain in append-only wallet ledgers; order events explain the operational journey and do not replace those ledgers.

## Completion criteria

An order is complete only when its pickup or delivery handover is verified, its bill/docket and order statuses agree, and the relevant wallet, invoice, target-reward and audit records agree with the outcome. Failed or reopened tasks remain active until a verified receipt or an audited admin resolution.
