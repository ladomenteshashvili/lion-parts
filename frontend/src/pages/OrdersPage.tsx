import { Link } from "react-router-dom";
import { useEffect, useState } from "react";

import { getOrders, type BackendOrder } from "../api/orders";
import { getProfile, type CustomerProfile } from "../api/profile";
import { getOrderStatusLabel } from "../utils/orderStatus";
import VerifiedPhoneRequiredCard from "../components/VerifiedPhoneRequiredCard";

function OrdersPage() {
  const [orders, setOrders] = useState<BackendOrder[]>([]);
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadOrdersPage() {
      try {
        const loadedProfile = await getProfile();
        setProfile(loadedProfile);

        if (!loadedProfile?.is_phone_verified) {
          setOrders([]);
          return;
        }

        const data = await getOrders();
        setOrders(data);
        setError("");
        window.dispatchEvent(new Event("lion-parts-orders-updated"));
      } catch (error) {
        console.error("Orders page load failed", error);
        setError("შეკვეთების ჩატვირთვა ვერ მოხერხდა");
      } finally {
        setIsLoading(false);
      }
    }

    loadOrdersPage();
  }, []);

  if (isLoading) {
    return (
      <section className="card">
        <p className="eyebrow">შეკვეთები</p>
        <h1>იტვირთება...</h1>
      </section>
    );
  }

  if (error) {
    return (
      <section className="card">
        <p className="eyebrow">შეკვეთები</p>
        <h1>შეცდომა</h1>
        <p className="form-error">{error}</p>
      </section>
    );
  }

  if (!profile?.is_phone_verified) {
    return (
      <VerifiedPhoneRequiredCard
        eyebrow="შეკვეთები"
        description="შეკვეთების სანახავად ჯერ უნდა დაადასტუროთ ტელეფონის ნომერი SMS კოდით. დადასტურების შემდეგ ამ ნომერზე შექმნილი შეკვეთები გამოჩნდება."
      />
    );
  }

  if (orders.length === 0) {
    return (
      <section className="card">
        <p className="eyebrow">შეკვეთები</p>
        <h1>შეკვეთები ჯერ არ გაქვს</h1>
        <p className="muted">
          აქ გამოჩნდება ამ დადასტურებულ ტელეფონზე შექმნილი შეკვეთები, სტატუსები
          და საჭირო მოქმედებები.
        </p>
      </section>
    );
  }

  return (
    <section className="card">
      <p className="eyebrow">შეკვეთები</p>
      <h1>ჩემი შეკვეთები</h1>

      <div className="profile-status">
        <strong>ტელეფონი დადასტურებულია</strong>
        <span>
          {profile.customer_name} · {profile.customer_phone}
        </span>
      </div>

      <div className="orders-list">
        {orders.map((order) => (
          <article className="order-card" key={order.order_number}>
            <div>
              <h3>{order.order_number}</h3>
              <p className="muted">
                {order.customer_name} · {order.customer_phone}
                {order.vin ? ` · VIN: ${order.vin}` : ""}
              </p>
              <p className="muted">
                Items: {order.items.length} · Created:{" "}
                {new Date(order.created_at).toLocaleString("ka-GE")}
              </p>
            </div>

            <div className="order-card__side">
              <span className="availability">
                {getOrderStatusLabel(order.status)}
              </span>
              <strong>{Number(order.total_gel).toLocaleString("ka-GE")} ₾</strong>
              <Link
                className="button-link button-link--small"
                to={`/orders/${order.order_number}`}
              >
                დეტალები
              </Link>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export default OrdersPage;