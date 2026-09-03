# Lion Parts Roadmap

ეს ფაილი ინახავს პროექტის მიმდინარე გეგმას, რომ შემდეგი ნაბიჯები არ დაიკარგოს.

## Current completed foundation

- Backend Django + DRF setup
- PostgreSQL setup
- Frontend React + Vite setup
- GitHub CI
- Backend tests for orders, cart, accounts, parts
- Demo parts search
- Backend cart flow
- Checkout flow
- Order detail page
- Item-level tracking
- Item action required flow
- Item event history
- Demo payment confirmation flow
- Order-level timeline status display
- Order timeline rendering cleanup

## Search MVP completed

Search page MVP is now working for the main customer ordering flow.

Completed:

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
- Checkout page shows order conditions before creating order
- Search result logging with raw and normalized provider responses

Search page is good enough for MVP checkout/payment work.

Remaining search polish for later:

- Improve multiple offer UI visually
- Improve VIN compatibility text and logic
- Improve part not found / request quote UX
- Add better search history/customer history
- Later: diagrams / catalog browsing
- Later: My Parts Feed

## My Parts Feed

My Parts Feed is postponed until customer identity/login is stronger.

Reason:

- Feed should belong to a real customer account, not only a guest browser session.
- It should use customer search history, saved parts, previous carts, previous orders and possibly VIN history.
- It will make more sense after phone login and customer account flow are completed.

Planned later:

- Show recent searched part numbers
- Show previously quoted parts
- Show saved/favorite parts
- Show parts connected to customer's VINs
- Show reorder / add again actions

## Current next step: Payment flow

Current payment confirmation is demo-only.

Next payment tasks:

- Add backend Payment model
- Add payment reference / payment_id structure
- Add payment status states:
  - pending
  - paid
  - failed
  - cancelled
  - unknown
- Add backend payment verification endpoint
- Connect payment status to Order status
- Prevent duplicate payments
- Show clear warning when payment status is unknown
- Replace demo payment confirmation UI with payment placeholder UI
- Later: connect real payment gateway

## SMS and login flow

Phone number will be the main customer identity.

Tasks:

- Add phone login flow
- Add demo OTP first
- Replace demo OTP with real SMS later
- Keep guest cart before login
- Merge guest cart into customer account after login
- Allow optional password login for frequent/trusted users
- Require verified phone for sensitive actions

## Customer support flow

Support should be written-only, not call/WhatsApp based.

Tasks:

- Add support request form
- Allow support from order page
- Allow support from specific order item
- Automatically attach order/item context
- Add simple customer-facing support status
- Keep operator/internal messages hidden from customer unless intentionally made visible
- Later: admin/operator support dashboard

## Later phases

- Operator dashboard
- Warehouse flow
- Trusted customer rules improvements
- Weight/dimensions confirmation and adjustment flow
- Real notifications
- Mobile app / PWA improvements
- Production deployment hardening