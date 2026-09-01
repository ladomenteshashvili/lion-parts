import { getSessionId } from "./cart";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

export type OrderItem = {
  id: number;
  cart_item_id: string;
  quote_id: string;
  part_option_id: string;
  part_number: string;
  name: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number | null;
  final_price_gel: string;
  proposed_final_price_gel: string | null;
  currency: string;
  note: string;
  quantity: number;
  proposed_eta_days: number | null;
  item_status: string;
  action_required: boolean;
  action_type: string;
  action_message: string;
  created_at: string;
  updated_at: string;
};

export type BackendOrder = {
  id: number;
  order_number: string;
  session_id: string;
  customer_name: string;
  customer_phone: string;
  vin: string;
  note: string;
  payment_type: "full";
  status: string;
  status_label: string;
  total_gel: string;
  items: OrderItem[];
  created_at: string;
  updated_at: string;
};

export async function getOrders(): Promise<BackendOrder[]> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/?session_id=${encodeURIComponent(sessionId)}`
  );

  if (!response.ok) {
    throw new Error("Orders load failed");
  }

  return response.json();
}

export async function checkoutOrder(payload: {
  customer_name: string;
  customer_phone: string;
  vin?: string;
  note?: string;
}): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/orders/checkout/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      ...payload,
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Checkout failed");
  }

  return response.json();
}

export async function getOrderDetail(orderNumber: string): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/${encodeURIComponent(
      orderNumber
    )}/?session_id=${encodeURIComponent(sessionId)}`
  );

  if (!response.ok) {
    throw new Error("Order detail load failed");
  }

  return response.json();
}

export async function resolveOrderItemAction(
  itemId: number
): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/items/${itemId}/demo-resolve-action/`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
      }),
    }
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Resolve item action failed");
  }

  return response.json();
}