import { useEffect, useState } from "react";
import { getOrders, type DemoOrder } from "../api/orders";

function OrdersPage() {
  const [orders, setOrders] = useState<DemoOrder[]>([]);

  useEffect(() => {
    setOrders(getOrders());
  }, []);

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
          <article className="order-card" key={order.order_id}>
            <div>
              <h3>{order.order_id}</h3>
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
              <span className="availability">{order.status}</span>
              <strong>{order.total_gel.toLocaleString("ka-GE")} ₾</strong>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export default OrdersPage;