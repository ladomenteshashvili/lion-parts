import type { PartOption } from "./client";

const CART_STORAGE_KEY = "lion_parts_cart";

export type CartItem = PartOption & {
  cart_item_id: string;
  quote_id: string;
  part_number: string;
  quantity: number;
};

export function buildCartItemId(params: {
  quote_id: string;
  part_option_id: string;
  part_number: string;
}) {
  return `${params.quote_id}:${params.part_option_id}:${params.part_number}`;
}

export function getCartItems(): CartItem[] {
  const rawCart = localStorage.getItem(CART_STORAGE_KEY);

  if (!rawCart) {
    return [];
  }

  try {
    return JSON.parse(rawCart) as CartItem[];
  } catch {
    return [];
  }
}

export function saveCartItems(items: CartItem[]) {
  localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(items));
}

export function addCartItem(item: CartItem) {
  const currentItems = getCartItems();

  const existingItem = currentItems.find(
    (cartItem) => cartItem.cart_item_id === item.cart_item_id
  );

  if (existingItem) {
    const updatedItems = currentItems.map((cartItem) =>
      cartItem.cart_item_id === item.cart_item_id
        ? { ...cartItem, quantity: cartItem.quantity + item.quantity }
        : cartItem
    );

    saveCartItems(updatedItems);
    return updatedItems;
  }

  const updatedItems = [...currentItems, item];
  saveCartItems(updatedItems);
  return updatedItems;
}

export function removeCartItem(cartItemId: string) {
  const updatedItems = getCartItems().filter(
    (item) => item.cart_item_id !== cartItemId
  );

  saveCartItems(updatedItems);
  return updatedItems;
}

export function clearCart() {
  saveCartItems([]);
}