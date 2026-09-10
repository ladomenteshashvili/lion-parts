import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-09-10T10:00:00Z";

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
  created_at: string;
  updated_at: string;
};

function buildProfile(hasPassword: boolean): MockProfile {
  return {
    id: 1,
    session_id: "playwright-password-session",
    customer_name: "Password Customer",
    customer_phone: "555123456",
    customer_tariff_id: null,
    customer_tariff_name: null,
    markup_percent: "20.00",
    can_enter_weight: false,
    is_phone_verified: true,
    has_password: hasPassword,
    can_request_quote: false,
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

async function mockPasswordProfileApi(
  page: Page,
  options: { initialProfile: MockProfile | null }
) {
  let profile = options.initialProfile;
  const requests: Record<string, unknown[]> = {
    setPassword: [],
    loginPassword: [],
    sendResetCode: [],
    resetPassword: [],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/cart/" && request.method() === "GET") {
      await fulfillJson(route, 200, {
        id: 1,
        session_id: "playwright-password-session",
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
      if (!profile) {
        await fulfillJson(route, 404, { detail: "profile not found" });
        return;
      }

      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/accounts/profile/password/" &&
      request.method() === "POST"
    ) {
      const body = JSON.parse(request.postData() || "{}");
      requests.setPassword.push(body);

      if (!profile) {
        await fulfillJson(route, 403, { detail: "phone verification required" });
        return;
      }

      if (!body.new_password) {
        await fulfillJson(route, 400, { detail: "new_password is required" });
        return;
      }

      profile = {
        ...profile,
        has_password: true,
        updated_at: now,
      };

      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/accounts/login-password/" &&
      request.method() === "POST"
    ) {
      const body = JSON.parse(request.postData() || "{}");
      requests.loginPassword.push(body);

      if (
        body.customer_phone !== "555123456" ||
        body.password !== "Strong1!"
      ) {
        await fulfillJson(route, 400, { detail: "invalid phone or password" });
        return;
      }

      profile = buildProfile(true);
      await fulfillJson(route, 200, profile);
      return;
    }

    if (
      pathname === "/api/accounts/password-reset/send-code/" &&
      request.method() === "POST"
    ) {
      const body = JSON.parse(request.postData() || "{}");
      requests.sendResetCode.push(body);

      await fulfillJson(route, 200, {
        detail: "password reset code sent",
        phone: "555123456",
        expires_in_seconds: 300,
        demo_code: "654321",
      });
      return;
    }

    if (
      pathname === "/api/accounts/password-reset/" &&
      request.method() === "POST"
    ) {
      const body = JSON.parse(request.postData() || "{}");
      requests.resetPassword.push(body);

      if (body.code !== "654321") {
        await fulfillJson(route, 400, { detail: "invalid verification code" });
        return;
      }

      profile = buildProfile(true);
      await fulfillJson(route, 200, profile);
      return;
    }

    await fulfillJson(route, 404, { detail: "not found" });
  });

  return requests;
}

test("verified customer can create profile password", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const requests = await mockPasswordProfileApi(page, {
    initialProfile: buildProfile(false),
  });

  await page.goto("/profile");

  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();
  await expect(page.getByText("Password Customer · 555123456")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "პაროლის შექმნა" })
  ).toBeVisible();

  await page.getByLabel("ახალი პაროლი", { exact: true }).fill("Strong1!");
  await page.getByLabel("გაიმეორეთ ახალი პაროლი", { exact: true }).fill("Strong1!");
  await page.getByRole("button", { name: "პაროლის შექმნა" }).click();

  await expect(page.getByText("პაროლი შეიქმნა")).toBeVisible();
  await expect(page.getByLabel("მიმდინარე პაროლი")).toBeVisible();

  expect(requests.setPassword).toHaveLength(1);
  expect(requests.setPassword[0]).toMatchObject({
    new_password: "Strong1!",
  });
  expect(pageErrors).toEqual([]);
});

test("customer can login with password without SMS code", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const requests = await mockPasswordProfileApi(page, {
    initialProfile: null,
  });

  await page.goto("/profile");

  await expect(
    page.getByRole("button", { name: "პაროლით შესვლა" })
  ).toBeVisible();

  const passwordLoginBox = page.locator(".note-box").filter({
    hasText: "პაროლით შესვლა",
  });

  await passwordLoginBox
    .getByLabel("მობილური პაროლით შესვლისთვის")
    .fill("555123456");
  await passwordLoginBox.getByLabel("პაროლი", { exact: true }).fill("Strong1!");
  await passwordLoginBox
    .getByRole("button", { name: "პაროლით შესვლა" })
    .click();

  await expect(page.getByText("პაროლით შესვლა წარმატებულია")).toBeVisible();
  await expect(page.getByText("Password Customer · 555123456")).toBeVisible();

  expect(requests.loginPassword).toHaveLength(1);
  expect(requests.loginPassword[0]).toMatchObject({
    customer_phone: "555123456",
    password: "Strong1!",
  });
  expect(pageErrors).toEqual([]);
});

test("customer can reset forgotten password and becomes authenticated", async ({
  page,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const requests = await mockPasswordProfileApi(page, {
    initialProfile: null,
  });

  await page.goto("/profile");

  const passwordLoginBox = page.locator(".note-box").filter({
    hasText: "პაროლით შესვლა",
  });

  await passwordLoginBox
    .getByRole("button", { name: "პაროლი დამავიწყდა" })
    .click();

  await passwordLoginBox
    .getByLabel("მობილური პაროლის აღდგენისთვის")
    .fill("555123456");
  await passwordLoginBox
    .getByRole("button", { name: "აღდგენის კოდის გაგზავნა" })
    .click();

  await expect(page.getByText("პაროლის აღდგენის SMS კოდი გაგზავნილია")).toBeVisible();
  await expect(page.getByText("სატესტო კოდი:")).toBeVisible();

  await passwordLoginBox.getByLabel("SMS კოდი").fill("654321");
  await passwordLoginBox
    .getByLabel("ახალი პაროლი", { exact: true })
    .fill("Newstrong1!");
  await passwordLoginBox
    .getByLabel("გაიმეორეთ ახალი პაროლი", { exact: true })
    .fill("Newstrong1!");
  await passwordLoginBox
    .getByRole("button", { name: "პაროლის აღდგენა" })
    .click();

  await expect(
    page.getByText("პაროლი აღდგენილია და შესვლა შესრულდა")
  ).toBeVisible();
  await expect(page.getByText("Password Customer · 555123456")).toBeVisible();

  expect(requests.sendResetCode).toHaveLength(1);
  expect(requests.resetPassword).toHaveLength(1);
  expect(requests.resetPassword[0]).toMatchObject({
    customer_phone: "555123456",
    code: "654321",
    new_password: "Newstrong1!",
  });
  expect(pageErrors).toEqual([]);
});
