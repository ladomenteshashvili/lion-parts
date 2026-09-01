import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { getOrders, type BackendOrder } from "../api/orders";
import { getOrderStatusLabel } from "../utils/orderStatus";

function OrdersPage() {
  const [orders, setOrders] = useState<BackendOrder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getOrders()
      .then((data) => {
        setOrders(data);
        setError("");
      })
      .catch(() => {
        setError("შეკვეთების ჩატვირთვა ვერ მოხერხდა");
      })
      .finally(() => {
        setIsLoading(false);
      });
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

  if (orders.length === 0) {
    return (
      <section className="card">
        <p className="eyebrow">შეკვეთები</p>
        <h1>შეკვეთები ჯერ არ გაქვს</h1>
        <p className="muted">
          აქ გამოჩნდება შექმნილი შეკვეთები, სტატუსები და საჭირო მოქმედებები.
        </p>
      </section>
    );
  }

  return (
    <section className="card">
      <p className="eyebrow">შეკვეთები</p>
      <h1>ჩემი შეკვეთები</h1>

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
              <span className="availability">{getOrderStatusLabel(order.status)}</span>
              <strong>{Number(order.total_gel).toLocaleString("ka-GE")} ₾</strong>
              <Link className="button-link button-link--small" to={`/orders/${order.order_number}`}>
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