import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  acknowledgePreparedQuote,
  getPreparedQuote,
  type PreparedQuote,
} from "../api/client";
import { addCartItem, buildCartItemId } from "../api/cart";

function PreparedQuotePage() {
  const { token } = useParams<{ token: string }>();
  const [quote, setQuote] = useState<PreparedQuote | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAcknowledging, setIsAcknowledging] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [isAdded, setIsAdded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      return;
    }

    getPreparedQuote(token)
      .then((data) => setQuote(data))
      .catch(() => setError("შეთავაზება ვერ მოიძებნა ან ჯერ მზად არ არის."))
      .finally(() => setIsLoading(false));
  }, [token]);

  async function handleAcknowledge() {
    if (!token) return;

    setIsAcknowledging(true);
    try {
      setQuote(await acknowledgePreparedQuote(token));
    } catch {
      setError("შეტყობინების დადასტურება ვერ მოხერხდა.");
    } finally {
      setIsAcknowledging(false);
    }
  }

  async function handleAddToCart() {
    if (!quote) return;

    const quoteId = quote.quote_id || `REQUEST-${quote.id}`;
    const partOptionId = quote.part_option_id || `REQUEST-${quote.id}`;
    const cartItemId = buildCartItemId({
      quote_id: quoteId,
      part_option_id: partOptionId,
      part_number: quote.part_number,
    });

    setIsAdding(true);
    setError("");

    try {
      await addCartItem({
        cart_item_id: cartItemId,
        quote_id: quoteId,
        part_option_id: partOptionId,
        part_number: quote.part_number,
        name: quote.name || quote.part_number,
        condition: quote.condition,
        brand: quote.brand,
        availability: quote.availability || "ფასი მზადაა",
        eta_days: quote.eta_days,
        weight_kg: quote.weight_kg ? Number(quote.weight_kg) : null,
        final_price_gel: Number(quote.final_price_gel),
        currency: "GEL",
        note: quote.operator_message,
        customer_notice: "წონა და ფასი გადამოწმებულია ოპერატორის მიერ.",
        weight_source: "operator",
        quantity: 1,
      });
      setIsAdded(true);
      window.dispatchEvent(new Event("lion-parts-cart-updated"));
    } catch {
      setError("კალათაში დამატება ვერ მოხერხდა.");
    } finally {
      setIsAdding(false);
    }
  }

  if (!token) {
    return (
      <section className="card">
        <h1>შეთავაზების ლინკი არასწორია</h1>
        <Link className="button-link" to="/">მთავარ გვერდზე დაბრუნება</Link>
      </section>
    );
  }

  if (isLoading) {
    return <section className="card"><p>იტვირთება...</p></section>;
  }

  if (!quote) {
    return (
      <section className="card">
        <h1>შეთავაზება ვერ მოიძებნა</h1>
        <p className="form-error">{error}</p>
        <Link className="button-link" to="/">მთავარ გვერდზე დაბრუნება</Link>
      </section>
    );
  }

  return (
    <section className="card prepared-quote-page">
      <p className="eyebrow">ფასი მზადაა</p>
      <h1>ნაწილის ფასი მომზადებულია</h1>
      <p className="muted">
        ოპერატორმა გადაამოწმა ნაწილის წონა და მოამზადა საბოლოო ფასი.
      </p>

      {!quote.is_acknowledged && (
        <div className="action-required-card">
          <strong>ახალი შეთავაზება</strong>
          <p>{quote.operator_message || "ფასი მზადაა სანახავად."}</p>
          <button
            type="button"
            onClick={handleAcknowledge}
            disabled={isAcknowledging}
          >
            {isAcknowledging ? "ინიშნება..." : "გასაგებია"}
          </button>
        </div>
      )}

      <article className="part-option prepared-quote-card">
        <div>
          <h2>{quote.name || quote.part_number}</h2>
          <p className="muted">
            Part number: {quote.part_number}
            {quote.vin ? ` · VIN: ${quote.vin}` : ""}
          </p>
          <p className="muted">
            {[quote.brand, quote.condition].filter(Boolean).join(" · ")}
            {quote.eta_days !== null ? ` · ETA: ${quote.eta_days} დღე` : ""}
          </p>
          {quote.operator_message && <p>{quote.operator_message}</p>}
        </div>

        <div className="part-option__side">
          <span className="availability availability--ready">ფასი მზადაა</span>
          {quote.weight_kg && <span>წონა: {Number(quote.weight_kg)} კგ</span>}
          <div className="part-price-box">
            <span>საბოლოო ფასი</span>
            <strong>{Number(quote.final_price_gel).toLocaleString("ka-GE")} ₾</strong>
          </div>

          {isAdded ? (
            <Link className="button-link" to="/cart">კალათის გახსნა</Link>
          ) : (
            <button type="button" onClick={handleAddToCart} disabled={isAdding}>
              {isAdding ? "ემატება..." : "კალათაში დამატება"}
            </button>
          )}
        </div>
      </article>

      {error && <p className="form-error">{error}</p>}
    </section>
  );
}

export default PreparedQuotePage;
