import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-10T12:00:00Z";

type MockLegalEntity = {
  id: number;
  company_identification_code: string;
  company_official_name: string;
  legal_address: string;
  contact_first_name: string;
  contact_last_name: string;
  email: string;
  mobile_phone: string;
  is_mobile_verified: boolean;
  mobile_verified_at: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

type MockProfile = {
  id: number;
  session_id: string;
  customer_name: string;
  customer_phone: string;
  customer_tariff_id: number | null;
  customer_tariff_name: string | null;
  markup_percent: string;
  can_enter_weight: boolean;
  is_phone_verified: boolean;
  has_password: boolean;
  can_request_quote: boolean;
  legal_entity: MockLegalEntity | null;
  created_at: string;
  updated_at: string;
};

const baseProfile: MockProfile = {
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

function normalizeMockPhone(value: string) {
  const digits = value.replace(/\D/g, "");
  return digits.startsWith("995") ? digits.slice(3) : digits;
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockLegalEntityApi(page: Page) {
  let profile: MockProfile = { ...baseProfile };
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

      const mobilePhone = normalizeMockPhone(String(body.mobile_phone || ""));
      const isMobileVerified = mobilePhone === profile.customer_phone;

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
          mobile_phone: mobilePhone,
          is_mobile_verified: isMobileVerified,
          mobile_verified_at: isMobileVerified ? now : null,
          is_active: true,
          created_at: now,
          updated_at: now,
        },
        updated_at: now,
      };

      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/accounts/profile/legal-entity/send-code/" &&
      request.method() === "POST"
    ) {
      await fulfillJson(route, 200, {
        detail: "verification code sent",
        phone: profile.legal_entity?.mobile_phone || "599777777",
        expires_in_seconds: 300,
        demo_code: "123456",
      });
      return;
    }

    if (
      pathname === "/api/accounts/profile/legal-entity/verify-code/" &&
      request.method() === "POST"
    ) {
      const body = JSON.parse(request.postData() || "{}");

      if (body.code !== "123456") {
        await fulfillJson(route, 400, {
          detail: "invalid verification code",
        });
        return;
      }

      if (profile.legal_entity) {
        profile = {
          ...profile,
          legal_entity: {
            ...profile.legal_entity,
            is_mobile_verified: true,
            mobile_verified_at: now,
            updated_at: now,
          },
          updated_at: now,
        };
      }

      await fulfillJson(route, 200, profile);
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });

  return requests;
}

async function fillLegalEntityForm(page: Page, mobilePhone: string) {
  await page.getByLabel("კომპანიის საიდენტიფიკაციო კოდი").fill("405834094");
  await page.getByLabel("კომპანიის ოფიციალური სახელწოდება").fill("EZShop LLC");
  await page.getByLabel("იურიდიული მისამართი").fill("Tbilisi, Georgia");
  await page.getByLabel("სახელი").fill("Lado");
  await page.getByLabel("გვარი").fill("Menteshashvili");
  await page.getByLabel("ელ.ფოსტა").fill("legal@example.com");
  await page.getByLabel("მობილური ნომერი").fill(mobilePhone);
}

test("verified customer can save legal entity profile", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const requests = await mockLegalEntityApi(page);

  await page.goto("/profile");

  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();
  await expect(page.getByText("იურიდიული პირის მონაცემები")).toBeVisible();

  await fillLegalEntityForm(page, "+995555123456");

  await page
    .getByRole("button", { name: "კომპანიის მონაცემების შენახვა" })
    .click();

  await expect(page.getByText("EZShop LLC · 405834094")).toBeVisible();
  await expect(page.getByText("მობილურის სტატუსი: დადასტურებულია")).toBeVisible();

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

test("verified customer can verify different legal entity mobile phone", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await mockLegalEntityApi(page);

  await page.goto("/profile");

  await fillLegalEntityForm(page, "+995599777777");

  await page
    .getByRole("button", { name: "კომპანიის მონაცემების შენახვა" })
    .click();

  await expect(page.getByText("მობილურის სტატუსი: დასადასტურებელია")).toBeVisible();
  await expect(page.getByText("კომპანიის მობილურის დადასტურება")).toBeVisible();

  const legalSection = page.locator(".note-box").filter({
    hasText: "იურიდიული პირის მონაცემები",
  });

  await legalSection.getByRole("button", { name: "კოდის გაგზავნა" }).click();

  await expect(page.getByText("სატესტო კოდი: 123456")).toBeVisible();

  await page.getByLabel("დადასტურების კოდი").fill("123456");
  await legalSection
    .getByRole("button", { name: "მობილურის დადასტურება" })
    .click();

  await expect(page.getByText("კომპანიის მობილური დადასტურებულია")).toBeVisible();
  await expect(page.getByText("მობილურის სტატუსი: დადასტურებულია")).toBeVisible();

  expect(pageErrors).toEqual([]);
});
