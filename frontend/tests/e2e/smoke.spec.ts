import { expect, test, type Page } from "@playwright/test";

const now = "2026-09-04T13:00:00Z";

const fakeProfile = {
  id: 1,
  session_id: "playwright-session",
  customer_name: "Test Customer",
  customer_phone: "555123456",
  customer_tariff_id: null,
  customer_tariff_name: null,
  markup_percent: "20.00",
  can_enter_weight: false,
  is_phone_verified: true,
  can_request_quote: false,
  created_at: now,
  updated_at: now,
};

const fakeOrder = {
  id: 1,
  order_number: "LP-TEST-0001",
  session_id: "playwright-session",
  customer_name: "Test Customer",
  customer_phone: "555123456",
  vin: "",
  note: "",
  payment_type: "full",
  payment: {
    id: 1,
    payment_reference: "PAY-TEST-0001",
    external_payment_id: "",
    provider: "manual",
    status: "pending",
    amount_gel: "120.00",
    currency: "GEL",
    paid_at: null,
    created_at: now,
    updated_at: now,
  },
  status: "payment_pending",
  status_label: "Payment pending",
  total_gel: "120.00",
  items: [
    {
      id: 1,
      cart_item_id: "quote:item:part",
      quote_id: "quote-test",
      part_option_id: "item-test",
      part_number: "TEST123",
      name: "Test Part",
      condition: "new",
      brand: "Test Brand",
      availability: "available",
      eta_days: 14,
      expected_arrival_date: "2026-09-20",
      weight_kg: "1.20",
      final_price_gel: "120.00",
      proposed_final_price_gel: null,
      currency: "GEL",
      note: "",
      customer_notice: "",
      weight_source: "api",
      quantity: 1,
      proposed_eta_days: null,
      proposed_expected_arrival_date: null,
      item_status: "created",
      action_required: false,
      action_type: "none",
      action_message: "",
      events: [],
      created_at: now,
      updated_at: now,
    },
  ],
  created_at: now,
  updated_at: now,
};

async function mockApi(page: Page, options: { verified: boolean }) {
  await page.route("**/api/cart/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: 1,
        session_id: "playwright-session",
        items: [],
        total_gel: 0,
        created_at: now,
        updated_at: now,
      }),
    });
  });

  await page.route("**/api/accounts/profile/**", async (route) => {
    if (!options.verified) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "profile not found" }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(fakeProfile),
    });
  });

  await page.route("**/api/orders/**", async (route) => {
    const url = new URL(route.request().url());

    if (url.pathname.includes(`/api/orders/${fakeOrder.order_number}/`)) {
      await route.fulfill({
        status: options.verified ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify(options.verified ? fakeOrder : { detail: "not found" }),
      });
      return;
    }

    if (url.pathname === "/api/orders/" || url.pathname.endsWith("/api/orders/")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(options.verified ? [fakeOrder] : []),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "not found" }),
    });
  });
}

test("protected routes do not render blank pages when user is not verified", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockApi(page, { verified: false });

  for (const path of ["/", "/profile", "/orders", "/orders/LP-TEST-0001"]) {
    await page.goto(path);
    await expect(page.locator(".app")).toBeVisible();
    await expect(page.locator("body")).toContainText("Lion Parts");
  }

  await expect(page.getByText("ტელეფონის დადასტურება საჭიროა")).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("verified customer can open orders list and order detail", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockApi(page, { verified: true });

  await page.goto("/orders");
  await expect(page.getByRole("heading", { name: "ჩემი შეკვეთები" })).toBeVisible();
  await expect(page.getByText("LP-TEST-0001")).toBeVisible();

  await page.goto("/orders/LP-TEST-0001");
  await expect(page.getByRole("heading", { name: "LP-TEST-0001" })).toBeVisible();
  await expect(page.getByText("Test Part")).toBeVisible();

  expect(pageErrors).toEqual([]);
});
