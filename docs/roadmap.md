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

## Next: Search page completion

Search page is working on MVP/demo level, but it is not finished.

Tasks:

- Add quantity selector on search result before adding to cart
- Improve multiple offer UI
- Add “part not found / request quote” flow
- Improve VIN compatibility text and logic
- Prepare structure for real supplier API integration
- Later: diagrams / catalog browsing
- Later: My Parts Feed

## Payment flow

Current payment confirmation is demo-only.

Future payment tasks:

- Replace demo confirmation with real payment placeholder UI
- Add payment reference/payment_id structure
- Add payment status states:
  - pending
  - paid
  - failed
  - cancelled
  - unknown
- Add backend payment verification endpoint
- Prevent duplicate payments
- Show clear warning when payment status is unknown
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

- Real supplier API
- Operator dashboard
- Warehouse flow
- Trusted customer rules
- Weight/dimensions confirmation flow
- Real notifications
- Mobile app / PWA improvements
- Production deployment hardening