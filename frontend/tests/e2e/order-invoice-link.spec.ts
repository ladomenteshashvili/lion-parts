import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-11T10:00:00Z";

const profile = {
  id: 1,
  session_id: "invoice-session",
  customer_name: "Invoice Customer",
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

const order = {
  id: 1,
  order_number: "LP-INVOICE-LINK-0001",
  session_id: "invoice-session",
  customer_name: "Invoice Customer",
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
  vin: "TESTVIN123",
  note: "",
  courier_delivery_requested: false,
  courier_delivery_fee_gel: "0.00",
  proposed_courier_delivery_fee_gel: null,
  courier_delivery_action_required: false,
  courier_delivery_action_message: "",
  courier_delivery_confirmed_at: null,
  courier_delivery_rejected_at: null,
  payment_type: "full",
  payment: null,
  status: "processing",
  status_label: "Processing",
  total_gel: "150.00",
  items: [],
  support_messages: [],
  support_unread_count: 0,
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
  await page.addInitScript(() => {
    window.localStorage.setItem("lion_parts_session_id", "invoice-session");
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/accounts/profile/" && request.method() === "GET") {
      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/orders/LP-INVOICE-LINK-0001/" &&
      request.method() === "GET"
    ) {
      await fulfillJson(route, 200, order);
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });
}

test("customer sees invoice print link on order detail", async ({ page }) => {
  await mockOrderApi(page);

  await page.goto("/orders/LP-INVOICE-LINK-0001");

  const invoiceLink = page.getByRole("link", {
    name: "ინვოისის ნახვა / PDF",
  });

  await expect(invoiceLink).toBeVisible();
  await expect(invoiceLink).toHaveAttribute(
    "href",
    /\/api\/orders\/LP-INVOICE-LINK-0001\/invoice\/\?session_id=invoice-session$/
  );
  await expect(invoiceLink).toHaveAttribute("target", "_blank");
});
