import type { PartOption } from "./client";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
const SESSION_STORAGE_KEY = "lion_parts_session_id";

export type CartItem = PartOption & {
  id: number;
  cart_item_id: string;
  quote_id: string;
  part_number: string;
  quantity: number;
};

export type BackendCart = {
  id: number;
  session_id: string;
  items: CartItem[];
  total_gel: number;
  created_at: string;
  updated_at: string;
};

export function getSessionId() {
  const existingSessionId = localStorage.getItem(SESSION_STORAGE_KEY);

  if (existingSessionId) {
    return existingSessionId;
  }

  const randomPart = Math.random().toString(36).slice(2);
  const newSessionId = `guest-${Date.now()}-${randomPart}`;

  localStorage.setItem(SESSION_STORAGE_KEY, newSessionId);

  return newSessionId;
}
export function buildCartItemId(params: {
  quote_id: string;
  part_option_id: string;
  part_number: string;
}) {
  return `${params.quote_id}:${params.part_option_id}:${params.part_number}`;
}

export async function getCart(): Promise<BackendCart> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/cart/?session_id=${encodeURIComponent(sessionId)}`
  );

  if (!response.ok) {
    throw new Error("Cart load failed");
  }

  return response.json();
}

export async function addCartItem(item: {
  cart_item_id: string;
  quote_id: string;
  part_option_id: string;
  part_number: string;
  name: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number;
  final_price_gel: number;
  currency: "GEL";
  note?: string;
  quantity: number;
}): Promise<BackendCart> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/cart/items/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      ...item,
      final_price_gel: Number(item.final_price_gel),
    }),
  });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || "Add cart item failed");
    }

  return response.json();
}

export async function removeCartItem(cartItemId: string): Promise<BackendCart> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/cart/items/${encodeURIComponent(
      cartItemId
    )}/?session_id=${encodeURIComponent(sessionId)}`,
    {
      method: "DELETE",
    }
  );

  if (!response.ok) {
    throw new Error("Remove cart item failed");
  }

  return response.json();
}