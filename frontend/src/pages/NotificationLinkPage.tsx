import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  acknowledgeCustomerNotification,
  getCustomerNotification,
  type CustomerNotification,
} from "../api/orders";
import { getOrderStatusLabel } from "../utils/orderStatus";
import { getOrderItemStatusLabel } from "../utils/orderItemStatus";

function NotificationLinkPage() {
  const { token } = useParams<{ token: string }>();
  const [notification, setNotification] =
    useState<CustomerNotification | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAcknowledging, setIsAcknowledging] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadNotification() {
      if (!token) {
        setError("Notification link არასწორია");
        setIsLoading(false);
        return;
      }

      try {
        const data = await getCustomerNotification(token);
        setNotification(data);
        setError("");
      } catch (error) {
        console.error("Notification load failed", error);
        setError("შეტყობინება ვერ მოიძებნა ან ლინკი არასწორია.");
      } finally {
        setIsLoading(false);
      }
    }

    loadNotification();
  }, [token]);

  async function handleAcknowledge() {
    if (!token) {
      return;
    }

    setIsAcknowledging(true);
    setError("");

    try {
      const data = await acknowledgeCustomerNotification(token);
      setNotification(data);
      window.dispatchEvent(new Event("lion-parts-orders-updated"));
    } catch (error) {
      console.error("Notification acknowledge failed", error);
      setError("შეტყობინების მონიშვნა ვერ მოხერხდა");
    } finally {
      setIsAcknowledging(false);
    }
  }

  if (isLoading) {
    return (
      <section className="card">
        <p className="eyebrow">შეტყობინება</p>
        <h1>იტვირთება...</h1>
      </section>
    );
  }

  if (error || !notification) {
    return (
      <section className="card">
        <p className="eyebrow">შეტყობინება</p>
        <h1>შეცდომა</h1>
        <p className="form-error">{error || "შეტყობინება ვერ მოიძებნა"}</p>
      </section>
    );
  }

  const order = notification.order;

  return (
    <section className="card notification-page">
      {!notification.is_read_by_customer && (
        <div className="forced-message-backdrop" role="dialog" aria-modal="true">
          <div className="forced-message-modal">
            <p className="eyebrow">ახალი შეტყობინება</p>
            <h1>{notification.title}</h1>
            <p>{notification.message}</p>

            {notification.item_part_number && (
              <p className="muted">Part: {notification.item_part_number}</p>
            )}

            <button
              type="button"
              onClick={handleAcknowledge}
              disabled={isAcknowledging}
            >
              {isAcknowledging ? "მუშავდება..." : "გასაგებია"}
            </button>
          </div>
        </div>
      )}

      <p className="eyebrow">შეკვეთის შეტყობინება</p>
      <h1>{notification.title}</h1>
      <p>{notification.message}</p>

      <div className="notification-order-box">
        <div>
          <p className="eyebrow">Order</p>
          <h2>{order.order_number}</h2>
          <p className="muted">
            {order.customer_name} · {order.customer_phone}
            {order.vin ? ` · VIN: ${order.vin}` : ""}
          </p>
        </div>

        <div className="order-detail-status">
          <span className="availability">{getOrderStatusLabel(order.status)}</span>
          <strong>{Number(order.total_gel).toLocaleString("ka-GE")} ₾</strong>
        </div>
      </div>

      <div className="order-items-list">
        {order.items.map((item) => (
          <article className="order-item-card" key={item.id}>
            <div>
              <h3>{item.name}</h3>
              <p className="muted">
                Part: {item.part_number} · Status:{" "}
                {getOrderItemStatusLabel(item.item_status)}
              </p>
              {item.action_required && (
                <p className="customer-notice">
                  ამ ნაწილზე საჭიროა მომხმარებლის მოქმედება.
                </p>
              )}
            </div>

            <strong>
              {(Number(item.final_price_gel) * item.quantity).toLocaleString(
                "ka-GE"
              )}{" "}
              ₾
            </strong>
          </article>
        ))}
      </div>

      <div className="support-box">
        <div>
          <p className="eyebrow">Support history</p>
          <h2>ოპერატორთან მიმოწერა</h2>
        </div>

        {order.support_messages.length === 0 ? (
          <p className="muted">მიმოწერა ჯერ არ არის.</p>
        ) : (
          <div className="support-messages">
            {order.support_messages.map((message) => (
              <article
                className={
                  message.sender_type === "customer"
                    ? "support-message support-message--customer"
                    : "support-message support-message--operator"
                }
                key={message.id}
              >
                <div>
                  <strong>
                    {message.sender_type === "customer"
                      ? "თქვენ"
                      : message.sender_name || "ოპერატორი"}
                  </strong>
                  <span className="muted">
                    {new Date(message.created_at).toLocaleString("ka-GE")}
                    {message.item_part_number
                      ? ` · Part: ${message.item_part_number}`
                      : ""}
                  </span>
                </div>
                <p>{message.message}</p>
              </article>
            ))}
          </div>
        )}
      </div>

      <p className="muted">
        ეს ლინკი მხოლოდ შეკვეთის და შეტყობინების სანახავად არის. მოქმედების
        დასადასტურებლად, გაუქმებისთვის ან ახალი support შეტყობინებისთვის შედით
        ტელეფონის დადასტურებით.
      </p>

      <Link className="button-link" to={`/orders/${order.order_number}`}>
        შეკვეთის სრული გვერდის გახსნა
      </Link>
    </section>
  );
}

export default NotificationLinkPage;
