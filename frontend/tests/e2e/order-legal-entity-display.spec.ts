import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-10T12:00:00Z";

const legalOrder = {
  id: 1,
  order_number: "LP-LEGAL-0001",
  session_id: "order-legal-session",
  current_customer_name: "Checkout Customer",
  current_customer_phone: "555123456",
  customer_name: "Checkout Customer",
  customer_phone: "555123456",
  billing_type: "legal_entity",
  legal_entity_profile_id: 7,
  legal_entity_company_identification_code: "405834094",
  legal_entity_company_official_name: "EZShop LLC",
  legal_entity_legal_address: "Tbilisi, Georgia",
  legal_entity_contact_first_name: "Lado",
  legal_entity_contact_last_name: "Menteshashvili",
  legal_entity_email: "legal@example.com",
  legal_entity_mobile_phone: "555123456",
  vin: "LEGALVIN123456789",
  note: "",
  payment_type: "full",
  payment: {
    id: 1,
    payment_reference: "PAY-LEGAL-0001",
    external_payment_id: "",
    provider: "demo",
    status: "pending",
    amount_gel: "300.00",
    currency: "GEL",
    paid_at: null,
    created_at: now,
    updated_at: now,
  },
  status: "payment_pending",
  status_label: "Payment pending",
  total_gel: "300.00",
  items: [
    {
      id: 11,
      cart_item_id: "legal-cart-item",
      quote_id: "legal-quote",
      part_option_id: "legal-option",
      part_number: "LEGAL123",
      proposed_part_number: "",
      proposed_name: "",
      name: "Legal Checkout Part",
      condition: "New",
      brand: "OEM",
      availability: "Available",
      eta_days: 10,
      expected_arrival_date: "2026-09-20",
      weight_kg: "2.50",
      final_price_gel: "150.00",
      proposed_final_price_gel: null,
      currency: "GEL",
      note: "",
      customer_notice: "",
      weight_source: "api",
      quantity: 2,
      proposed_eta_days: null,
      proposed_expected_arrival_date: null,
      item_status: "created",
      action_required: false,
      action_type: "",
      action_message: "",
      events: [],
      created_at: now,
      updated_at: now,
    },
  ],
  support_messages: [],
  support_unread_count: 0,
  created_at: now,
  updated_at: now,
};

const profile = {
  id: 1,
  session_id: "order-legal-session",
  customer_name: "Checkout Customer",
  customer_phone: "555123456",
  customer_tariff_id: null,
  customer_tariff_name: null,
  markup_percent: "20.00",
  can_enter_weight: false,
  is_phone_verified: true,
  has_password: false,
  can_request_quote: false,
  legal_entity: null,
  created_at: now,
  updated_at: now,
};

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockOrderApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/cart/" && request.method() === "GET") {
      await fulfillJson(route, 200, {
        id: 1,
        session_id: "order-legal-session",
        items: [],
        total_gel: 0,
        created_at: now,
        updated_at: now,
      });
      return;
    }

    if (pathname === "/api/accounts/profile/" && request.method() === "GET") {
      await fulfillJson(route, 200, profile);
      return;
    }

    if (pathname === "/api/orders/" && request.method() === "GET") {
      await fulfillJson(route, 200, [legalOrder]);
      return;
    }

    if (
      pathname === "/api/orders/LP-LEGAL-0001/" &&
      request.method() === "GET"
    ) {
      await fulfillJson(route, 200, legalOrder);
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });
}

test("orders list shows legal entity billing summary", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockOrderApi(page);

  await page.goto("/orders");

  await expect(page.getByText("LP-LEGAL-0001")).toBeVisible();
  await expect(page.getByText("გაფორმებულია იურიდიულ პირზე")).toBeVisible();
  await expect(page.getByText("EZShop LLC")).toBeVisible();
  await expect(page.getByText("405834094")).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test("order detail shows legal entity billing details", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockOrderApi(page);

  await page.goto("/orders/LP-LEGAL-0001");

  await expect(
    page.getByText("შეკვეთა გაფორმებულია იურიდიულ პირზე")
  ).toBeVisible();
  await expect(page.getByText("EZShop LLC · 405834094")).toBeVisible();
  await expect(page.getByText("იურიდიული მისამართი: Tbilisi, Georgia")).toBeVisible();
  await expect(page.getByText("საკონტაქტო პირი: Lado Menteshashvili")).toBeVisible();
  await expect(page.getByText("legal@example.com · 555123456")).toBeVisible();

  expect(pageErrors).toEqual([]);
});
