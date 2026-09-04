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

test("customer can verify phone, search part, add to cart, checkout, and open order detail", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  let profile: typeof fakeProfile | null = null;
  let cartItems: Array<Record<string, unknown>> = [];
  let createdOrder: typeof fakeOrder | null = null;

  const flowPart = {
    part_option_id: "flow-option-1",
    name: "Flow Test Part",
    condition: "new",
    brand: "Flow Brand",
    availability: "available",
    eta_days: 14,
    final_price_gel: 250,
    currency: "GEL",
    requires_weight_input: false,
    weight_kg: 2.5,
    note: "Flow test offer",
    weight_source: "api",
    customer_notice: "",
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/health/") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          service: "lion-parts-api",
        }),
      });
      return;
    }

    if (pathname === "/api/accounts/profile/") {
      if (!profile) {
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
        body: JSON.stringify(profile),
      });
      return;
    }

    if (pathname === "/api/accounts/send-code/") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "verification code sent",
          phone: "555123456",
          expires_in_seconds: 300,
          demo_code: "123456",
        }),
      });
      return;
    }

    if (pathname === "/api/accounts/verify-code/") {
      const body = JSON.parse(request.postData() || "{}");

      if (body.code !== "123456") {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({ detail: "invalid verification code" }),
        });
        return;
      }

      profile = {
        ...fakeProfile,
        customer_name: "Flow Customer",
        customer_phone: "555123456",
        is_phone_verified: true,
      };

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(profile),
      });
      return;
    }

    if (pathname === "/api/parts/search/") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          quote_id: "FLOW-QUOTE-1",
          part_number: "FLOW123",
          vin: null,
          results: [flowPart],
        }),
      });
      return;
    }

    if (pathname === "/api/cart/" && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          session_id: "playwright-session",
          items: cartItems,
          total_gel: cartItems.reduce(
            (sum, item) =>
              sum +
              Number(item.final_price_gel || 0) * Number(item.quantity || 1),
            0
          ),
          created_at: now,
          updated_at: now,
        }),
      });
      return;
    }

    if (pathname === "/api/cart/items/" && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");

      cartItems = [
        {
          id: 1,
          cart_item_id: body.cart_item_id,
          quote_id: body.quote_id,
          part_option_id: body.part_option_id,
          part_number: body.part_number,
          name: body.name,
          condition: body.condition,
          brand: body.brand,
          availability: body.availability,
          eta_days: body.eta_days,
          weight_kg: body.weight_kg,
          final_price_gel: String(body.final_price_gel),
          currency: body.currency,
          note: body.note || "",
          customer_notice: body.customer_notice || "",
          weight_source: body.weight_source || "api",
          quantity: body.quantity || 1,
          created_at: now,
          updated_at: now,
        },
      ];

      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          session_id: "playwright-session",
          items: cartItems,
          total_gel: 250,
          created_at: now,
          updated_at: now,
        }),
      });
      return;
    }

    if (pathname === "/api/orders/checkout/" && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");

      createdOrder = {
        ...fakeOrder,
        order_number: "LP-FLOW-0001",
        customer_name: body.customer_name,
        customer_phone: body.customer_phone,
        vin: body.vin || "",
        note: body.note || "",
        total_gel: "250.00",
        items: [
          {
            ...fakeOrder.items[0],
            name: "Flow Test Part",
            part_number: "FLOW123",
            quote_id: "FLOW-QUOTE-1",
            final_price_gel: "250.00",
            weight_kg: "2.50",
          },
        ],
      };

      cartItems = [];

      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(createdOrder),
      });
      return;
    }

    if (pathname === "/api/orders/" && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(createdOrder ? [createdOrder] : []),
      });
      return;
    }

    if (pathname === "/api/orders/LP-FLOW-0001/" && request.method() === "GET") {
      await route.fulfill({
        status: createdOrder ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify(createdOrder || { detail: "not found" }),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "not found" }),
    });
  });

  await page.goto("/profile");
  await page.getByLabel("ტელეფონის ნომერი").fill("555123456");
  await page.getByRole("button", { name: "SMS კოდის გაგზავნა" }).click();
  await expect(page.getByLabel("SMS კოდი")).toBeVisible();
  await page.getByLabel("SMS კოდი").fill("123456");
  await page.getByRole("button", { name: "კოდის დადასტურება" }).click();
  await expect(page.getByText("ტელეფონის ნომერი დადასტურებულია")).toBeVisible();

  await page.goto("/");
  await page.getByPlaceholder("მაგ: 51118070648").fill("FLOW123");
  await page.getByRole("button", { name: "ძებნა" }).click();
  await expect(page.getByText("Quote #FLOW-QUOTE-1")).toBeVisible();
  await expect(page.getByText("Flow Test Part")).toBeVisible();
  await page.getByRole("button", { name: "კალათაში დამატება" }).click();
  await expect(page.getByText("ნაწილი დაემატა კალათაში")).toBeVisible();

  await page.goto("/cart");
  await expect(page.getByRole("heading", { name: "შენი კალათა" })).toBeVisible();
  await expect(page.getByText("Flow Test Part")).toBeVisible();
  await page.getByRole("link", { name: "შეკვეთის გაგრძელება" }).click();

  await expect(page.getByRole("heading", { name: "შეკვეთის გაფორმება" })).toBeVisible();
  await expect(page.getByText("ტელეფონი დადასტურებულია")).toBeVisible();
  await page.getByPlaceholder("VIN").fill("TESTVIN1234567890");
  await page
    .getByPlaceholder("მაგ: გთხოვთ გადაამოწმოთ თავსებადობა")
    .fill("Playwright full flow test");
  await page.getByRole("button", { name: "შეკვეთის შექმნა" }).click();

  await expect(page).toHaveURL(/\/orders\/LP-FLOW-0001$/);
  await expect(page.getByRole("heading", { name: "LP-FLOW-0001" })).toBeVisible();
  await expect(page.getByText("Flow Test Part")).toBeVisible();

  await page.goto("/orders");
  await expect(page.getByRole("heading", { name: "ჩემი შეკვეთები" })).toBeVisible();
  await expect(page.getByText("LP-FLOW-0001")).toBeVisible();

  expect(pageErrors).toEqual([]);
});
