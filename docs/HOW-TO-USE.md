# Aapoorti E-Franchise - How to use

Staff aur franchise partners ke liye practical Hinglish guide.

## 1. App open aur login

1. Online app: https://aapoorti-e-franchise.vercel.app/login
2. Isi computer par local app: http://localhost:8000/login
3. Admin se mila login ID/mobile aur password enter karein. Login ke baad apne role ka dashboard khulega.
4. **Account** se password change karein. **Operations** se apna Work Desk kholein.

Online app ke liye backend PC, internet, server aur Cloudflare tunnel running hone chahiye. Temporary tunnel restart par hosting connection update karna pad sakta hai. App na khule to administrator se contact karein.

Phone par Android Chrome mein browser menu se Install app/Add to Home screen; iPhone Safari mein Share > Add to Home Screen use karein.

## 2. Admin: pehli baar setup

1. Admin dashboard ke Network section mein outlet banayein.
2. POS, Guard aur Delivery staff banakar sahi outlet assign karein.
3. Franchise account banayein: owner/shop, login, address, outlet aur advance details.
4. Franchise active rakhein; delivery ke liye registered address check karein.
5. Catalogue mein products add/import karein. Selling price, weight, GST, SKU, commission aur slab eligibility check karein.
6. Commission rules, promotions aur delivery charges configure karein.
   - Pricing & compliance mein **Per km** ya **Fixed** delivery pricing choose karein. Fixed charge ke upar Standard base/Priority premium add hota hai; eligible Standard orders par free-delivery quota apply hota hai.
   - Promotion mein **Franchise eligibility** se ek partner choose karein ya All franchises rakhein. Same product ki partner-specific promotion global product promotion se pehle apply hoti hai.
   - Operations > **Fulfilment and dispatch policies** se outlet aur franchise ke allowed Pickup/Delivery modes set karein. Dono mein kam-se-kam ek common mode hona chahiye; disabled mode ka held order execute nahi hoga.
   - Docket policy **Optional**, **Required** ya **Auto** choose karein. Required mein single bill ke liye bhi docket chahiye. Auto mein POS ka bill handover same franchise/mode ke packed bills ko configured limit tak ek docket mein group karta hai.
7. Operations > Monthly target programs mein sales thresholds aur reward percentages set karein. Example: `50000:2` aur `100000:4` alag lines par.
8. Wallet mein verified advance/credit enter karein. Configured advance allocation ke hisaab se purchase wallet credit hota hai; default 75% hai.

Real invoicing se pehle supplier tax details configure karein. Online recharge/SMS ke liye provider setup zaroori hai; tab tak manual wallet funding aur app-generated receiver code use karein.

## 3. Franchise: customer ka order

1. **New order** kholein aur catalogue mein product search karein.
2. Product/pack size select karein; quantity add karein. Bill ki live commission dekhein.
3. **Bag / Review bag** kholein. Customer ka naam aur valid 10-digit mobile enter karein.
4. Franchise pickup ya delivery select karein. Delivery franchise ke registered address par hoti hai.
5. Items, customer total, delivery charge aur required purchase-wallet balance check karein.
6. **Use wallet & execute** se order place karein. Wallet kam ho to order **ON_HOLD** rahega.
7. Abhi execute nahi karna ho to **Save on hold** use karein; wallet debit nahi hota.
8. **Track orders** se status dekhein. Bill banne ke baad customer invoice print/save karein.

Held order ko fund karke execute karein. Billed order ka normal cancellation available nahi hai; correction ke liye Admin se contact karein.

## 4. Franchise: earnings aur monthly projection

Home dashboard par:

| Field | Kya batata hai |
|---|---|
| Monthly sales in target card | Current month ki delivered eligible sales |
| Target rewards earned | Achieved slab ke hisaab se actual target reward |
| Projected monthly sales | Ab tak ki delivered sales ki calendar-day average se month-end estimate |
| Projected monthly earning | Estimated product commission + active monthly programs ke target rewards |
| Nearest upcoming slab | Projected eligible sales ke barabar ya usse upar ka pehla configured slab |
| Earning at slab | Same product mix aur commission rate par slab tak pahunchne ki estimated earning |
| Extra eligible sales | Projection se us slab tak pahunchne ke liye sales gap |

Example: 30-day month ke day 10 tak ₹20,000 delivered eligible sales ho to projection ₹60,000 hai. Commission ₹2,000 ho to projected commission ₹6,000 hai. ₹50,000 par 2% aur ₹1,00,000 par 4% slabs hon to projected earning ₹7,200 hai. Nearest upcoming ₹1,00,000 slab par same mix se estimated earning ₹14,000, aur extra eligible sales ₹40,000 hogi.

Per-program comparison mein product commission + sirf us program ka reward hota hai; multiple comparison earnings ko add na karein. Overall projected monthly earning mein commission ek baar count hoti hai. Slab se excluded products eligible sales nahi badhate. Delivered eligible sales history zero ho to slab commission estimate available nahi hota.

Projection guaranteed income ya wallet balance nahi hai. Delivery complete hone par actual reward rules apply hote hain.

**How to earn more** screen par average basket, customers per day aur working days adjust karke apna plan dekhein. Ye manual planning calculator hai; Home ki actual-sales projection se alag hai.

**Business Summary** mein Today/Monthly/Yearly/Custom dates choose karein aur **Download PDF** se report nikalein.

## 5. POS: billing aur packing

1. **Operations** mein assigned outlet ka executed order kholein.
2. **Claim** karein, phir SKU/barcode scan karke ordered quantities pick karein.
3. Sab items pick hone par bill finalize karein.
4. POS dashboard mein bill **Packed** mark karein.
5. Zaroorat ho to same franchise aur same fulfilment mode ke bills ka docket banayein.
6. Operations se dispatch label print karein aur goods Guard ko handover karein.

Claim 15 minutes ka hai; successful scans renew karte hain. Extra quantity/unknown SKU rejected hoga. Camera unavailable ho to scanner/manual input use karein.

## 6. Guard: receipt, weight aur release

1. Operations mein bill/docket scan karke **Acknowledge receipt** karein.
2. Guard dashboard par weight enter karein, ya configured machine reading review/apply karein.
   **Seal inspection** bhi record karein. Intact seal confirm karein; broken seal ke saath reason enter karke goods hold karein. Repacking/recheck ke baad fresh intact inspection record karein. Default policy mein pending/broken seal goods release rokta hai. Broken seal release hamesha block karta hai.
3. **PASS** ho to release continue karein. **ALERT** aaye to items aur weight recheck karein; blocking enabled ho to release rukega.
4. Pickup ho to franchise receiver apne Operations/Work Desk se code generate kare.
5. Receiver code verify karke goods release karein.
6. Delivery ho to delivery task/driver handover flow complete karein.

Docket mein grouped bill ko alag release na karein. Actual goods receiver ko dene ke samay code verify karein.

## 7. Delivery staff: trip complete karna

1. Assigned delivery **Accept** karein.
2. Guard se goods lekar **Confirm pickup** karein.
3. **Start delivery trip** karein; map link se franchise address kholein.
4. Geofence enabled ho to Work Desk par fresh GPS capture karein.
5. Required proof mode ho to photo/signature upload karein.
6. Receiver franchise apne Work Desk se delivery code generate kare.
7. Receiver code enter karke delivery complete karein.
8. Delivery nahi ho payi to reason ke saath **Report failed delivery** karein. Admin reopen/reassign karega.

Receiver code 5 minutes mein expire hota hai; five wrong attempts par lock hota hai. Naya code generate karne ke beech minimum 30 seconds ka gap hai.

Live status updates connected screen ko automatically refresh karte hain aur Franchise ka active view preserve hota hai. Form edit/cart review chal raha ho to refresh notice aata hai, taaki unsaved changes safe rahein. Streaming unavailable ho to 15-second polling fallback hai.

## Automatic database backup

Is PC par `Aapoorti-E-Franchise-Daily-Backup` Windows task daily 02:00 local time par verified SQLite backup banata hai. Missed run next available session mein chal sakta hai; Windows user signed in aur PC available hona chahiye. Files `data/backups/` mein rehti hain; automatic deletion nahi hota. Admin periodically off-PC copy aur restore check karein.

Android/iOS preview projects aur build instructions [mobile setup](../mobile/README.md) mein hain. Local browser app: http://localhost:8000/login. Public link ke liye backend PC aur temporary Cloudflare tunnel running rehna chahiye.

## 8. Wallet aur settlement

- **Purchase wallet:** order execute karne ke liye.
- **Target earnings wallet:** target rewards ke credits.
- Franchise Account screen ke **Earnings → Purchase wallet** form mein available amount enter karke transfer karein.
- Admin manual credit/debit ke saath reason enter karein. Bank deposit verify karke hi credit karein.
- Admin Operations mein delivered orders ka outlet settlement batch banayein, CSV reconcile karein, phir bank payment outside app karein.
- Actual bank payment ke baad exact amount/reference se settlement **PAID** mark karein. App automatic bank transfer nahi karta.

## 9. Offline aur common problems

| Problem | Kya karein |
|---|---|
| Wallet insufficient / ON_HOLD | Admin se verified wallet funding karayein, phir execute karein |
| Order POS queue mein nahi | Order executed hai aur outlet mapping sahi hai, check karein |
| Session expired | Page reload/sign in karke current status check karein, phir action karein |
| Camera/GPS permission denied | Browser permissions allow karein; camera ke liye manual/scanner fallback available hai |
| OTP expired/locked | Receiver se allowed interval ke baad naya code generate karayein |
| Weight/count ALERT | Packing, quantities aur actual reading recheck karein |
| App online nahi khul raha | Admin PC, internet, backend aur tunnel check kare |
| Projection zero | Current month mein delivered sales hain ya nahi, check karein |

Already-open catalogue par internet disconnect ho to offline order draft save ho sakta hai. Reconnect aur sign in ke baad draft review karke manually submit karein. Offline drafts final executed orders nahi hain; wallet payment, OTP handover aur delivery completion ke liye online connection chahiye.

## 10. Roz ka closing check

1. Franchise: held orders, deliveries, invoice aur wallet activity check karein.
2. POS: unbilled/unpacked orders aur Guard ko pending handovers check karein.
3. Guard: unacknowledged receipts aur unreleased bills/dockets check karein.
4. Delivery: open/failed tasks aur pending receiver proof check karein.
5. Admin: exceptions, wallet ledger, delivered sales, settlement aur backups review karein.

Detailed workflow: [MODULE-WORKFLOWS.md](MODULE-WORKFLOWS.md). Technical setup: [DEPLOYMENT.md](DEPLOYMENT.md).
