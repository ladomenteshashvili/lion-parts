"""The API contract (G§8.0 schemas, G§8 endpoint bodies) as exact key sets (LP-BE-1203). A contract change is a
one-line diff here, plus an entry in the Guide's change log (G§16)."""

PAGINATED = {"count", "next", "previous", "results"}
ERROR = {"code", "detail"}
FIELD_ERROR = {"code", "message"}

TARIFF = {"name", "payment_terms", "split_upfront_percent", "can_enter_weight", "can_request_quote", "benefits_text"}
COMPANY = {"official_name", "identification_code", "legal_address"}
CUSTOMER = {"id", "account_type", "first_name", "last_name", "personal_id", "company", "phone", "is_phone_verified",
            "email", "is_email_verified", "is_fully_verified", "tariff", "marketing_opt_in", "preferred_language",
            "created_at"}
BADGES = {"search_new", "orders_action", "orders_to_pay", "notifications_unread"}
ME = {"authenticated", "customer", "cart_count", "badges"}

PICKUP_POINT = {"id", "name", "address", "working_hours"}
DELIVERY_AREAS = {"country", "cities", "districts"}
CITY = {"code", "name"}
DISTRICT = {"code", "city", "name"}
META = {"languages", "default_language", "currencies", "default_currency", "base_currency", "multi_currency_enabled",
        "usd_rate", "price_valid_business_days", "pickup_points", "delivery", "otp_length", "otp_resend_seconds",
        "password_rules"}

OFFER = {"offer_id", "name", "brand", "condition", "status", "list_unit_price", "discount_unit_amount", "unit_price",
         "currency", "eta_min_days", "eta_max_days", "weight_kg", "weight_source", "requires_weight",
         "in_cart_quantity", "image_urls", "customer_notice", "note"}
WEIGHT_PRICED = {"search_id", "price_valid_until", "offer"}
SEARCH = {"search_id", "part_number", "vin", "currency", "exchange_rate", "price_valid_until", "not_found", "offers"}
HISTORY_ITEM = {"id", "kind", "search_id", "quote_request_id", "part_number", "vin", "name", "brand", "status",
                "unit_price", "previous_unit_price", "currency", "offers_count", "eta_min_days", "eta_max_days",
                "weight_kg", "weight_source", "price_valid_until", "is_new", "purchased_order_number",
                "operator_message", "created_at"}
QUOTE_REQUEST = {"id", "request_type", "part_number", "vin", "name", "brand", "status", "is_price_ready", "unit_price",
                 "currency", "price_valid_until", "weight_kg", "operator_message", "created_at"}
PUBLIC_QUOTE = {"quote_request_id", "part_number", "name", "brand", "unit_price", "currency", "price_valid_until",
                "eta_min_days", "eta_max_days", "weight_kg", "operator_message", "requires_login"}

CART_ITEM = {"id", "source", "part_number", "name", "brand", "condition", "quantity", "vin", "list_unit_price",
             "discount_unit_amount", "unit_price", "previous_unit_price", "line_total", "status", "eta_min_days",
             "eta_max_days", "weight_kg", "weight_source", "customer_notice", "price_valid_until", "image_urls"}
CART_SUMMARY = {"items_count", "items_total", "discount_total", "delivery_fee", "total"}
CART = {"currency", "items", "summary", "payment_terms", "checkout_blocked", "checkout_blocked_reasons", "merge"}
CHECKOUT_OPTIONS = {"currency", "summary", "payment_terms", "payment_options", "payment_methods", "payer",
                    "pickup_points", "delivery", "checkout_blocked", "checkout_blocked_reasons"}
PAYMENT_OPTION = {"value", "due_now", "balance", "default"}
PAYER = {"account_type", "name", "company_name", "email", "phone"}

ITEM_EVENT = {"id", "event_type", "title", "message", "created_at"}
ITEM_ACTION = {"type", "message", "old", "new", "decisions"}
ORDER_ITEM = {"id", "part_number", "name", "brand", "condition", "quantity", "vin", "list_unit_price",
              "discount_unit_amount", "unit_price", "line_total", "weight_kg", "weight_source", "item_status",
              "eta_min_days", "eta_max_days", "expected_arrival_from", "expected_arrival_to", "action", "events",
              "image_urls"}
PAYMENT = {"id", "kind", "provider", "status", "amount", "currency", "checkout_url", "paid_at", "created_at"}
COURIER = {"fee", "proposed_fee", "action_required", "message", "confirmed_at", "declined_at"}
ORDER_DELIVERY = {"city", "city_name", "district", "district_name", "address", "comment"}
ORDER = {"order_number", "status", "payment_status", "currency", "created_at", "items_count", "delivery_method",
         "pickup_point", "delivery", "courier", "payment_method", "payment_option", "items_total", "discount_total",
         "courier_fee", "total", "amount_due_now", "amount_paid", "balance_due", "expected_arrival_from",
         "expected_arrival_to", "progress", "attention", "can_cancel", "items"}
ORDER_DETAIL = ORDER | {"timeline", "payments", "support_messages", "support_unread_count", "invoice_url"}
SUPPORT_MESSAGE = {"id", "item_id", "item_part_number", "sender_type", "message", "is_read_by_customer", "created_at"}
GROUP_COUNTS = {"all", "action", "to_pay", "to_pickup", "active", "completed", "cancelled"}
CHECKOUT = {"order", "payment"}

NOTIFICATION = {"id", "event_type", "title", "message", "target", "blocking", "read_at", "created_at"}
PUBLIC_NOTIFICATION = {"event_type", "title", "message", "order_number", "item_part_number", "target",
                       "requires_login"}
TICKET_CREATED = {"id", "context", "status", "created_at"}
TICKET = {"id", "context", "part_number", "vin", "message", "status", "operator_reply", "replied_at", "created_at"}

CSRF = {"csrf_token"}
OTP_SENT = {"masked_phone", "expires_in", "resend_in"}  # + "demo_code" only with SMS_DEMO_MODE (never in production)
REGISTERED = {"csrf_token", "customer", "email_verification_sent"}
LOGGED_IN = {"csrf_token", "customer"}
EMAIL_SENT = {"sent_to", "resend_in"}
HEALTH = {"status", "service"}
