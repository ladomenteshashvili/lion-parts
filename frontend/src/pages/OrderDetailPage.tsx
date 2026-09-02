import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getOrderStatusLabel } from "../utils/orderStatus";
import {
  getActionTypeLabel,
  getOrderItemStatusLabel,
} from "../utils/orderItemStatus";

import {
  getOrderDetail,
  resolveOrderItemAction,
  type BackendOrder,
  type OrderItem,
  type OrderItemEvent,
} from "../api/orders";
import { formatDateKa } from "../utils/dateFormat";

function OrderDetailPage() {
  const { orderNumber } = useParams<{ orderNumber: string }>();

  const [order, setOrder] = useState<BackendOrder | null>(null);
  const [selectedItem, setSelectedItem] = useState<OrderItem | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isResolvingAction, setIsResolvingAction] = useState(false);
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

  function isTimelineStepActive(item: OrderItem, stepStatus: string) {
    const statuses = [
      "created",
      "payment_confirmed",
      "checking",
      "action_required",
      "purchased",
      "received_usa",
      "shipped_to_georgia",
      "received_georgia",
      "ready_for_pickup",
      "completed",
    ];

    const currentIndex = statuses.indexOf(item.item_status);
    const stepIndex = statuses.indexOf(stepStatus);

    if (item.item_status === "cancelled") {
      return stepStatus === "cancelled";
    }

    if (currentIndex === -1 || stepIndex === -1) {
      return false;
    }

    return stepIndex <= currentIndex;
  }

function getEventValue(
  value: Record<string, unknown> | null,
  key: string
): string | number | null {
  if (!value || value[key] === undefined || value[key] === null) {
    return null;
  }

  const fieldValue = value[key];

  if (typeof fieldValue === "string" || typeof fieldValue === "number") {
    return fieldValue;
  }

  return String(fieldValue);
}

function renderEventChanges(event: OrderItemEvent) {
  const oldPrice = getEventValue(event.old_value, "final_price_gel");
  const newPrice = getEventValue(event.new_value, "proposed_final_price_gel");

  const oldEta = getEventValue(event.old_value, "eta_days");
  const newEta = getEventValue(event.new_value, "proposed_eta_days");

  const oldDate = getEventValue(event.old_value, "expected_arrival_date");
  const newDate = getEventValue(
    event.new_value,
    "proposed_expected_arrival_date"
  );

  const hasChanges = oldPrice || newPrice || oldEta || newEta || oldDate || newDate;

  if (!hasChanges) {
    return null;
  }

  return (
    <div className="event-change-list">
      {(oldPrice || newPrice) && (
        <div className="event-change-row">
          <span>ფასი</span>
          <strong>
            {oldPrice ? `${Number(oldPrice).toLocaleString("ka-GE")} ₾` : "—"} →{" "}
            {newPrice ? `${Number(newPrice).toLocaleString("ka-GE")} ₾` : "—"}
          </strong>
        </div>
      )}

      {(oldEta || newEta) && (
        <div className="event-change-row">
          <span>ETA</span>
          <strong>
            {oldEta ? `${oldEta} დღე` : "—"} → {newEta ? `${newEta} დღე` : "—"}
          </strong>
        </div>
      )}

      {(oldDate || newDate) && (
        <div className="event-change-row">
          <span>მოსალოდნელი თარიღი</span>
          <strong>
            {oldDate ? formatDateKa(String(oldDate)) : "—"} →{" "}
            {newDate ? formatDateKa(String(newDate)) : "—"}
          </strong>
        </div>
      )}
    </div>
  );
}



  async function handleResolveItemAction() {
  if (!selectedItem) {
    return;
  }

  setIsResolvingAction(true);
  setError("");

  try {
    const updatedOrder = await resolveOrderItemAction(selectedItem.id);

    setOrder(updatedOrder);

    const updatedSelectedItem = updatedOrder.items.find(
      (item) => item.id === selectedItem.id
    );

    setSelectedItem(updatedSelectedItem || null);

    window.dispatchEvent(new Event("lion-parts-orders-updated"));
  } catch (error) {
    console.error("Resolve item action failed", error);
    setError("მოქმედების დადასტურება ვერ მოხერხდა");
  } finally {
    setIsResolvingAction(false);
  }
}


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

  const actionRequiredItems = order.items.filter(
    (item) => item.action_required
  );

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
          <span className="availability">
            {getOrderStatusLabel(order.status)}
          </span>
          <strong>{Number(order.total_gel).toLocaleString("ka-GE")} ₾</strong>
        </div>
      </div>

      {actionRequiredItems.length > 0 && (
        <div className="action-required-card">
          <strong>საჭიროა თქვენი მოქმედება</strong>
          <span>
            {actionRequiredItems.length} ნაწილზე საჭიროა თქვენი პასუხი.
          </span>
          <span className="muted">
            გახსენით შესაბამისი ნაწილი და ნახეთ მიზეზი.
          </span>
        </div>
      )}

      {order.status === "payment_pending" && (
        <div className="payment-demo-box">
          <strong>გადახდა მოსალოდნელია</strong>
          <span>
            Demo რეჟიმში შეკვეთა შეიქმნა Payment pending სტატუსით. რეალურ
            სისტემაში აქ იქნება payment gateway.
          </span>
        </div>
      )}

      <div className="tracking-timeline">
        <h2>შეკვეთის საერთო პროგრესი</h2>

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
            <p className="muted">
              შემდეგ ეტაპზე დაემატება online payment confirmation.
            </p>
          </div>
        </div>

        <div className="timeline-step">
          <span className="timeline-dot" />
          <div>
            <strong>შეკვეთა მუშავდება</strong>
            <p className="muted">
              ეს არის მთლიანი შეკვეთის საერთო სტატუსი. თითოეულ ნაწილს თავისი
              ცალკე სტატუსი აქვს ქვემოთ.
            </p>
          </div>
        </div>

        <div className="timeline-step">
          <span className="timeline-dot" />
          <div>
            <strong>დასრულება</strong>
            <p className="muted">
              შეკვეთა დასრულდება მაშინ, როცა ყველა ნაწილი გაიცემა ან პროცესი
              დაიხურება.
            </p>
          </div>
        </div>
      </div>

      <div className="order-items">
        <h2>ნაწილები</h2>

        {order.items.map((item) => (
          <article className="cart-item" key={item.id}>
            <div>
              <h3>{item.name}</h3>

              <span
                className={
                  item.action_required
                    ? "item-status item-status--action"
                    : "item-status"
                }
              >
                {getOrderItemStatusLabel(item.item_status)}
              </span>

              <p className="muted">
                Part number: {item.part_number} · Quote: {item.quote_id}
              </p>

              <p className="muted">
                {item.brand} · {item.condition} · ETA:{" "}
                {item.eta_days ? `${item.eta_days} დღე` : "მითითებული არ არის"}
              </p>

              <p className="muted">
                მოსალოდნელი ჩამოსვლა: {formatDateKa(item.expected_arrival_date)}
              </p>

              {item.action_required && (
                <div className="item-action-preview">
                  <p className="form-error">
                    საჭიროა მოქმედება: {getActionTypeLabel(item.action_type)}
                    {item.action_message ? ` — ${item.action_message}` : ""}
                  </p>

                  {item.proposed_final_price_gel && (
                    <p className="muted">
                      ახალი ფასი:{" "}
                      <strong>
                        {Number(item.proposed_final_price_gel).toLocaleString("ka-GE")} ₾
                      </strong>
                    </p>
                  )}

                  {item.proposed_eta_days && (
                    <p className="muted">
                      ახალი ETA: <strong>{item.proposed_eta_days} დღე</strong>
                    </p>
                  )}
                </div>
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

              <button type="button" onClick={() => setSelectedItem(item)}>
                დეტალები
              </button>
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

      {selectedItem && (
        <div className="modal-backdrop" onClick={() => setSelectedItem(null)}>
          <div className="modal-card" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div>
                <p className="eyebrow">ნაწილის დეტალები</p>
                <h2>{selectedItem.name}</h2>
                <p className="muted">Part number: {selectedItem.part_number}</p>
              </div>

              <button
                type="button"
                className="modal-close"
                onClick={() => setSelectedItem(null)}
              >
                ×
              </button>
            </div>

            <div className="modal-summary">
              <span
                className={
                  selectedItem.action_required
                    ? "item-status item-status--action"
                    : "item-status"
                }
              >
                {getOrderItemStatusLabel(selectedItem.item_status)}
              </span>

              <strong>
                {(
                  Number(selectedItem.final_price_gel) * selectedItem.quantity
                ).toLocaleString("ka-GE")}{" "}
                ₾
              </strong>
            </div>

            {selectedItem.action_required && (
              <div className="action-required-card">
                <strong>საჭიროა თქვენი მოქმედება</strong>
                <span>{getActionTypeLabel(selectedItem.action_type)}</span>

                {selectedItem.action_message && (
                  <span className="muted">{selectedItem.action_message}</span>
                )}

                {(selectedItem.proposed_final_price_gel ||
                  selectedItem.proposed_eta_days) && (
                  <div className="proposed-changes">
                    {selectedItem.proposed_final_price_gel && (
                      <div className="proposed-change-row">
                        <span>არსებული ფასი</span>
                        <strong>
                          {Number(selectedItem.final_price_gel).toLocaleString("ka-GE")} ₾
                        </strong>
                        <span>ახალი ფასი</span>
                        <strong>
                          {Number(selectedItem.proposed_final_price_gel).toLocaleString(
                            "ka-GE"
                          )}{" "}
                          ₾
                        </strong>
                      </div>
                    )}

                    {selectedItem.proposed_eta_days && (
                      <div className="proposed-change-row">
                        <span>არსებული ETA</span>
                        <strong>
                          {selectedItem.eta_days
                            ? `${selectedItem.eta_days} დღე`
                            : "მითითებული არ არის"}
                        </strong>

                        <span>არსებული მოსალოდნელი თარიღი</span>
                        <strong>{formatDateKa(selectedItem.expected_arrival_date)}</strong>

                        <span>ახალი ETA</span>
                        <strong>{selectedItem.proposed_eta_days} დღე</strong>

                        <span>ახალი მოსალოდნელი თარიღი</span>
                        <strong>
                          {formatDateKa(selectedItem.proposed_expected_arrival_date)}
                        </strong>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="tracking-timeline tracking-timeline--modal">
              <h3>ნაწილის პროგრესი</h3>

              <div
                className={
                  isTimelineStepActive(selectedItem, "created")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>ნაწილი შეკვეთაში დაემატა</strong>
                  <p className="muted">ნაწილი დაფიქსირდა ამ შეკვეთაში.</p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "payment_confirmed")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>გადახდა დადასტურებულია</strong>
                  <p className="muted">
                    ამ ეტაპის შემდეგ იწყება ნაწილის დამუშავება.
                  </p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "checking")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>მოწმდება</strong>
                  <p className="muted">
                    ოპერატორი ამოწმებს availability, ETA, წონას და თავსებადობას.
                  </p>
                </div>
              </div>

              {selectedItem.action_required && (
                <div className="timeline-step timeline-step--warning">
                  <span className="timeline-dot" />
                  <div>
                    <strong>საჭიროა მომხმარებლის პასუხი</strong>
                    <p className="muted">
                      გადაწყვეტილების მიღების შემდეგ პროცესი გაგრძელდება.
                    </p>
                  </div>
                </div>
              )}

              <div
                className={
                  isTimelineStepActive(selectedItem, "purchased")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>ნაწილი შეძენილია</strong>
                  <p className="muted">
                    ნაწილი შეძენილია მომწოდებელთან.
                  </p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "received_usa")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>მიღებულია აშშ-ში</strong>
                  <p className="muted">
                    ნაწილი მივიდა ამერიკის საწყობში.
                  </p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "shipped_to_georgia")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>გამოგზავნილია საქართველოში</strong>
                  <p className="muted">
                    ნაწილი გზაშია საქართველოში.
                  </p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "received_georgia")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>ჩამოსულია საქართველოში</strong>
                  <p className="muted">
                    ნაწილი მიღებულია საქართველოში.
                  </p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "ready_for_pickup")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>მზადაა გასაცემად</strong>
                  <p className="muted">
                    მომხმარებელს შეუძლია ნაწილის მიღება.
                  </p>
                </div>
              </div>

              <div
                className={
                  isTimelineStepActive(selectedItem, "completed")
                    ? "timeline-step timeline-step--active"
                    : "timeline-step"
                }
              >
                <span className="timeline-dot" />
                <div>
                  <strong>დასრულებულია</strong>
                  <p className="muted">
                    ამ ნაწილზე პროცესი დასრულებულია.
                  </p>
                </div>
              </div>
            </div>


            <div className="item-history">
              <h3>ნაწილის ისტორია</h3>

              {selectedItem.events.length === 0 ? (
                <p className="muted">ისტორია ჯერ არ არის.</p>
              ) : (
                <div className="item-history-list">
                  {selectedItem.events.map((event) => (
                    <article className="item-history-entry" key={event.id}>
                      <div>
                        <strong>{event.title}</strong>
                        <p className="muted">
                          {new Date(event.created_at).toLocaleString("ka-GE")}
                          {event.actor_name ? ` · ${event.actor_name}` : ""}
                        </p>
                      </div>

                      {event.message && <p>{event.message}</p>}

                    {renderEventChanges(event)}
                    </article>
                  ))}
                </div>
              )}
            </div>

            <div className="modal-actions">
              {selectedItem.action_required && (
                <button
                  type="button"
                  onClick={handleResolveItemAction}
                  disabled={isResolvingAction}
                >
                  {isResolvingAction ? "მუშავდება..." : "დადასტურება"}
                </button>
              )}

              <button
                type="button"
                className="button-secondary"
                onClick={() => setSelectedItem(null)}
              >
                დახურვა
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default OrderDetailPage;