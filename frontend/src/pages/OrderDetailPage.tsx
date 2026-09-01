import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getOrderDetail, type BackendOrder } from "../api/orders";

function OrderDetailPage() {
  const { orderNumber } = useParams<{ orderNumber: string }>();

  const [order, setOrder] = useState<BackendOrder | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!orderNumber) {
      setError("Order number ვერ მოიძებნა");
      setIsLoading(false);
      return;
    }

    getOrderDetail(orderNumber)
      .then((data) => {
        setOrder(data);
        setError("");
      })
      .catch(() => {
        setError("შეკვეთის დეტალების ჩატვირთვა ვერ მოხერხდა");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [orderNumber]);

  if (isLoading) {
    return (
      <section className="card">
        <p className="eyebrow">Tracking</p>
        <h1>იტვირთება...</h1>
      </section>
    );
  }

  if (error || !order) {
    return (
      <section className="card">
        <p className="eyebrow">Tracking</p>
        <h1>შეცდომა</h1>
        <p className="form-error">{error || "შეკვეთა ვერ მოიძებნა"}</p>
        <Link className="button-link" to="/orders">
          შეკვეთებზე დაბრუნება
        </Link>
      </section>
    );
  }

  return (
    <section className="card">
      <Link className="back-link" to="/orders">
        ← შეკვეთებზე დაბრუნება
      </Link>

      <div className="order-detail-header">
        <div>
          <p className="eyebrow">Tracking</p>
          <h1>{order.order_number}</h1>
          <p className="muted">
            {order.customer_name} · {order.customer_phone}
            {order.vin ? ` · VIN: ${order.vin}` : ""}
          </p>
        </div>

        <div className="order-detail-status">
          <span className="availability">{order.status_label}</span>
          <strong>{Number(order.total_gel).toLocaleString("ka-GE")} ₾</strong>
        </div>
      </div>

      {order.status === "payment_pending" && (
        <div className="payment-demo-box">
          <strong>გადახდა მოსალოდნელია</strong>
          <span>
            Demo რეჟიმში შეკვეთა შეიქმნა Payment pending სტატუსით. რეალურ სისტემაში აქ იქნება payment gateway.
          </span>
        </div>
      )}

      <div className="tracking-timeline">
        <h2>შეკვეთის პროგრესი</h2>

        <div className="timeline-step timeline-step--active">
          <span className="timeline-dot" />
          <div>
            <strong>შეკვეთა შექმნილია</strong>
            <p className="muted">
              {new Date(order.created_at).toLocaleString("ka-GE")}
            </p>
          </div>
        </div>

        <div className="timeline-step">
          <span className="timeline-dot" />
          <div>
            <strong>გადახდა დასადასტურებელია</strong>
            <p className="muted">შემდეგ ეტაპზე დაემატება online payment confirmation.</p>
          </div>
        </div>

        <div className="timeline-step">
          <span className="timeline-dot" />
          <div>
            <strong>შესყიდვა / დამუშავება</strong>
            <p className="muted">ოპერატორი გადაამოწმებს availability, ETA და თავსებადობას.</p>
          </div>
        </div>

        <div className="timeline-step">
          <span className="timeline-dot" />
          <div>
            <strong>ტრანსპორტირება საქართველოში</strong>
            <p className="muted">ამ ეტაპზე გამოჩნდება shipping/import პროგრესი.</p>
          </div>
        </div>

        <div className="timeline-step">
          <span className="timeline-dot" />
          <div>
            <strong>მზადაა გაცემისთვის</strong>
            <p className="muted">მომხმარებელი მიიღებს pickup/payment notice-ს.</p>
          </div>
        </div>
      </div>

      <div className="order-items">
        <h2>ნაწილები</h2>

        {order.items.map((item) => (
          <article className="cart-item" key={item.id}>
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
            </div>
          </article>
        ))}
      </div>

      {order.note && (
        <div className="note-box">
          <strong>კომენტარი</strong>
          <p>{order.note}</p>
        </div>
      )}
    </section>
  );
}

export default OrderDetailPage;