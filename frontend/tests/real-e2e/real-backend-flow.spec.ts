import { expect, test } from "@playwright/test";

function makeUniqueGeorgianPhone() {
  const suffix = String(Date.now() % 1_000_000).padStart(6, "0");
  return `555${suffix}`;
}

test("real backend customer flow: verify phone, search, cart, checkout, order detail", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const phone = makeUniqueGeorgianPhone();
  const customerName = `Integration Customer ${phone}`;
  const partNumber = `FLOW-${phone}`;

  await page.goto("/profile");

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

  await expect(page.getByLabel("SMS კოდი")).toBeVisible();
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
    await expect(page.getByLabel("სახელი")).toBeVisible();
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

  await expect(page.getByText("ტელეფონის ნომერი დადასტურებულია")).toBeVisible();

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
  await expect(page.getByText("Demo OEM Part")).toBeVisible();

  const addCartResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/cart/items/") &&
      response.request().method() === "POST"
  );

  await page.getByRole("button", { name: "კალათაში დამატება" }).first().click();

  const addCartResponse = await addCartResponsePromise;
  expect(addCartResponse.status()).toBe(201);

  await expect(page.getByText("ნაწილი დაემატა კალათაში")).toBeVisible();

  await page.goto("/cart");
  await expect(page.getByRole("heading", { name: "შენი კალათა" })).toBeVisible();
  await expect(page.getByText("Demo OEM Part")).toBeVisible();

  await page.getByRole("link", { name: "შეკვეთის გაგრძელება" }).click();

  await expect(page.getByRole("heading", { name: "შეკვეთის გაფორმება" })).toBeVisible();
  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();

  await page.getByPlaceholder("VIN").fill("TESTVIN1234567890");
  await page
    .getByPlaceholder("მაგ: გთხოვთ გადაამოწმოთ თავსებადობა")
    .fill("Real backend Playwright integration test");

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
  expect(orderData.payment.status).toBe("pending");
  expect(orderData.items[0].part_number).toBe(partNumber);

  await expect(page).toHaveURL(new RegExp(`/orders/${orderData.order_number}$`));
  await expect(page.getByRole("heading", { name: orderData.order_number })).toBeVisible();
  await expect(page.getByText("Demo OEM Part")).toBeVisible();

  await page.goto("/orders");
  await expect(page.getByRole("heading", { name: "ჩემი შეკვეთები" })).toBeVisible();
  await expect(page.getByText(orderData.order_number)).toBeVisible();

  expect(pageErrors).toEqual([]);
});
