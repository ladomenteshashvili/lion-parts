# Disabled legacy endpoints

Since **LPK-98 / LP-BE-103** the legacy `session_id` API was **not routed**; **LP-BE-1201 (M6)** finished its
removal. It identified customers by a client-generated session token (backend-review F1–F4, F6) and could
not pass the new `CsrfSessionAuthentication` + `IsCustomer` defaults. No authentication bypass was ever added.

All legacy views, serializers, URL modules and their skipped tests are deleted (accounts in LP-BE-201,
parts in LP-BE-402, cart in LP-BE-502/503, orders in LP-BE-601); LP-BE-1201 removed the last shims
(`accounts/customer_sessions.py`, `api/legacy.py`, the empty legacy `parts/tests.py`). The routes that the
new contract does not serve again are frozen in `api/tests/test_legacy.py::REMOVED_ROUTES` and asserted to
return the JSON 404 for every method. `grep -rn "session_id" backend --include=*.py | grep -v migrations`
is empty. The legacy browser frontend in `frontend/` does not work against this backend; the external
frontend builds against the new contract (AI_IMPLEMENTATION_GUIDE §8).

| Method | Legacy route | Replaced by | New endpoint (G§8) |
|---|---|---|---|
| POST | `/api/accounts/send-code/`, `/api/accounts/verify-code/` | LP-BE-204, LP-BE-205 | `/api/auth/register/`, `/api/auth/register/verify-phone/` |
| POST | `/api/accounts/login-password/` | LP-BE-206 | `/api/auth/login/` |
| POST | `/api/accounts/password-reset/send-code/`, `/api/accounts/password-reset/` | LP-BE-209 | `/api/auth/password-reset/`, `.../confirm/` (email link) |
| GET | `/api/accounts/profile/` | LP-BE-210 | `GET /api/profile/` |
| POST | `/api/accounts/profile/password/` | LP-BE-210 | `/api/profile/password/` |
| POST/PUT/PATCH | `/api/accounts/profile/legal-entity/` (+ `send-code/`, `verify-code/`) | LP-BE-204/205 | removed (G§8.11): company data is captured at registration |
| POST | `/api/accounts/demo-login/` | — | removed (G§8.11, review F4) |
| POST | `/api/parts/search/` | LP-BE-403 | `/api/parts/search/` (new contract) |
| GET | `/api/parts/feed/` | LP-BE-404 | `/api/parts/history/` |
| POST | `/api/parts/calculate-price/` | LP-BE-405 | `/api/parts/calculate-price/` (new contract) |
| POST | `/api/parts/quote-requests/` | LP-BE-406 | `/api/parts/quote-requests/` (new contract) |
| GET/POST | `/api/parts/public/quote-requests/<uuid>/` (+ `acknowledge/`) | LP-BE-406 | `/api/public/q/<token>/` |
| GET | `/api/cart/` | LP-BE-503 | `GET /api/cart/` |
| POST | `/api/cart/items/` | LP-BE-502 | `POST /api/cart/items/` (by reference, never by price) |
| DELETE | `/api/cart/items/<cart_item_id>/` | LP-BE-503 | `DELETE /api/cart/items/<id>/` |
| POST | `/api/orders/checkout/` | LP-BE-603 | `/api/orders/checkout/` (new contract) |
| GET | `/api/orders/` | LP-BE-802 | `GET /api/orders/?group=` |
| GET | `/api/orders/<number>/` | LP-BE-801 | `GET /api/orders/<number>/` |
| GET | `/api/orders/<number>/invoice/` | LP-BE-604 | `GET /api/orders/<number>/invoice/?lang=` |
| POST | `/api/orders/<number>/support/messages/`, `.../support/acknowledge/` | LP-BE-806 | same paths, customer-scoped |
| POST | `/api/orders/<number>/resolve-courier-fee/`, `.../decline-courier-fee/` | LP-BE-805 | `.../courier-fee/accept/`, `.../courier-fee/decline/` |
| POST | `/api/orders/items/<id>/resolve-action/`, `cancel-action/`, `acknowledge-action/` | LP-BE-803 | `/api/orders/items/<id>/decision/` |
| POST | `/api/orders/<number>/verify-payment/`, `demo-confirm-payment/` | LP-BE-704 | `/api/payments/fake/<id>/complete/` (demo only) |
| POST | `/api/orders/items/<id>/demo-request-change/`, `demo-update-status/` | LP-BE-807 | `/api/demo/orders/<n>/items/<id>/action/`, `.../status/` (demo only) |
| GET/POST | `/api/orders/public/notifications/<uuid>/` (+ `acknowledge/`) | LP-BE-904 | `/api/public/n/<token>/` |

Still working: Django admin (all operator actions, invoice print/CSV), the AMT provider and pricing
code, `GET /api/health/`, `GET /api/meta/`.
