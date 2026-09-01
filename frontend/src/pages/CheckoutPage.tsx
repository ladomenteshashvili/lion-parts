import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { getCart, type CartItem } from "../api/cart";
import { createDemoOrder } from "../api/orders";

function CheckoutPage() {
  const navigate = useNavigate();

  const [items, setItems] = useState<CartItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [vin, setVin] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getCart()
      .then((cart) => {
        setItems(cart.items);
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

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (items.length === 0) {
      setError("კალათა ცარიელია");
      return;
    }

    if (!customerName.trim()) {
      setError("სახელი აუცილებელია");
      return;
    }

    if (!customerPhone.trim()) {
      setError("ტელეფონის ნომერი აუცილებელია");
      return;
    }

    createDemoOrder({
      customer_name: customerName.trim(),
      customer_phone: customerPhone.trim(),
      vin: vin.trim() || undefined,
      note: note.trim() || undefined,
      payment_type: "FULL",
      total_gel: total,
      items,
    });

    navigate("/orders");
  }

  if (isLoading) {
    return (
      <section className="card">
        <p className="eyebrow">Checkout</p>
        <h1>იტვირთება...</h1>
      </section>
    );
  }

  if (items.length === 0) {
    return (
      <section className="card">
        <p className="eyebrow">Checkout</p>
        <h1>კალათა ცარიელია</h1>
        <p className="muted">
          შეკვეთის გასაგრძელებლად ჯერ დაამატე ნაწილი კალათაში.
        </p>
      </section>
    );
  }

  return (
    <section className="card">
      <p className="eyebrow">Checkout</p>
      <h1>შეკვეთის გაფორმება</h1>
      <p className="muted">
        ეს არის demo checkout. შემდეგ order backend database-ში შეიქმნება.
      </p>

      <div className="checkout-summary">
        <span>ჯამი გადასახდელი</span>
        <strong>{total.toLocaleString("ka-GE")} ₾</strong>
      </div>

      <form className="checkout-form" onSubmit={handleSubmit}>
        <label>
          სახელი
          <input
            value={customerName}
            onChange={(event) => setCustomerName(event.target.value)}
            placeholder="მაგ: ლადო"
          />
        </label>

        <label>
          ტელეფონის ნომერი
          <input
            value={customerPhone}
            onChange={(event) => setCustomerPhone(event.target.value)}
            placeholder="მაგ: 599123456"
          />
        </label>

        <label>
          VIN — არასავალდებულო
          <input
            value={vin}
            onChange={(event) => setVin(event.target.value)}
            placeholder="VIN"
          />
        </label>

        <label>
          კომენტარი — არასავალდებულო
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="მაგ: გთხოვთ გადაამოწმოთ თავსებადობა"
          />
        </label>

        <div className="payment-demo-box">
          <strong>გადახდა</strong>
          <span>Demo რეჟიმი: 100% გადახდა, status — Payment pending</span>
        </div>

        {error && <p className="form-error">{error}</p>}

        <button type="submit">შეკვეთის შექმნა</button>
      </form>
    </section>
  );
}

export default CheckoutPage;