# Lion Parts Manual Testing Checklist

ეს ფაილი არის ხელით ტესტირების მთავარი სია. ყოველი ახალი ფუნქციის დამატებისას უნდა განახლდეს.

## Server startup

1. Pull latest main.
2. Run backend migrations.
3. Start backend on port 8000.
4. Start frontend on port 5173.
5. Open http://2.28.40.250:5173/
6. Check backend health at http://2.28.40.250:8000/api/health/

Expected backend health: status ok.

## Profile / phone login

Check:

- Phone number input is visible for logged out user.
- SMS code can be sent.
- SMS code can be verified.
- New phone requires customer name after code verification.
- Existing verified phone logs in without asking name again.
- Verified profile shows customer name and phone.
- Verified profile does not show SMS send form.
- Verified profile shows logout button.
- Logout clears current browser session.
- After logout, empty phone login form is shown.

## Search page

Page: /

Check:

- Backend status is ok.
- Search by OEM part number works.
- VIN is optional.
- Search result shows Quote ID.
- Customer sees final price in GEL only.
- Quantity input works.
- Add to cart works.
- Added item shows as already added.

Suggested test part numbers:

- 68275354AC
- 51118070648

## Search Feed

Page: /

Section: ჩემი ნაწილები / ბოლო ძიებები

Check:

- Feed is visible for verified phone.
- New search appears in feed.
- VIN appears in feed when search used VIN.
- Feed shows found count.
- Restart search button runs the same search again.
- Same verified phone in another browser/session can see the same feed.
- Different verified phone cannot see another customer’s feed.

## Cart

Page: /cart

Check:

- Added part appears in cart.
- Quantity is correct.
- Total GEL is correct.
- Remove item works.
- Checkout link/button works.

## Checkout

Page: /checkout

Check:

- Checkout shows phone verification form inline when phone is not verified.
- Checkout does not redirect to Profile for phone verification.
- After SMS verification, customer stays on Checkout.
- After verification, create order button becomes available.
- Customer name and phone are taken from verified profile.
- VIN can be entered.
- Comment can be entered.
- Order is created successfully.
- Customer is redirected to order detail.
- Order status is payment pending.
- Payment reference is visible.

## Orders list

Page: /orders

Check:

- Verified phone can see its own orders.
- Unverified user sees phone verification required card.
- Different verified phone cannot see another phone’s orders.
- Order list shows order number, status, total and date.

## Order detail

Page: /orders/<ORDER_NUMBER>

Check:

- Verified owner phone can open order detail.
- Different verified phone gets not found/error.
- Order total is visible.
- Payment pending message is visible when unpaid.
- Payment reference is visible when unpaid.
- Items list is visible.
- Item detail modal opens.
- Customer-visible timeline/history is visible.
- Internal/admin-only events are not visible to customer.

## Manual admin payment confirmation

Admin: http://2.28.40.250:8000/admin/

Check:

- Open Orders.
- Find payment pending order.
- Use admin action to mark selected order as paid manually.
- Refresh frontend order detail.
- Order status changes to processing.
- Payment status changes to paid.
- Item status changes to payment confirmed.

## Safety endpoints

These endpoints must return 404 by default:

- POST /api/orders/LP-TEST/demo-confirm-payment/
- POST /api/orders/LP-TEST/verify-payment/
- POST /api/orders/items/999/demo-request-change/
- POST /api/orders/items/999/demo-update-status/

## Admin search logs

Admin section: Parts / Part search logs

Check:

- Part number is visible.
- VIN is visible.
- Customer phone is visible.
- Customer name is visible.
- Session ID is visible.
- Provider is visible.
- Found count is visible.

## Automated checks before merge/release

Run:

./scripts/check.sh

Expected:

- Backend tests pass.
- Frontend mock E2E passes.
- Real backend E2E passes.
