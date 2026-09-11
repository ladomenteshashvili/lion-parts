import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-11T08:00:00Z";

const profile = {
  id: 1,
  session_id: "courier-fee-session",
  customer_name: "Courier Fee Customer",
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

function makeOrder(overrides = {}) {
  return {
    id: 1,
    order_number: "LP-COURIER-FEE-0001",
    session_id: "courier-fee-session",
    current_customer_name: "Courier Fee Customer",
    current_customer_phone: "555123456",
    customer_name: "Courier Fee Customer",
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
    courier_delivery_requested: true,
    courier_delivery_fee_gel: "0.00",
    proposed_courier_delivery_fee_gel: "12.00",
    courier_delivery_action_required: true,
    courier_delivery_action_message: "დაადასტურეთ კურიერის ფასი.",
    courier_delivery_confirmed_at: null,
    courier_delivery_rejected_at: null,
    payment_type: "full",
    payment: null,
    status: "action_required",
    status_label: "Action required",
    total_gel: "150.00",
    items: [],
    support_messages: [],
    support_unread_count: 0,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockOrderApi(page: Page) {
  const requests: string[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/accounts/profile/" && request.method() === "GET") {
      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/orders/LP-COURIER-FEE-0001/" &&
      request.method() === "GET"
    ) {
      await fulfillJson(route, 200, makeOrder());
      return;
    }

    if (
      pathname === "/api/orders/LP-COURIER-FEE-0001/resolve-courier-fee/" &&
      request.method() === "POST"
    ) {
      requests.push("resolve");

      await fulfillJson(
        route,
        200,
        makeOrder({
          courier_delivery_fee_gel: "12.00",
          proposed_courier_delivery_fee_gel: null,
          courier_delivery_action_required: false,
          courier_delivery_action_message: "",
          courier_delivery_confirmed_at: now,
          status: "processing",
          status_label: "Processing",
          total_gel: "162.00",
        })
      );
      return;
    }

    if (
      pathname === "/api/orders/LP-COURIER-FEE-0001/decline-courier-fee/" &&
      request.method() === "POST"
    ) {
      requests.push("decline");

      await fulfillJson(
        route,
        200,
        makeOrder({
          proposed_courier_delivery_fee_gel: null,
          courier_delivery_action_required: false,
          courier_delivery_action_message: "",
          courier_delivery_rejected_at: now,
          status: "processing",
          status_label: "Processing",
          total_gel: "150.00",
        })
      );
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });

  return requests;
}

test("customer can confirm courier fee", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const requests = await mockOrderApi(page);

  await page.goto("/orders/LP-COURIER-FEE-0001");

  await expect(
    page.getByText("კურიერის ღირებულება დასადასტურებელია")
  ).toBeVisible();

  await expect(page.getByText("12 ₾", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "დადასტურება" }).click();

  await expect(page.getByText("კურიერის დადასტურებული ღირებულება")).toBeVisible();
  await expect(page.getByText("162", { exact: false })).toBeVisible();

  expect(requests).toEqual(["resolve"]);
  expect(pageErrors).toEqual([]);
});

test("customer can decline courier fee", async ({ page }) => {
  page.on("dialog", async (dialog) => {
    await dialog.accept();
  });

  const requests = await mockOrderApi(page);

  await page.goto("/orders/LP-COURIER-FEE-0001");

  await expect(
    page.getByText("კურიერის ღირებულება დასადასტურებელია")
  ).toBeVisible();

  await page.getByRole("button", { name: "უარყოფა" }).click();

  await expect(
    page.getByText(
      "ოპერატორი დაგიკავშირდებათ მიწოდების მისამართისა და ღირებულების დასაზუსტებლად."
    )
  ).toBeVisible();

  expect(requests).toEqual(["decline"]);
});
