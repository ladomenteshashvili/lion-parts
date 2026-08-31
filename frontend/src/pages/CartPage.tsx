import { useEffect, useMemo, useState } from "react";
import { getCartItems, removeCartItem, type CartItem } from "../api/cart";

function CartPage() {
  const [items, setItems] = useState<CartItem[]>([]);

  useEffect(() => {
    setItems(getCartItems());
  }, []);

  const total = useMemo(() => {
    return items.reduce(
      (sum, item) => sum + item.final_price_gel * item.quantity,
      0
    );
  }, [items]);

  function handleRemove(partOptionId: string) {
    setItems(removeCartItem(partOptionId));
  }

  if (items.length === 0) {
    return (
      <section className="card">
        <p className="eyebrow">კალათა</p>
        <h1>შენი კალათა ცარიელია</h1>
        <p className="muted">
          აქ გამოჩნდება არჩეული ნაწილები, რაოდენობები და საბოლოო ფასი.
        </p>
      </section>
    );
  }

  return (
    <section className="card">
      <p className="eyebrow">კალათა</p>
      <h1>შენი კალათა</h1>
      <p className="muted">
        ეს არის demo კალათა. შემდეგ ეტაპზე checkout-ს და payment flow-ს დავამატებთ.
      </p>

      <div className="cart-list">
        {items.map((item) => (
          <article className="cart-item" key={item.part_option_id}>
            <div>
              <h3>{item.name}</h3>
              <p className="muted">
                Part number: {item.part_number} · Quote: {item.quote_id}
              </p>
              <p className="muted">
                {item.brand} · {item.condition} · ETA: {item.eta_days} დღე
              </p>
            </div>

            <div className="cart-item__side">
              <span>Qty: {item.quantity}</span>
              <strong>
                {(item.final_price_gel * item.quantity).toLocaleString("ka-GE")} ₾
              </strong>
              <button type="button" onClick={() => handleRemove(item.part_option_id)}>
                წაშლა
              </button>
            </div>
          </article>
        ))}
      </div>

      <div className="cart-total">
        <span>ჯამი</span>
        <strong>{total.toLocaleString("ka-GE")} ₾</strong>
      </div>
    </section>
  );
}

export default CartPage;