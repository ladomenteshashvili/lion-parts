import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-10T12:00:00Z";

const baseProfile = {
  id: 1,
  session_id: "legal-entity-session",
  customer_name: "Legal Customer",
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

async function mockLegalEntityApi(page: Page) {
  let profile = baseProfile;
  const requests: unknown[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/cart/" && request.method() === "GET") {
      await fulfillJson(route, 200, {
        id: 1,
        session_id: "legal-entity-session",
        items: [],
        total_gel: 0,
        created_at: now,
        updated_at: now,
      });
      return;
    }

    if (pathname === "/api/orders/" && request.method() === "GET") {
      await fulfillJson(route, 200, []);
      return;
    }

    if (pathname === "/api/accounts/profile/" && request.method() === "GET") {
      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/accounts/profile/legal-entity/" &&
      request.method() === "POST"
    ) {
      const body = JSON.parse(request.postData() || "{}");
      requests.push(body);

      profile = {
        ...profile,
        legal_entity: {
          id: 1,
          company_identification_code: body.company_identification_code,
          company_official_name: body.company_official_name,
          legal_address: body.legal_address,
          contact_first_name: body.contact_first_name,
          contact_last_name: body.contact_last_name,
          email: body.email,
          mobile_phone: "555123456",
          is_mobile_verified: true,
          mobile_verified_at: now,
          is_active: true,
          created_at: now,
          updated_at: now,
        },
        updated_at: now,
      };

      await fulfillJson(route, 200, profile);
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });

  return requests;
}

test("verified customer can save legal entity profile", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const requests = await mockLegalEntityApi(page);

  await page.goto("/profile");

  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();
  await expect(page.getByText("იურიდიული პირის მონაცემები")).toBeVisible();

  await page.getByLabel("კომპანიის საიდენტიფიკაციო კოდი").fill("405834094");
  await page.getByLabel("კომპანიის ოფიციალური სახელწოდება").fill("EZShop LLC");
  await page.getByLabel("იურიდიული მისამართი").fill("Tbilisi, Georgia");
  await page.getByLabel("სახელი").fill("Lado");
  await page.getByLabel("გვარი").fill("Menteshashvili");
  await page.getByLabel("ელ.ფოსტა").fill("legal@example.com");
  await page.getByLabel("მობილური ნომერი").fill("+995555123456");

  await page
    .getByRole("button", { name: "კომპანიის მონაცემების შენახვა" })
    .click();

  await expect(page.getByText("კომპანიის მონაცემები შენახულია")).toBeVisible();
  await expect(page.getByText("EZShop LLC · 405834094")).toBeVisible();

  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    company_identification_code: "405834094",
    company_official_name: "EZShop LLC",
    legal_address: "Tbilisi, Georgia",
    contact_first_name: "Lado",
    contact_last_name: "Menteshashvili",
    email: "legal@example.com",
    mobile_phone: "+995555123456",
  });

  expect(pageErrors).toEqual([]);
});
