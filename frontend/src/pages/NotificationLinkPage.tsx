import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  acknowledgeCustomerNotification,
  getCustomerNotification,
  type CustomerNotification,
  type OrderItem,
  type OrderSupportMessage,
} from "../api/orders";
import { getOrderStatusLabel } from "../utils/orderStatus";
import { getOrderItemStatusLabel } from "../utils/orderItemStatus";
import { formatDateKa } from "../utils/dateFormat";

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
      description:
        "ლინკი გაიგზავნა იმიტომ, რომ ოპერატორმა ამ შეკვეთაზე შეტყობინება დატოვა.",
    };
  }

  if (state === "action_required") {
    return {
      eyebrow: "საჭიროა მოქმედება",
      title: "ამ ნაწილზე საჭიროა თქვენი პასუხი",
      description:
        "ლინკი გაიგზავნა იმიტომ, რომ შეკვეთის გაგრძელებამდე ამ ნაწილზე ცვლილებაა დასადასტურებელი.",
    };
  }

  if (state === "item_update") {
    return {
      eyebrow: "ნაწილის განახლება",
      title: "შეკვეთაში ერთ ნაწილზე განახლებაა",
      description:
        "ლინკი გაიგზავნა იმიტომ, რომ კონკრეტული ნაწილის სტატუსი ან ინფორმაცია შეიცვალა.",
    };
  }

  return {
    eyebrow: "შეკვეთის განახლება",
    title: "თქვენი შეკვეთა განახლდა",
    description:
      "ლინკი გაიგზავნა იმიტომ, რომ შეკვეთის სტატუსი ან ინფორმაცია შეიცვალა.",
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

function NotificationLinkPage() {
  const { token } = useParams<{ token: string }>();

  const [notification, setNotification] =
    useState<CustomerNotification | null>(null);
  const [selectedItem, setSelectedItem] = useState<OrderItem | null>(null);
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
        const state = getNotificationViewState(data);

        setNotification(data);
        setError("");

        if (
          (state === "item_update" || state === "action_required") &&
          data.item_id
        ) {
          const item = data.order.items.find((orderItem) => {
            return orderItem.id === data.item_id;
          });

          setSelectedItem(item || null);
        }
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
    if (!notification) {
      return;
    }

    const state = getNotificationViewState(notification);

    if (state === "support_reply") {
      window.setTimeout(() => {
        supportRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }, 200);
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

  function getHighlightedSupportMessage(): OrderSupportMessage | null {
    if (!notification?.support_message_id) {
      return null;
    }

    return (
      notification.order.support_messages.find((message) => {
        return message.id === notification.support_message_id;
      }) || null
    );
  }

  function renderForcedMessage(notificationData: CustomerNotification) {
    const state = getNotificationViewState(notificationData);
    const copy = getStateCopy(state);
    const supportMessage = getHighlightedSupportMessage();

    return (
      <div className="forced-message-backdrop" role="dialog" aria-modal="true">
        <div className="forced-message-modal">
          <p className="eyebrow">{copy.eyebrow}</p>
          <h1>{notificationData.title || copy.title}</h1>

          <p>
            {notificationData.message ||
              supportMessage?.message ||
              copy.description}
          </p>

          {notificationData.item_part_number && (
            <p className="muted">Part: {notificationData.item_part_number}</p>
          )}

          <p className="muted">
            ამ შეტყობინების დახურვა მხოლოდ “გასაგებია” ღილაკით შეიძლება.
          </p>

          <button
            type="button"
            onClick={handleAcknowledge}
            disabled={isAcknowledging}
          >
            {isAcknowledging ? "მუშავდება..." : "გასაგებია"}
          </button>
        </div>
      </div>
    );
  }

  function renderItemModal(item: OrderItem) {
    const isActionRequired = item.action_required;
    const hasAlternative = Boolean(item.proposed_part_number);
    const hasPriceChange = Boolean(item.proposed_final_price_gel);
    const hasEtaChange = Boolean(item.proposed_eta_days);

    return (
      <div className="notification-item-modal-backdrop">
        <div className="notification-item-modal">
          <div className="notification-item-modal__header">
            <div>
              <p className="eyebrow">
                {isActionRequired ? "საჭიროა მოქმედება" : "ნაწილის განახლება"}
              </p>
              <h2>{item.name}</h2>
              <p className="muted">Part: {item.part_number}</p>
            </div>

            <button
              type="button"
              className="button-secondary"
              onClick={() => setSelectedItem(null)}
            >
              დახურვა
            </button>
          </div>

          <div className="notification-change-grid">
            <span>სტატუსი</span>
            <strong>{getOrderItemStatusLabel(item.item_status)}</strong>

            <span>მიმდინარე ფასი</span>
            <strong>{formatGel(item.final_price_gel)}</strong>

            <span>მიმდინარე ETA</span>
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
            <div className="customer-notice">
              <strong>ოპერატორის შეტყობინება</strong>
              <p>{item.action_message}</p>
            </div>
          )}

          {isActionRequired && (
            <div className="action-required-card">
              <strong>ამ ლინკით შეგიძლიათ ცვლილების ნახვა.</strong>
              <span>
                დადასტურება ან გაუქმება ფინანსურად მნიშვნელოვანია, ამიტომ ეს
                მოქმედება მხოლოდ ტელეფონის დადასტურების შემდეგ იქნება შესაძლებელი.
              </span>
              <Link
                className="button-link"
                to={`/orders/${notification?.order.order_number}`}
              >
                ტელეფონით შესვლა და მოქმედების შესრულება
              </Link>
            </div>
          )}
        </div>
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
  const highlightedSupportMessage = getHighlightedSupportMessage();

  return (
    <section className="card notification-deeplink-page">
      {!notification.is_read_by_customer && renderForcedMessage(notification)}

      {selectedItem && renderItemModal(selectedItem)}

      <div className="notification-hero">
        <div>
          <p className="eyebrow">{copy.eyebrow}</p>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
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

      {state === "support_reply" && highlightedSupportMessage && (
        <div className="action-required-card">
          <strong>ოპერატორის პასუხი</strong>
          <span>{highlightedSupportMessage.message}</span>
        </div>
      )}

      {(state === "item_update" || state === "action_required") &&
        notification.item_id && (
          <div className="action-required-card">
            <strong>
              {state === "action_required"
                ? "ამ ნაწილზე საჭიროა თქვენი მოქმედება"
                : "ამ ნაწილზე განახლებაა"}
            </strong>
            <span>
              Part: {notification.item_part_number || "—"}
            </span>
            <button
              type="button"
              onClick={() => {
                const item = order.items.find((orderItem) => {
                  return orderItem.id === notification.item_id;
                });

                setSelectedItem(item || null);
              }}
            >
              ნაწილის დეტალების გახსნა
            </button>
          </div>
        )}

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

              {item.action_required && (
                <p className="customer-notice">
                  ამ ნაწილზე საჭიროა მომხმარებლის მოქმედება.
                </p>
              )}
            </div>

            <div className="part-option__side">
              <strong>
                {(Number(item.final_price_gel) * item.quantity).toLocaleString(
                  "ka-GE"
                )}{" "}
                ₾
              </strong>

              <button type="button" onClick={() => setSelectedItem(item)}>
                დეტალები
              </button>
            </div>
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
          გაუქმება ან ახალი support შეტყობინება მხოლოდ ტელეფონის დადასტურების
          შემდეგ შესრულდება.
        </p>

        <Link className="button-link" to={`/orders/${order.order_number}`}>
          შეკვეთის სრული გვერდის გახსნა
        </Link>
      </div>
    </section>
  );
}

export default NotificationLinkPage;
