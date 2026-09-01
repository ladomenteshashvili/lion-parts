import type { CartItem } from "./cart";

const ORDERS_STORAGE_KEY = "lion_parts_orders";

export type DemoOrder = {
  order_id: string;
  customer_name: string;
  customer_phone: string;
  vin?: string;
  note?: string;
  payment_type: "FULL";
  status: "Payment pending";
  total_gel: number;
  items: CartItem[];
  created_at: string;
};

export function getOrders(): DemoOrder[] {
  const rawOrders = localStorage.getItem(ORDERS_STORAGE_KEY);

  if (!rawOrders) {
    return [];
  }

  try {
    return JSON.parse(rawOrders) as DemoOrder[];
  } catch {
    return [];
  }
}

export function saveOrders(orders: DemoOrder[]) {
  localStorage.setItem(ORDERS_STORAGE_KEY, JSON.stringify(orders));
}

export function createDemoOrder(
  order: Omit<DemoOrder, "order_id" | "created_at" | "status">
) {
  const currentOrders = getOrders();

  const newOrder: DemoOrder = {
    ...order,
    order_id: `ORD-${Date.now()}`,
    status: "Payment pending",
    created_at: new Date().toISOString(),
  };

  const updatedOrders = [newOrder, ...currentOrders];
  saveOrders(updatedOrders);

  return newOrder;
}