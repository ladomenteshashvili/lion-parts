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


## Customer action required

Check:

- Admin can request price change confirmation from Order items.
- Admin can request ETA change confirmation from Order items.
- Customer sees action-required badge in the Orders navigation.
- Customer sees action-required summary at the top of Orders page.
- Customer sees action-required badge on the affected order card.
- Customer can open item details and approve the change.
- Customer can open item details and cancel the item if the change is not acceptable.
- After approval, item returns to checking and order returns to processing.
- After cancelling the only item, item becomes cancelled and order becomes cancelled.
- Different verified phone cannot approve or cancel another customer’s item.


## Support messaging

Check:

- Customer can open order detail and see Support section.
- Customer can send a support message for the whole order.
- Customer can send a support message linked to a specific item.
- Admin can open Order in Django Admin and add an operator reply in Support messages inline.
- Admin reply appears on customer order detail.
- Header Orders badge increases when there is unread operator reply.
- Orders page shows operator reply badge on affected order.
- Customer clicks “გასაგებია” and unread support badge disappears.
- Different verified phone cannot read or send support messages for another customer’s order.


## Final employee testing checklist

Use this checklist before release or before giving the app to real customers.

### 1. Login / Profile

- Phone number input is visible for logged out user.
- SMS code can be sent.
- SMS code can be verified.
- New phone requires customer name after code verification.
- Existing verified phone logs in without asking name again.
- Verified profile shows customer name and phone.
- Verified profile does not show SMS send form.
- Logout clears current browser session.

### 2. Search / Cart / Checkout

- Search by OEM part number works.
- VIN is optional.
- Customer sees final price in GEL only.
- Quantity input works.
- Add to cart works.
- Cart shows added item and correct quantity.
- Checkout creates order.
- Customer is redirected to order detail.
- Order status is payment pending.
- Payment reference is visible.

### 3. Admin payment and item tracking

- Admin can mark payment pending order as paid manually.
- Frontend order status changes to processing.
- Item status changes to payment confirmed.
- Admin can move item through checking, purchased, received USA, shipped to Georgia, received Georgia, ready for pickup and completed.
- Customer timeline/history shows customer-visible status changes.

### 4. Price change

- Admin can enter proposed_final_price_gel and request price confirmation.
- Customer sees Orders badge and action-required card.
- Customer can approve price change.
- Customer can cancel item if price is not acceptable.
- Different verified phone cannot approve or cancel the action.

### 5. ETA change

- Admin can enter proposed_eta_days and request ETA confirmation.
- Customer sees new ETA and expected arrival date.
- Customer can approve ETA change.
- Customer can cancel item if ETA is not acceptable.

### 6. Alternative part

- Admin can enter proposed_part_number.
- Admin can optionally enter proposed_name.
- Admin can optionally enter proposed_final_price_gel.
- Admin can optionally enter proposed_eta_days.
- Customer sees alternative part number.
- Customer sees changed price/ETA when provided.
- Customer can approve alternative part.
- Customer can cancel item if alternative is not acceptable.

### 7. Fitment issue

- Admin can request VIN fitment confirmation with action_message.
- VIN is not required for this test stage.
- Customer sees action required.
- Customer can approve.
- Customer can cancel item.

### 8. Weight correction before purchase

- Admin can request weight/dimensions price change before item is purchased.
- Customer sees normal approve/cancel decision.
- Approval applies changed price.
- Cancel cancels item.

### 9. Weight correction after purchase / notice only

- Item status is purchased or later.
- weight_source is manual/customer.
- Admin enters proposed_final_price_gel and requests weight/dimensions confirmation.
- Customer sees only “გასაგებია”.
- Customer must not see cancel button.
- Customer must not see normal confirmation button.
- Clicking “გასაგებია” clears badge.
- Item remains purchased or its current logistics status.
- Changed final price remains applied.

### 10. Support messaging

- Customer can send support message for whole order.
- Customer can send support message linked to item.
- Admin can reply from Order support messages inline.
- Customer sees operator reply.
- Header Orders badge increases for unread operator reply.
- Orders page shows operator reply badge.
- Customer clicks “გასაგებია” and unread badge disappears.

### 11. Admin operator task filters

Orders admin:

- “ახალი გადახდილი — შესამოწმებელი” filter works.
- “Customer პასუხს ელოდება” filter works.
- “Customer-ის ახალი შეტყობინება” filter works.
- “გზაში / ლოგისტიკა” filter works.
- “მზადაა გასაცემად” filter works.

Order items admin:

- “გადახდილია — შესამოწმებელი” filter works.
- “Customer პასუხს ელოდება” filter works.
- Purchased / received USA / shipped / received Georgia / ready pickup filters work.

Support messages admin:

- “Customer-ის ახალი შეტყობინება” filter works.
- “მონიშნე operator-ის მიერ წაკითხულად” action works.

### 12. Privacy

- Unverified user cannot see orders.
- Different verified phone cannot see another customer’s orders.
- Different verified phone cannot open another customer’s order detail.
- Different verified phone cannot send support message on another customer’s order.


## Notification magic links

Check:

- Admin creates operator support reply.
- Order customer notification is created automatically.
- Admin can copy Customer link from Order customer notifications.
- Opening `/n/<token>` works without phone verification.
- The notification message opens as a forced modal.
- Modal cannot be closed by background click or X button.
- Only “გასაგებია” closes the message.
- After “გასაგებია”, the notification becomes read.
- Support reply message also becomes read by customer.
- Order details are visible on the notification link page.
- Public notification link does not expose session_id.
- Public notification link does not allow approve/cancel/support send actions.
- Normal order detail page also pops unread operator support message on entry.


## Notification deep links UX

Check four notification link states:

### 1. Order update link

- Admin changes an order/item status that creates customer-visible order update.
- Order customer notification is created automatically.
- Open `/n/<token>`.
- Phone verification is not required.
- Page explains why the link was sent.
- Order summary is visible.
- Forced message modal appears if unread.
- Modal has no X/close.
- Only “გასაგებია” closes it.

### 2. Item update link

- Admin changes a specific item status or creates item-related update.
- Open `/n/<token>`.
- Page explains this is a part update.
- Affected item is highlighted.
- Affected item modal opens automatically.
- Customer can reopen item details from the page.

### 3. Support reply link

- Admin creates operator support reply.
- Notification is created automatically.
- Open `/n/<token>`.
- Page explains this is an operator reply.
- Support history is visible.
- The exact operator reply is highlighted.
- Forced unread modal shows the operator message.
- “გასაგებია” marks it read.

### 4. Action required link

- Admin requests price / ETA / fitment / alternative confirmation.
- Open `/n/<token>`.
- Page explains customer action is required.
- Affected item modal opens automatically.
- Changed price / ETA / alternative number is visible when provided.
- Public link does not allow approve/cancel.
- Page links customer to full order page for phone verification and decision.

