# Lion Parts Roadmap

ეს ფაილი ინახავს პროექტის მიმდინარე მდგომარეობას და შემდეგ ნაბიჯებს.

## Completed foundation

- Backend Django + DRF setup
- PostgreSQL setup
- Frontend React + Vite setup
- GitHub repo with `main` and `dev` workflow
- GitHub CI on `main` and `dev`
- Backend tests for accounts, parts, cart and orders
- Frontend Playwright mock E2E tests
- Real backend + frontend Playwright E2E tests
- Local check scripts:
  - `scripts/check.sh`
  - `scripts/check-real-e2e.sh`

## Customer MVP completed

Main customer order flow works for MVP testing:

- Search by part number
- Real AMT supplier API integration
- Kursi.ge USD sell rate integration
- Customer tariff / markup logic
- Carrier service / USD per kg tariff logic
- Final customer price in GEL
- Quantity selector before adding to cart
- Multiple returned part options
- Manual/customer weight input for trusted customers
- Manual weight price recalculation
- Weight saved into cart and order items
- Customer notice for manually entered estimated weight
- Backend cart flow
- Checkout flow
- Payment pending order creation
- Manual admin payment confirmation
- Order detail page
- Orders list page
- Item-level tracking
- Item action required flow
- Item event history
- Order-level timeline display

## Customer identity completed

Phone number is now the main customer identity.

Completed:

- Real SMS verification with Sender.ge
- Demo SMS mode for tests and local E2E
- Verified phone required for checkout
- Verified phone required for orders list
- Verified phone required for order detail
- Existing verified phone can access old orders from another browser/session
- Verification flow asks only for phone + code first
- New phone asks for customer name only after verification
- Reusable verification required card
- Reusable phone verification form component

Current behavior:

- Orders are matched by verified phone number.
- Cart is still session-based.
- Same browser cart remains after verification.
- Same phone on another browser sees old orders, but does not receive old cart.

## Automated testing status

Current test layers:

1. Django backend tests
2. Frontend mock Playwright E2E
3. Real Django backend + frontend Playwright E2E

Rule:

- When a customer flow changes, update tests in the same step.
- Before merging `dev` into `main`, CI must be green.
- Before public launch or important merge, run `./scripts/check.sh` locally when possible.

## Current next step: Production safety cleanup

Before new features, clean public test/demo endpoints and customer-visible data.

Tasks:

- Remove or disable legacy customer-side demo payment confirmation endpoint.
- Remove or protect demo operator endpoints:
  - `demo-request-change`
  - `demo-update-status`
- Rename customer action endpoint:
  - from `demo-resolve-action`
  - to `resolve-action`
- Keep customer resolve-action available only for verified phone owner.
- Ensure customer API returns only events intended to be visible to customer.
- Add tests for hidden/internal events.
- Add tests for wrong-phone access blocking.

## Next product features after safety cleanup

### 1. Operator/admin order workflow

- Admin actions for item statuses:
  - payment confirmed
  - checking
  - purchased
  - received USA
  - shipped to Georgia
  - received Georgia
  - ready for pickup
  - completed
- Visible customer timeline messages where needed
- Internal notes hidden from customer

### 2. Support flow

- Support request from order page
- Support request from specific order item
- Auto-attach order/item context
- Customer-facing support status
- Internal/admin support handling later

### 3. Customer account polish

- Logout / switch phone flow
- Duplicate customers cleanup
- Optional password login for trusted/frequent customers
- Better profile page

### 4. Search polish

- Better multiple offer UI
- Better part-not-found UX
- Better VIN compatibility text
- Search history/customer history

### 5. Later phases

- My Parts Feed
- Diagrams / catalog browsing
- Operator dashboard
- Warehouse flow
- Real payment gateway
- Real notifications
- Production deployment hardening
- Mobile app / PWA improvements

## My Parts Feed

My Parts Feed is postponed until customer identity and order history are stable.

Planned later:

- Show recent searched part numbers
- Show previously quoted parts
- Show saved/favorite parts
- Show parts connected to customer's VINs
- Show reorder / add again actions
