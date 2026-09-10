import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-10T12:00:00Z";

const profile = {
  id: 1,
  session_id: "checkout-courier-session",
  customer_name: "Courier Customer",
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

const cartResponse = {
  id: 1,
  session_id: "checkout-courier-session",
  items: [
    {
      id: 1,
      cart_item_id: "courier-cart-item",
      quote_id: "courier-quote",
      part_option_id: "courier-option",
      part_number: "COURIER123",
      name: "Courier Test Part",
      condition: "New",
      brand: "OEM",
      availability: "Available",
      eta_days: 10,
      weight_kg: "2.50",
      final_price_gel: "150.00",
      currency: "GEL",
      note: "",
      customer_notice: "",
      weight_source: "api",
      quantity: 1,
      created_at: now,
      updated_at: now,
    },
  ],
  total_gel: "150.00",
  created_at: now,
  updated_at: now,
};

function makeOrder(courierDeliveryRequested: boolean) {
  return {
    id: 1,
    order_number: "LP-COURIER-0001",
    session_id: "checkout-courier-session",
    customer_name: "Courier Customer",
    customer_phone: "555123456",
    billing_type: "personal",
    legal_entity_profile_id: null,
    legal_entity_company_identification_code: "",
    legal_entity_company_official_name: "",
    legal_entity_legal_address: "",
    legal_entity_contact_first_name: "",
    legal_entity_contact_last_name: "",
    legal_entity_email: "",
    legal_entity_mobile_phone: "",
    vin: "",
    note: "",
    courier_delivery_requested: courierDeliveryRequested,
    payment_type: "full",
    payment: null,
    status: "payment_pending",
    status_label: "Payment pending",
    total_gel: "150.00",
    items: [],
    support_messages: [],
    support_unread_count: 0,
    created_at: now,
    updated_at: now,
  };
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockCheckoutApi(page: Page) {
  const checkoutRequests: unknown[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/cart/" && request.method() === "GET") {
      await fulfillJson(route, 200, cartResponse);
      return;
    }

    if (pathname === "/api/accounts/profile/" && request.method() === "GET") {
      await fulfillJson(route, 200, profile);
      return;
    }

    if (pathname === "/api/orders/checkout/" && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");
      checkoutRequests.push(body);

      await fulfillJson(
        route,
        201,
        makeOrder(Boolean(body.courier_delivery_requested))
      );
      return;
    }

    if (
      pathname === "/api/orders/LP-COURIER-0001/" &&
      request.method() === "GET"
    ) {
      await fulfillJson(route, 200, makeOrder(true));
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });

  return checkoutRequests;
}

test("checkout can request courier delivery", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const checkoutRequests = await mockCheckoutApi(page);

  await page.goto("/checkout");

  await page.getByRole("checkbox", { name: "მინდა კურიერით მიწოდება" }).check();

  await expect(
    page.getByText("კურიერის ღირებულება ამ ეტაპზე არ ემატება ჯამს")
  ).toBeVisible();

  await page.getByRole("button", { name: "შეკვეთის შექმნა" }).click();

  await expect(page).toHaveURL(/\/orders\/LP-COURIER-0001/);

  expect(checkoutRequests).toHaveLength(1);
  expect(checkoutRequests[0]).toMatchObject({
    courier_delivery_requested: true,
  });

  expect(pageErrors).toEqual([]);
});
