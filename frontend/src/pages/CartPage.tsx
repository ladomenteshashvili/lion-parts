import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getCart, removeCartItem, type CartItem } from "../api/cart";

function CartPage() {
  const [items, setItems] = useState<CartItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getCart()
      .then((cart) => {
        setItems(cart.items);
        setError("");
      })
      .catch(() => {
        setError("კალათის ჩატვირთვა ვერ მოხერხდა");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  const total = useMemo(() => {
    return items.reduce(
      (sum, item) => sum + Number(item.final_price_gel) * item.quantity,
      0
    );
  }, [items]);

  async function handleRemove(cartItemId: string) {
    try {
      const cart = await removeCartItem(cartItemId);
      setItems(cart.items);
    } catch {
      setError("ნაწილის წაშლა ვერ მოხერხდა");
    }
  }

  if (isLoading) {
    return (
      <section className="card">
        <p className="eyebrow">კალათა</p>
        <h1>იტვირთება...</h1>
      </section>
    );
  }

  if (error) {
    return (
      <section className="card">
        <p className="eyebrow">კალათა</p>
        <h1>შეცდომა</h1>
        <p className="form-error">{error}</p>
      </section>
    );
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
        კალათა უკვე backend-ში ინახება session_id-ით. Login-ის შემდეგ ამას customer account-ს მივაბამთ.
      </p>

      <div className="cart-list">
        {items.map((item) => (
          <article className="cart-item" key={item.cart_item_id}>
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
                {(Number(item.final_price_gel) * item.quantity).toLocaleString("ka-GE")} ₾
              </strong>
              <button type="button" onClick={() => handleRemove(item.cart_item_id)}>
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

      <div className="cart-actions">
        <Link className="button-link" to="/checkout">
          შეკვეთის გაგრძელება
        </Link>
      </div>
    </section>
  );
}

export default CartPage;