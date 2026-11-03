# Language QA (LP-BE-1306)

Languages: `ka` (default), `en`, `ru`. msgids are English; catalogs in `backend/locale/<lang>/LC_MESSAGES/django.po`.

## Checked automatically (in the test suite)

| Check | Test |
|---|---|
| Every registered text (errors, DRF messages, system events, notification titles) translated in ka and ru; no fuzzy or empty entries; no Georgian literals in Python | `api/tests/test_i18n.py` |
| Every ka/ru translation keeps exactly the placeholders of its msgid (`%(name)s`, `{amount}`, `%d`); plural forms never invent one | `api/tests/test_language_qa.py::PlaceholderTests` |
| Plural texts have all forms (ka 2, ru 3); Russian 1 / 2 / 5 forms render correctly | `api/tests/test_language_qa.py`, `orders/test_services.py::AttentionTests` |
| Language selection: `en`, `en-US`, `EN`, `ru-RU,ru;q=0.9`, `de,ru;q=0.5` → ru, `de` / `*` / empty / missing → ka; `Content-Language`; error `detail` and field `message` follow it | `api/tests/test_language_qa.py::LanguageSelectionTests` |
| System texts and notification titles differ in each language | `api/tests/test_language_qa.py::SeededContentTests` |
| Seeded cities, districts and pickup point names exist in all three languages | `api/tests/test_language_qa.py::SeededContentTests` |
| Customer language for later messages (`preferred_language`), Georgian fallback when a template language is missing | `orders/test_notify.py`, `orders/test_notification_templates.py` |
| Emails: subject/body in the customer's language, `<html lang>` follows it | `api/tests/test_mail.py`, `api/tests/test_log_secrets.py::EmailLanguageTests` |
| Invoice in `?lang=` | `orders/test_invoice.py` |
| Admin follows `Accept-Language`, Georgian by default | `api/tests/test_language_qa.py::AdminLanguageTests` |

## Fixed in this pass

- **Courier fee wording (customer-facing):** the banner and title said "delivery fee" (`მიტანის ღირებულება` /
  `стоимость доставки`) but the order's system messages said `კურიერის საფასური` / `стоимость курьера`. The
  customer-facing messages now use the banner's wording (regression test `TerminologyTests`); the admin keeps
  "courier fee", with ru `стоимость курьерской доставки`.
- **ru plural agreement** in the DRF `max_digits` message (`не более {max_digits} цифры` was wrong for most
  numbers): `Число не должно содержать больше цифр, чем {max_digits}.`
- **Email `<html lang>`** was always `ka` (M6 security review finding).

## For the native-speaker review (acceptance: ka and ru signed off)

1. `cart_invalid` (ka): `Checkout დაბლოკილია: …` starts with the English word "Checkout" (it is the Guide's
   reference text). Suggested: `შეკვეთის გაფორმება დაბლოკილია: …`.
2. Review every entry of both `.po` files, prioritizing customer-facing texts: error messages (`api/errors.py`),
   order banners, timeline and event titles (`orders/texts.py`), notification titles and the SMS/email templates
   (admin → Notification channel settings), the invoice (`orders/templates/orders/invoice.html`).
3. Check money and status vocabulary is used consistently: payment `გადახდა`/`оплата`, invoice `ინვოისი`/`счёт`,
   refund `დაბრუნება`/`возврат`, balance due `დარჩენილი თანხა`/`остаток к оплате`, delivery fee
   `მიტანის ღირებულება`/`стоимость доставки`.

## Launch content still missing (operator, in the admin)

- Pickup point **address and working hours** (only the name is seeded) in ka, en and ru.
- Tariff **benefits text** (not seeded) in ka, en and ru.
- Notification channels are all `none`; enable them per event when SMS/SMTP are live.

## Adding a language

`backend/README.md` → "Adding a language (5 steps)".
