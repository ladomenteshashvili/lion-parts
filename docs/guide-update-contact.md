# Implementation Guide update: contact page (LPK-230, LPK-231)

`AI_IMPLEMENTATION_GUIDE.md` is not tracked in this repository's working copy, so its owner applies this text to the
master copy. Two changes.

## 1. Replace section 8.1 with

### 8.1 Meta & health

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/api/health/` | P | `{"status": "ok", "service": "lion-parts-backend"}` |
| GET | `/api/meta/` | P | below |

```json
{
  "languages": [{"code": "ka", "name": "ქართული"}, {"code": "en", "name": "English"}, {"code": "ru", "name": "Русский"}],
  "default_language": "ka",
  "currencies": [{"code": "USD", "symbol": "$"}, {"code": "GEL", "symbol": "₾"}],
  "multi_currency_enabled": true, "default_currency": "USD", "base_currency": "USD", "price_valid_business_days": 10,
  "usd_rate": {"rate": "2.5971", "date": "2026-10-24"},
  "pickup_points": [{"id": 1, "name": "ბელიაშვილის გაცემის პუნქტი", "address": "", "working_hours": ""}],
  "delivery": {"country": "GE",
               "cities": [{"code": "tbilisi", "name": "თბილისი"}],
               "districts": [{"code": "vake", "city": "tbilisi", "name": "ვაკე"},
                             {"code": "saburtalo", "city": "tbilisi", "name": "საბურთალო"}]},
  "otp_length": 6,
  "otp_resend_seconds": 60,
  "password_rules": {"min_length": 8, "upper": true, "lower": true, "digit": true, "symbol": true},
  "contact": {"phone": "+995 32 200 00 00", "email": "info@example.com", "whatsapp": "+995 555 12 34 56",
              "viber": "", "facebook": "https://www.facebook.com/example", "instagram": ""}
}
```
Only active currencies are listed. Names (pickup points, cities, districts) are in the request language; `languages[].name` is always the language's own name.
`contact` comes from Site settings (admin, "Contact page"). All six keys are always present and are plain strings, the same in every language; `""` means hidden. `phone`, `whatsapp` and `viber` are numbers in the form `+995 555 12 34 56`; the frontend builds the `tel:`, WhatsApp and Viber links. `facebook` and `instagram` are `http(s)` links. Pickup point addresses and hours stay in `pickup_points`; the invoice seller's details are not published.

## 2. Append to the section 16 "Contract changes log" table

| 2026-11-18 | `GET /api/meta/` adds `contact` {`phone`, `email`, `whatsapp`, `viber`, `facebook`, `instagram`}: plain strings, `""` when hidden, the same in every language, set in the Site settings admin "Contact page" (LPK-230, LPK-231). New migration `pricing.0003`. | No (additive) |
