import { getSessionId } from "./cart";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

export type OrderItemEvent = {
  id: number;
  event_type: string;
  title: string;
  message: string;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  actor_type: string;
  actor_name: string;
  visible_to_customer: boolean;
  created_at: string;
};

export type OrderItem = {
  id: number;
  cart_item_id: string;
  quote_id: string;
  part_option_id: string;
  part_number: string;
  proposed_part_number: string;
  proposed_name: string;
  name: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number | null;
  expected_arrival_date: string | null;
  weight_kg: string | null;
  final_price_gel: string;
  proposed_final_price_gel: string | null;
  currency: string;
  note: string;
  customer_notice: string;
  weight_source: string;
  quantity: number;
  proposed_eta_days: number | null;
  proposed_expected_arrival_date: string | null;
  item_status: string;
  action_required: boolean;
  action_type: string;
  action_message: string;
  events: OrderItemEvent[];
  created_at: string;
  updated_at: string;
};


export type OrderSupportMessage = {
  id: number;
  item_id: number | null;
  item_part_number: string | null;
  sender_type: "customer" | "operator" | "system";
  sender_name: string;
  message: string;
  visible_to_customer: boolean;
  is_read_by_customer: boolean;
  is_read_by_operator: boolean;
  created_at: string;
};

export type OrderPayment = {
  id: number;
  payment_reference: string;
  external_payment_id: string;
  provider: string;
  status: string;
  amount_gel: string;
  currency: string;
  paid_at: string | null;
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
  payment: OrderPayment | null;
  status: string;
  status_label: string;
  total_gel: string;
  items: OrderItem[];
  support_messages: OrderSupportMessage[];
  support_unread_count: number;
  created_at: string;
  updated_at: string;
};


export type PublicOrder = Omit<BackendOrder, "session_id">;

export type CustomerNotification = {
  id: number;
  token: string;
  notification_type: "support_reply" | "order_update" | "action_required" | "notice";
  title: string;
  message: string;
  item_id: number | null;
  item_part_number: string | null;
  support_message_id: number | null;
  event_id: number | null;
  is_read_by_customer: boolean;
  acknowledged_at: string | null;
  created_at: string;
  order: PublicOrder;
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
    `${API_BASE_URL}/api/orders/items/${itemId}/resolve-action/`,
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


export async function cancelOrderItemAction(
  itemId: number
): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/items/${itemId}/cancel-action/`,
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
    throw new Error(errorText || "Cancel item action failed");
  }

  return response.json();
}


export async function acknowledgeOrderItemAction(
  itemId: number
): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/items/${itemId}/acknowledge-action/`,
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
    throw new Error(errorText || "Acknowledge item action failed");
  }

  return response.json();
}


export async function sendOrderSupportMessage(
  orderNumber: string,
  payload: {
    message: string;
    item_id?: number;
  }
): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/${encodeURIComponent(
      orderNumber
    )}/support/messages/`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
        ...payload,
      }),
    }
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Send support message failed");
  }

  return response.json();
}

export async function acknowledgeOrderSupportMessages(
  orderNumber: string
): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/${encodeURIComponent(
      orderNumber
    )}/support/acknowledge/`,
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
    throw new Error(errorText || "Acknowledge support messages failed");
  }

  return response.json();
}


export async function getCustomerNotification(
  token: string
): Promise<CustomerNotification> {
  const response = await fetch(
    `${API_BASE_URL}/api/orders/public/notifications/${encodeURIComponent(token)}/`
  );

  if (!response.ok) {
    throw new Error("Notification load failed");
  }

  return response.json();
}

export async function acknowledgeCustomerNotification(
  token: string
): Promise<CustomerNotification> {
  const response = await fetch(
    `${API_BASE_URL}/api/orders/public/notifications/${encodeURIComponent(
      token
    )}/acknowledge/`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    }
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Notification acknowledge failed");
  }

  return response.json();
}

export async function confirmOrderPayment(
  orderNumber: string,
  paymentReference = ""
): Promise<BackendOrder> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/orders/${encodeURIComponent(
      orderNumber
    )}/verify-payment/`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
        payment_reference: paymentReference,
      }),
    }
  );

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Verify payment failed");
  }

  return response.json();
}