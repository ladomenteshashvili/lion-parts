import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { getCart, type CartItem } from "../api/cart";
import { checkoutOrder } from "../api/orders";
import { getProfile, type CustomerProfile } from "../api/profile";

function CheckoutPage() {
  const navigate = useNavigate();

  const [items, setItems] = useState<CartItem[]>([]);
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [vin, setVin] = useState("");
  const [note, setNote] = useState("");

  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    async function loadCheckoutData() {
      try {
        const cart = await getCart();
        setItems(cart.items);

        try {
          const loadedProfile = await getProfile();

          if (loadedProfile) {
            setProfile(loadedProfile);
            setCustomerName(loadedProfile.customer_name);
            setCustomerPhone(loadedProfile.customer_phone);
          }
        } catch {
          // Profile is optional for loading checkout,
          // but verified profile is required before order submit.
        }
      } catch {
        setError("კალათის ჩატვირთვა ვერ მოხერხდა");
      } finally {
        setIsLoading(false);
      }
    }

    loadCheckoutData();
  }, []);

  const total = useMemo(() => {
    return items.reduce(
      (sum, item) => sum + Number(item.final_price_gel) * item.quantity,
      0
    );
  }, [items]);

  const hasCustomerWeightItems = useMemo(() => {
    return items.some((item) => item.weight_source === "customer");
  }, [items]);

  const isVerifiedProfile = Boolean(profile?.is_phone_verified);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (items.length === 0) {
      setError("კალათა ცარიელია");
      return;
    }

    if (!profile || !profile.is_phone_verified) {
      setError("შეკვეთის გასაფორმებლად ჯერ ტელეფონის ნომერი უნდა დაადასტუროთ.");
      return;
    }

    setIsSubmitting(true);
    setError("");

    try {
      const order = await checkoutOrder({
        customer_name: profile.customer_name,
        customer_phone: profile.customer_phone,
        vin: vin.trim() || undefined,
        note: note.trim() || undefined,
      });

      window.dispatchEvent(new Event("lion-parts-cart-updated"));
      window.dispatchEvent(new Event("lion-parts-orders-updated"));

      navigate(`/orders/${order.order_number}`);
    } catch (error) {
      console.error("Checkout failed", error);

      const errorMessage =
        error instanceof Error
          ? error.message
          : "შეკვეთის შექმნა ვერ მოხერხდა";

      setError(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
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
        გადაამოწმეთ შეკვეთის დეტალები. შეკვეთა შეიქმნება გადახდის მოლოდინში
        და გადახდის დადასტურების შემდეგ დაიწყება დამუშავება.
      </p>

      {!isVerifiedProfile && (
        <div className="action-required-card">
          <strong>ტელეფონის დადასტურება საჭიროა</strong>
          <span>
            შეკვეთის გასაფორმებლად ჯერ პროფილში უნდა დაადასტუროთ ქართული
            მობილური ნომერი SMS კოდით.
          </span>
          <button type="button" onClick={() => navigate("/profile")}>
            პროფილზე გადასვლა
          </button>
        </div>
      )}

      {isVerifiedProfile && (
        <div className="profile-status">
          <strong>ტელეფონი დადასტურებულია</strong>
          <span>
            {profile?.customer_name} · {profile?.customer_phone}
          </span>
        </div>
      )}

      <div className="checkout-summary">
        <span>ჯამი გადასახდელი</span>
        <strong>{total.toLocaleString("ka-GE")} ₾</strong>
      </div>

      <div className="checkout-policy-box">
        <strong>შეკვეთის პირობები</strong>

        <ul>
          <li>
            ეკრანზე ნაჩვენები თანხა არის გადასახდელი თანხა შეკვეთის დასაწყებად.
          </li>
          <li>
            ნაწილის ხელმისაწვდომობა, მიწოდების დრო და VIN-თან თავსებადობა
            დამუშავების ეტაპზე დადასტურდება.
          </li>

          {hasCustomerWeightItems && (
            <li>
              ერთ ან რამდენიმე ნაწილზე ფასი დათვლილია თქვენს მიერ შეყვანილი
              სავარაუდო წონით. საბოლოო წონა დადგინდება აშშ-ის საწყობში მიღების
              შემდეგ. თუ რეალური წონა განსხვავებული იქნება, საბოლოო თანხა შეიძლება
              დაკორექტირდეს — შესაძლოა დაემატოს ან დაბრუნდეს თანხა.
            </li>
          )}
        </ul>
      </div>

      <div className="checkout-items">
        <h2>ნაწილები</h2>

        {items.map((item) => (
          <article className="cart-item" key={item.cart_item_id}>
            <div>
              <h3>{item.name}</h3>

              <p className="muted">
                Part number: {item.part_number} · Quote: {item.quote_id}
              </p>

              <p className="muted">
                {item.brand} · {item.condition} · ETA:{" "}
                {item.eta_days ? `${item.eta_days} დღე` : "მითითებული არ არის"}
              </p>

              {item.weight_kg && (
                <p className="muted">
                  წონა: {Number(item.weight_kg).toLocaleString("ka-GE")} კგ
                </p>
              )}

              {item.customer_notice && (
                <p className="customer-notice">{item.customer_notice}</p>
              )}
            </div>

            <div className="cart-item__side">
              <span>Qty: {item.quantity}</span>

              <strong>
                {(Number(item.final_price_gel) * item.quantity).toLocaleString(
                  "ka-GE"
                )}{" "}
                ₾
              </strong>
            </div>
          </article>
        ))}
      </div>

      <form className="checkout-form" onSubmit={handleSubmit}>
        <label>
          სახელი
          <input
            value={customerName}
            onChange={(event) => setCustomerName(event.target.value)}
            placeholder="მაგ: ლადო"
            disabled={isVerifiedProfile}
          />
        </label>

        <label>
          ტელეფონის ნომერი
          <input
            value={customerPhone}
            onChange={(event) => setCustomerPhone(event.target.value)}
            placeholder="მაგ: 599123456"
            disabled={isVerifiedProfile}
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
          <span>
            შეკვეთა შეიქმნება გადახდის მოლოდინში. გადახდის დადასტურების შემდეგ
            დამუშავება დაიწყება.
          </span>
        </div>

        {error && <p className="form-error">{error}</p>}

        <button type="submit" disabled={isSubmitting || !isVerifiedProfile}>
          {isSubmitting ? "იქმნება..." : "შეკვეთის შექმნა"}
        </button>
      </form>
    </section>
  );
}

export default CheckoutPage;