import { expect, test, type Page } from "@playwright/test";

function makeUniqueGeorgianPhone() {
  const suffix = String(Date.now() % 1_000_000).padStart(6, "0");
  return `555${suffix}`;
}

async function openProfilePageAfterInitialLoad(page: Page) {
  await page.goto("/profile");

  const phoneInput = page.getByLabel("ტელეფონის ნომერი");

  await expect(phoneInput).toBeVisible();
  await expect(phoneInput).toBeEditable();
  await expect(
    page.getByRole("button", { name: "SMS კოდის გაგზავნა" })
  ).toBeVisible();
}

test("real backend password login keeps customer order access", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const phone = makeUniqueGeorgianPhone();
  const customerName = `Password Flow Customer ${phone}`;
  const password = "Strong1!";
  const partNumber = `PWD-${phone}`;

  await openProfilePageAfterInitialLoad(page);

  await page.getByLabel("ტელეფონის ნომერი").fill(phone);

  const sendCodeResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/accounts/send-code/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "SMS კოდის გაგზავნა" }).click();

  const sendCodeResponse = await sendCodeResponsePromise;
  expect(sendCodeResponse.status()).toBe(200);

  const sendCodeData = await sendCodeResponse.json();
  expect(sendCodeData.demo_code).toBeTruthy();

  await page.getByLabel("SMS კოდი").fill(sendCodeData.demo_code);

  const firstVerifyResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/accounts/verify-code/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "კოდის დადასტურება" }).click();

  const firstVerifyResponse = await firstVerifyResponsePromise;
  expect(firstVerifyResponse.status()).toBe(200);

  const firstVerifyData = await firstVerifyResponse.json();

  if (firstVerifyData.requires_customer_name) {
    await page.getByLabel("სახელი").fill(customerName);

    const secondVerifyResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/api/accounts/verify-code/") &&
        response.request().method() === "POST"
    );

    await page.getByRole("button", { name: "პროფილის დასრულება" }).click();

    const secondVerifyResponse = await secondVerifyResponsePromise;
    expect(secondVerifyResponse.status()).toBe(200);

    const secondVerifyData = await secondVerifyResponse.json();
    expect(secondVerifyData.is_phone_verified).toBe(true);
    expect(secondVerifyData.customer_phone).toBe(phone);
  } else {
    expect(firstVerifyData.is_phone_verified).toBe(true);
  }

  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();

  await page.getByLabel("ახალი პაროლი", { exact: true }).fill(password);
  await page
    .getByLabel("გაიმეორეთ ახალი პაროლი", { exact: true })
    .fill(password);

  const setPasswordResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/accounts/profile/password/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "პაროლის შექმნა" }).click();

  const setPasswordResponse = await setPasswordResponsePromise;
  expect(setPasswordResponse.status()).toBe(200);

  await expect(page.getByText("პაროლი შეიქმნა")).toBeVisible();

  await page.goto("/");
  await expect(page.getByText("Backend status: ok")).toBeVisible();

  await page.getByPlaceholder("მაგ: 51118070648").fill(partNumber);

  const searchResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/parts/search/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "ძებნა" }).click();

  const searchResponse = await searchResponsePromise;
  expect(searchResponse.status()).toBe(200);

  await expect(page.getByText("Quote #Q-DEMO-0001")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Demo OEM Part" })).toBeVisible();

  const addCartResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/cart/items/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "კალათაში დამატება" }).first().click();

  const addCartResponse = await addCartResponsePromise;
  expect(addCartResponse.status()).toBe(201);

  await page.goto("/cart");
  await expect(page.getByRole("heading", { name: "შენი კალათა" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Demo OEM Part" })).toBeVisible();

  await page.getByRole("link", { name: "შეკვეთის გაგრძელება" }).click();

  await expect(page.getByRole("heading", { name: "შეკვეთის გაფორმება" })).toBeVisible();
  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();

  await page.getByPlaceholder("VIN").fill("PASSWORDVIN1234567");
  await page
    .getByPlaceholder("მაგ: გთხოვთ გადაამოწმოთ თავსებადობა")
    .fill("Password persistence real backend test");

  const checkoutResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/orders/checkout/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "შეკვეთის შექმნა" }).click();

  const checkoutResponse = await checkoutResponsePromise;
  expect(checkoutResponse.status()).toBe(201);

  const orderData = await checkoutResponse.json();
  expect(orderData.order_number).toContain("LP-");
  expect(orderData.items[0].part_number).toBe(partNumber);

  await expect(page).toHaveURL(new RegExp(`/orders/${orderData.order_number}$`));
  await expect(page.getByRole("heading", { name: orderData.order_number })).toBeVisible();

  await page.goto("/profile");
  await page.getByRole("button", { name: "გასვლა" }).click();

  await expect(page.getByLabel("ტელეფონის ნომერი")).toHaveValue("");

  const passwordLoginBox = page.locator(".note-box").filter({
    hasText: "პაროლით შესვლა",
  });

  await passwordLoginBox
    .getByLabel("მობილური პაროლით შესვლისთვის")
    .fill(phone);
  await passwordLoginBox.getByLabel("პაროლი", { exact: true }).fill(password);

  const loginResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/accounts/login-password/") &&
      response.request().method() === "POST"
  );

  await passwordLoginBox
    .getByRole("button", { name: "პაროლით შესვლა" })
    .click();

  const loginResponse = await loginResponsePromise;
  expect(loginResponse.status()).toBe(200);

  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();
  await expect(page.getByText(phone)).toBeVisible();

  await page.goto("/orders");
  await expect(page.getByRole("heading", { name: "ჩემი შეკვეთები" })).toBeVisible();
  await expect(page.getByText(orderData.order_number)).toBeVisible();

  await page.goto(`/orders/${orderData.order_number}`);
  await expect(page.getByRole("heading", { name: orderData.order_number })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Demo OEM Part" })).toBeVisible();

  expect(pageErrors).toEqual([]);
});
