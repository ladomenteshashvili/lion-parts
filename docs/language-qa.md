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

## Audit of 2026-11-06 (LPK-185, Phase F)

Machine checks over both catalogs, 307 messages each: nothing missing, empty, fuzzy or in the wrong script; placeholders and final punctuation match every source text; a fresh
`makemessages` finds no new or lost source text; the catalogs compile. No technical correction was needed. Footer placeholders (`{email}`, `{url}`) are filled from `EMAIL_REPLY_TO` and
`FRONTEND_BASE_URL`. The English footer was also read in a real SMTP message; ka and ru footers are checked by test.

Main terms found, for the reviewer to confirm as consistent: invoice `ინვოისი` / `счёт`; order `შეკვეთა` / `заказ`; payment `გადახდა` / `платёж`, `оплата`; cart `კალათა` / `корзина`; support `მხარდაჭერა` / `поддержка`.
Russian uses both `платёж` and `оплата` for payment, and `счёт` also means an account in general: please check that each is natural where it appears.

**Not native-speaker sign-off (B3 stays open).** These were machine-checked only. New texts to review first:

| English | ka | ru |
|---|---|---|
| This is a service message from Lion Parts about your account, order or support request. It is not advertising. | ეს არის Lion Parts-ის სერვისული შეტყობინება თქვენი ანგარიშის, შეკვეთის ან მხარდაჭერის მოთხოვნის შესახებ. ის არ არის რეკლამა. | Это служебное сообщение от Lion Parts о вашем аккаунте, заказе или обращении в поддержку. Оно не является рекламой. |
| Support: {email}. To contact us, reply to this email. | მხარდაჭერა: {email}. დასაკავშირებლად უპასუხეთ ამ წერილს. | Поддержка: {email}. Чтобы связаться с нами, ответьте на это письмо. |
| Please do not reply to this email. To contact us, use our website: {url} | გთხოვთ, არ უპასუხოთ ამ წერილს. დასაკავშირებლად გამოიყენეთ ჩვენი ვებგვერდი: {url} | Пожалуйста, не отвечайте на это письмо. Чтобы связаться с нами, используйте наш сайт: {url} |
| The request changed while the supplier was being asked; try again. | მოთხოვნა შეიცვალა მომწოდებლის პასუხის მოლოდინში; სცადეთ თავიდან. | Запрос изменился, пока мы ждали ответ поставщика; попробуйте ещё раз. |
