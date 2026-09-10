import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-10T12:00:00Z";

const cartResponse = {
  id: 1,
  session_id: "checkout-legal-session",
  items: [
    {
      cart_item_id: "legal-cart-item",
      quote_id: "legal-quote",
      part_option_id: "legal-option",
      part_number: "LEGAL123",
      name: "Legal Checkout Part",
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
      quantity: 2,
      created_at: now,
      updated_at: now,
    },
  ],
  total_gel: "300.00",
  created_at: now,
  updated_at: now,
};

function makeProfile(isMobileVerified: boolean) {
  return {
    id: 1,
    session_id: "checkout-legal-session",
    customer_name: "Checkout Customer",
    customer_phone: "555123456",
    customer_tariff_id: null,
    customer_tariff_name: null,
    markup_percent: "20.00",
    can_enter_weight: false,
    is_phone_verified: true,
    has_password: false,
    can_request_quote: false,
    legal_entity: {
      id: 7,
      company_identification_code: "405834094",
      company_official_name: "EZShop LLC",
      legal_address: "Tbilisi, Georgia",
      contact_first_name: "Lado",
      contact_last_name: "Menteshashvili",
      email: "legal@example.com",
      mobile_phone: "555123456",
      is_mobile_verified: isMobileVerified,
      mobile_verified_at: isMobileVerified ? now : null,
      is_active: true,
      created_at: now,
      updated_at: now,
    },
    created_at: now,
    updated_at: now,
  };
}

function makeOrder(useLegalEntityBilling: boolean) {
  return {
    id: 1,
    order_number: "LP-LEGAL-0001",
    session_id: "checkout-legal-session",
    current_customer_name: "Checkout Customer",
    current_customer_phone: "555123456",
    customer_name: "Checkout Customer",
    customer_phone: "555123456",
    billing_type: useLegalEntityBilling ? "legal_entity" : "personal",
    legal_entity_profile_id: useLegalEntityBilling ? 7 : null,
    legal_entity_company_identification_code: useLegalEntityBilling ? "405834094" : "",
    legal_entity_company_official_name: useLegalEntityBilling ? "EZShop LLC" : "",
    legal_entity_legal_address: useLegalEntityBilling ? "Tbilisi, Georgia" : "",
    legal_entity_contact_first_name: useLegalEntityBilling ? "Lado" : "",
    legal_entity_contact_last_name: useLegalEntityBilling ? "Menteshashvili" : "",
    legal_entity_email: useLegalEntityBilling ? "legal@example.com" : "",
    legal_entity_mobile_phone: useLegalEntityBilling ? "555123456" : "",
    vin: "",
    note: "",
    payment_type: "full",
    payment: null,
    status: "payment_pending",
    status_label: "Payment pending",
    total_gel: "300.00",
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

async function mockCheckoutApi(page: Page, isMobileVerified: boolean) {
  const checkoutRequests: unknown[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/cart/" && request.method() === "GET") {
      await fulfillJson(route, 200, cartResponse);
      return;
    }

    if (pathname === "/api/orders/" && request.method() === "GET") {
      await fulfillJson(route, 200, []);
      return;
    }

    if (pathname === "/api/accounts/profile/" && request.method() === "GET") {
      await fulfillJson(route, 200, makeProfile(isMobileVerified));
      return;
    }

    if (pathname === "/api/orders/checkout/" && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");
      checkoutRequests.push(body);

      await fulfillJson(
        route,
        201,
        makeOrder(Boolean(body.use_legal_entity_billing))
      );
      return;
    }

    if (
      pathname === "/api/orders/LP-LEGAL-0001/" &&
      request.method() === "GET"
    ) {
      await fulfillJson(route, 200, makeOrder(true));
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });

  return checkoutRequests;
}

test("verified customer can choose legal entity billing at checkout", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const checkoutRequests = await mockCheckoutApi(page, true);

  await page.goto("/checkout");

  await expect(page.getByText("ვისზე გაფორმდეს შეკვეთა")).toBeVisible();
  await expect(page.getByText("EZShop LLC · 405834094")).toBeVisible();

  await page.getByRole("radio", { name: /იურიდიულ პირზე/ }).check();

  await page.getByRole("button", { name: "შეკვეთის შექმნა" }).click();

  await expect(page).toHaveURL(/\/orders\/LP-LEGAL-0001/);

  expect(checkoutRequests).toHaveLength(1);
  expect(checkoutRequests[0]).toMatchObject({
    customer_name: "Checkout Customer",
    customer_phone: "555123456",
    use_legal_entity_billing: true,
  });

  expect(pageErrors).toEqual([]);
});

test("legal entity billing is disabled until company mobile is verified", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockCheckoutApi(page, false);

  await page.goto("/checkout");

  await expect(page.getByText("ვისზე გაფორმდეს შეკვეთა")).toBeVisible();
  await expect(
    page.getByText("კომპანიის მობილური უნდა იყოს")
  ).toBeVisible();

  await expect(
    page.getByRole("radio", { name: /იურიდიულ პირზე/ })
  ).toBeDisabled();

  expect(pageErrors).toEqual([]);
});
