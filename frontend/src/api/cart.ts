import type { PartOption } from "./client";

const CART_STORAGE_KEY = "lion_parts_cart";

export type CartItem = PartOption & {
  quote_id: string;
  part_number: string;
  quantity: number;
};

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
    (cartItem) => cartItem.part_option_id === item.part_option_id
  );

  if (existingItem) {
    const updatedItems = currentItems.map((cartItem) =>
      cartItem.part_option_id === item.part_option_id
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

export function removeCartItem(partOptionId: string) {
  const updatedItems = getCartItems().filter(
    (item) => item.part_option_id !== partOptionId
  );

  saveCartItems(updatedItems);
  return updatedItems;
}

export function clearCart() {
  saveCartItems([]);
}