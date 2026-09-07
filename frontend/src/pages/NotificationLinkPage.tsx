import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  acknowledgeCustomerNotification,
  getCustomerNotification,
  type CustomerNotification,
  type OrderItem,
  type OrderSupportMessage,
} from "../api/orders";
import { formatDateKa } from "../utils/dateFormat";
import { getOrderItemStatusLabel } from "../utils/orderItemStatus";
import { getOrderStatusLabel } from "../utils/orderStatus";

type NotificationViewState =
  | "order_update"
  | "item_update"
  | "support_reply"
  | "action_required";

function getNotificationViewState(
  notification: CustomerNotification
): NotificationViewState {
  if (notification.notification_type === "support_reply") {
    return "support_reply";
  }

  if (notification.notification_type === "action_required") {
    return "action_required";
  }

  if (notification.item_id) {
    return "item_update";
  }

  return "order_update";
}

function getStateCopy(state: NotificationViewState) {
  if (state === "support_reply") {
    return {
      eyebrow: "ოპერატორის პასუხი",
      title: "თქვენ გაქვთ ახალი პასუხი",
      reason:
        "ეს ლინკი გამოგიგზავნეთ, რადგან ოპერატორმა თქვენს შეკვეთაზე ახალი პასუხი დატოვა.",
    };
  }

  if (state === "action_required") {
    return {
      eyebrow: "საჭიროა მოქმედება",
      title: "შეკვეთაზე საჭიროა თქვენი პასუხი",
      reason:
        "ეს ლინკი გამოგიგზავნეთ, რადგან ერთ ნაწილზე ცვლილებაა დასადასტურებელი.",
    };
  }

  if (state === "item_update") {
    return {
      eyebrow: "ნაწილის განახლება",
      title: "ერთ ნაწილზე განახლებაა",
      reason:
        "ეს ლინკი გამოგიგზავნეთ, რადგან თქვენს შეკვეთაში კონკრეტული ნაწილის სტატუსი ან ინფორმაცია შეიცვალა.",
    };
  }

  return {
    eyebrow: "შეკვეთის განახლება",
    title: "თქვენი შეკვეთა განახლდა",
    reason:
      "ეს ლინკი გამოგიგზავნეთ, რადგან თქვენი შეკვეთის სტატუსი ან ინფორმაცია შეიცვალა.",
  };
}

function formatGel(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }

  return `${Number(value).toLocaleString("ka-GE")} ₾`;
}

function formatEta(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }

  return `${value} დღე`;
}

function findTargetItem(notification: CustomerNotification): OrderItem | null {
  if (!notification.item_id) {
    return null;
  }

  return (
    notification.order.items.find((item) => item.id === notification.item_id) ||
    null
  );
}

function findTargetSupportMessage(
  notification: CustomerNotification
): OrderSupportMessage | null {
  if (!notification.support_message_id) {
    return null;
  }

  return (
    notification.order.support_messages.find((message) => {
      return message.id === notification.support_message_id;
    }) || null
  );
}

function NotificationLinkPage() {
  const { token } = useParams<{ token: string }>();

  const [notification, setNotification] =
    useState<CustomerNotification | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAcknowledging, setIsAcknowledging] = useState(false);
  const [error, setError] = useState("");

  const supportRef = useRef<HTMLDivElement | null>(null);

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

  useEffect(() => {
    if (!notification || !notification.is_read_by_customer) {
      return;
    }

    const state = getNotificationViewState(notification);

    if (state === "support_reply") {
      window.setTimeout(() => {
        supportRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }, 150);
    }
  }, [notification]);

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

  function renderBlockingMessage(notificationData: CustomerNotification) {
    const state = getNotificationViewState(notificationData);
    const copy = getStateCopy(state);
    const targetSupportMessage = findTargetSupportMessage(notificationData);
    const message =
      notificationData.message || targetSupportMessage?.message || copy.reason;

    return (
      <div className="notification-blocking-screen" role="dialog" aria-modal="true">
        <div className="notification-blocking-card">
          <p className="eyebrow">{copy.eyebrow}</p>
          <h1>{notificationData.title || copy.title}</h1>

          <p className="notification-reason">{copy.reason}</p>

          <div className="notification-message-box">
            <strong>შეტყობინება</strong>
            <p>{message}</p>
          </div>

          <div className="notification-mini-summary">
            <span>შეკვეთა</span>
            <strong>{notificationData.order.order_number}</strong>

            {notificationData.item_part_number && (
              <>
                <span>ნაწილი</span>
                <strong>{notificationData.item_part_number}</strong>
              </>
            )}
          </div>

          <button
            type="button"
            onClick={handleAcknowledge}
            disabled={isAcknowledging}
          >
            {isAcknowledging ? "მუშავდება..." : "გასაგებია"}
          </button>

          <p className="muted">
            ამ შეტყობინების დახურვა მხოლოდ “გასაგებია” ღილაკით შეიძლება.
          </p>
        </div>
      </div>
    );
  }

  function renderTargetItemPanel(item: OrderItem, state: NotificationViewState) {
    const hasAlternative = Boolean(item.proposed_part_number);
    const hasPriceChange = Boolean(item.proposed_final_price_gel);
    const hasEtaChange = Boolean(item.proposed_eta_days);

    return (
      <div className="notification-focus-card">
        <div>
          <p className="eyebrow">
            {state === "action_required"
              ? "საჭიროა თქვენი პასუხი"
              : "განახლებული ნაწილი"}
          </p>
          <h2>{item.name}</h2>
          <p className="muted">Part: {item.part_number}</p>
        </div>

        <div className="notification-change-grid">
          <span>სტატუსი</span>
          <strong>{getOrderItemStatusLabel(item.item_status)}</strong>

          <span>ფასი</span>
          <strong>{formatGel(item.final_price_gel)}</strong>

          <span>ETA</span>
          <strong>{formatEta(item.eta_days)}</strong>

          {item.expected_arrival_date && (
            <>
              <span>მოსალოდნელი თარიღი</span>
              <strong>{formatDateKa(item.expected_arrival_date)}</strong>
            </>
          )}
        </div>

        {(hasAlternative || hasPriceChange || hasEtaChange) && (
          <div className="notification-proposed-box">
            <strong>შემოთავაზებული ცვლილება</strong>

            {hasAlternative && (
              <p>
                ალტერნატიული ნომერი:{" "}
                <strong>{item.proposed_part_number}</strong>
                {item.proposed_name ? ` · ${item.proposed_name}` : ""}
              </p>
            )}

            {hasPriceChange && (
              <p>
                ახალი ფასი:{" "}
                <strong>{formatGel(item.proposed_final_price_gel)}</strong>
              </p>
            )}

            {hasEtaChange && (
              <p>
                ახალი ETA: <strong>{formatEta(item.proposed_eta_days)}</strong>
                {item.proposed_expected_arrival_date
                  ? ` · ${formatDateKa(item.proposed_expected_arrival_date)}`
                  : ""}
              </p>
            )}
          </div>
        )}

        {item.action_message && (
          <div className="notification-message-box">
            <strong>ოპერატორის შეტყობინება</strong>
            <p>{item.action_message}</p>
          </div>
        )}

        {state === "action_required" && (
          <div className="action-required-card">
            <strong>ამ ნაწილზე საჭიროა გადაწყვეტილება</strong>
            <span>
              დადასტურება ან გაუქმება მხოლოდ ტელეფონის დადასტურების შემდეგ არის
              შესაძლებელი.
            </span>
            <Link className="button-link" to={`/orders/${notification?.order.order_number}`}>
              ტელეფონით შესვლა და პასუხის გაცემა
            </Link>
          </div>
        )}
      </div>
    );
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

  const state = getNotificationViewState(notification);
  const copy = getStateCopy(state);
  const order = notification.order;
  const targetItem = findTargetItem(notification);
  const targetSupportMessage = findTargetSupportMessage(notification);

  return (
    <section className="card notification-clean-page">
      {!notification.is_read_by_customer && renderBlockingMessage(notification)}

      <div className="notification-clean-hero">
        <div>
          <p className="eyebrow">{copy.eyebrow}</p>
          <h1>{copy.title}</h1>
          <p>{copy.reason}</p>
        </div>

        <span className="notification-state-pill">
          {notification.is_read_by_customer ? "ნანახია" : "ახალი"}
        </span>
      </div>

      <div className="notification-order-box">
        <div>
          <p className="eyebrow">შეკვეთა</p>
          <h2>{order.order_number}</h2>
          <p className="muted">
            {order.customer_name} · {order.customer_phone}
            {order.vin ? ` · VIN: ${order.vin}` : ""}
          </p>
        </div>

        <div className="order-detail-status">
          <span className="availability">{getOrderStatusLabel(order.status)}</span>
          <strong>{formatGel(order.total_gel)}</strong>
        </div>
      </div>

      {state === "support_reply" && targetSupportMessage && (
        <div className="notification-focus-card">
          <p className="eyebrow">ოპერატორის პასუხი</p>
          <h2>ახალი შეტყობინება</h2>
          <div className="notification-message-box">
            <strong>{targetSupportMessage.sender_name || "ოპერატორი"}</strong>
            <p>{targetSupportMessage.message}</p>
          </div>
        </div>
      )}

      {targetItem && renderTargetItemPanel(targetItem, state)}

      <div className="order-items-list">
        {order.items.map((item) => (
          <article
            className={
              item.id === notification.item_id
                ? "order-item-card order-item-card--highlighted"
                : "order-item-card"
            }
            key={item.id}
          >
            <div>
              <h3>{item.name}</h3>
              <p className="muted">
                Part: {item.part_number} · Status:{" "}
                {getOrderItemStatusLabel(item.item_status)}
              </p>
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

      <div className="support-box" ref={supportRef}>
        <div>
          <p className="eyebrow">Support history</p>
          <h2>ოპერატორთან მიმოწერა</h2>
          <p className="muted">
            შეკვეთასთან დაკავშირებული წინა კითხვა-პასუხები აქ ინახება.
          </p>
        </div>

        {order.support_messages.length === 0 ? (
          <p className="muted">მიმოწერა ჯერ არ არის.</p>
        ) : (
          <div className="support-messages">
            {order.support_messages.map((message) => (
              <article
                className={
                  message.id === notification.support_message_id
                    ? "support-message support-message--operator support-message--highlighted"
                    : message.sender_type === "customer"
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

      <div className="notification-footer-note">
        <strong>უსაფრთხოების შენიშვნა</strong>
        <p>
          ეს ლინკი მხოლოდ შეკვეთის და შეტყობინების სანახავად არის. დადასტურება,
          გაუქმება ან ახალი support შეტყობინება სრულ შეკვეთის გვერდზე, ტელეფონის
          დადასტურების შემდეგ შესრულდება.
        </p>

        <Link className="button-link" to={`/orders/${order.order_number}`}>
          შეკვეთის სრული გვერდის გახსნა
        </Link>
      </div>
    </section>
  );
}

export default NotificationLinkPage;
