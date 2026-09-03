import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  createPartQuoteRequest,
  getHealthStatus,
  searchParts,
} from "../api/client";
import type { PartSearchResponse } from "../api/client";
import { addCartItem, buildCartItemId, getSessionId } from "../api/cart";
import { getProfile } from "../api/profile";
type HealthStatus = {
  status: string;
  service: string;
};

function SearchPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthError, setHealthError] = useState("");

  const [partNumber, setPartNumber] = useState("");
  const [vin, setVin] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [cartMessage, setCartMessage] = useState("");
  const [quote, setQuote] = useState<PartSearchResponse | null>(null);
  const [addedCartItemIds, setAddedCartItemIds] = useState<string[]>([]);
  const [quantitiesByCartItemId, setQuantitiesByCartItemId] = useState<
    Record<string, number>
  >({});

  const [quoteRequestName, setQuoteRequestName] = useState("");
  const [quoteRequestPhone, setQuoteRequestPhone] = useState("");
  const [quoteRequestComment, setQuoteRequestComment] = useState("");
  const [isQuoteRequestSubmitting, setIsQuoteRequestSubmitting] =
    useState(false);
  const [quoteRequestMessage, setQuoteRequestMessage] = useState("");
  const [quoteRequestError, setQuoteRequestError] = useState("");
  const [canRequestQuote, setCanRequestQuote] = useState(false);

  useEffect(() => {
    getHealthStatus()
      .then((data) => {
        setHealth(data);
        setHealthError("");
      })
      .catch(() => {
        setHealth(null);
        setHealthError("Backend connection failed");
      });
  }, []);


    useEffect(() => {
    getProfile()
      .then((profile) => {
        if (!profile) {
          setCanRequestQuote(false);
          return;
        }

        setCanRequestQuote(profile.can_request_quote);
        setQuoteRequestName(profile.customer_name);
        setQuoteRequestPhone(profile.customer_phone);
      })
      .catch(() => {
        setCanRequestQuote(false);
      });
  }, []);


  async function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const cleanPartNumber = partNumber.trim();
    const cleanVin = vin.trim();

    if (!cleanPartNumber) {
      setSearchError("Part number აუცილებელია");
      setQuote(null);
      return;
    }

    setIsSearching(true);
    setSearchError("");
    setCartMessage("");
    setQuoteRequestMessage("");
    setQuoteRequestError("");
    setAddedCartItemIds([]);
    setQuantitiesByCartItemId({});

    try {
      const data = await searchParts({
        part_number: cleanPartNumber,
        vin: cleanVin || undefined,
      });

      setQuote(data);
    } catch {
      setQuote(null);
      setSearchError("ძიება ვერ შესრულდა. სცადე თავიდან.");
    } finally {
      setIsSearching(false);
    }
  }

  async function handleAddToCart(
    item: PartSearchResponse["results"][number],
    quantity: number
  ) {
    const currentQuote = quote;

    if (!currentQuote) {
      return;
    }

    const cartItemId = buildCartItemId({
      quote_id: currentQuote.quote_id,
      part_option_id: item.part_option_id,
      part_number: currentQuote.part_number,
    });

    try {
      await addCartItem({
        ...item,
        cart_item_id: cartItemId,
        quote_id: currentQuote.quote_id,
        part_number: currentQuote.part_number,
        final_price_gel: Number(item.final_price_gel),
        quantity,
      });

      window.dispatchEvent(new Event("lion-parts-cart-updated"));

      setSearchError("");
      setCartMessage("ნაწილი დაემატა კალათაში");
      setAddedCartItemIds((currentIds) => [...currentIds, cartItemId]);
    } catch (error) {
      console.error("Add to cart failed", error);
      setCartMessage("");
      setSearchError("კალათაში დამატება ვერ მოხერხდა");
    }
  }

  async function handleQuoteRequestSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

        if (!canRequestQuote) {
      setQuoteRequestError(
        "შეთავაზების მოთხოვნა თქვენს ანგარიშზე ჩართული არ არის"
      );
      return;
    }

    const cleanPartNumber = partNumber.trim();
    const cleanVin = vin.trim();
    const cleanPhone = quoteRequestPhone.trim();

    if (!cleanPartNumber) {
      setQuoteRequestError("Part number აუცილებელია");
      return;
    }

    if (!cleanPhone) {
      setQuoteRequestError("ტელეფონის ნომერი აუცილებელია");
      return;
    }

    setIsQuoteRequestSubmitting(true);
    setQuoteRequestError("");
    setQuoteRequestMessage("");

    try {
      const request = await createPartQuoteRequest({
        session_id: getSessionId(),
        part_number: cleanPartNumber,
        vin: cleanVin || undefined,
        customer_name: quoteRequestName.trim(),
        customer_phone: cleanPhone,
        comment: quoteRequestComment.trim(),
      });

      setQuoteRequestMessage(
        `მოთხოვნა მიღებულია. Request #${request.id}. ოპერატორი გადაამოწმებს და დაგიკავშირდებათ.`
      );
      setQuoteRequestComment("");
    } catch (error) {
      console.error("Quote request failed", error);
      setQuoteRequestError("მოთხოვნის გაგზავნა ვერ მოხერხდა");
    } finally {
      setIsQuoteRequestSubmitting(false);
    }
  }

  function getQuantity(cartItemId: string) {
    return quantitiesByCartItemId[cartItemId] ?? 1;
  }

  function handleQuantityChange(cartItemId: string, value: string) {
    const parsedQuantity = Number(value);

    const nextQuantity =
      Number.isFinite(parsedQuantity) && parsedQuantity > 0
        ? Math.floor(parsedQuantity)
        : 1;

    setQuantitiesByCartItemId((currentQuantities) => ({
      ...currentQuantities,
      [cartItemId]: nextQuantity,
    }));
  }

  return (
    <section className="card">
      <p className="eyebrow">ნაწილების ძიება</p>
      <h1>მოძებნე ნაწილი part number-ით</h1>
      <p className="muted">
        შეიყვანე OEM part number. სურვილის შემთხვევაში დაამატე VIN, რომ
        ოპერატორმა თავსებადობა გადაამოწმოს.
      </p>

      <div className="status-box">
        {health ? (
          <span>Backend status: {health.status}</span>
        ) : healthError ? (
          <span className="status-box__error">{healthError}</span>
        ) : (
          <span>Checking backend...</span>
        )}
      </div>

      <form className="search-form" onSubmit={handleSearch}>
        <input
          value={partNumber}
          onChange={(event) => setPartNumber(event.target.value)}
          placeholder="მაგ: 51118070648"
        />

        <input
          value={vin}
          onChange={(event) => setVin(event.target.value)}
          placeholder="VIN — არასავალდებულო"
        />

        <button type="submit" disabled={isSearching}>
          {isSearching ? "იძებნება..." : "ძებნა"}
        </button>
      </form>

      {searchError && <p className="form-error">{searchError}</p>}
      {cartMessage && <p className="form-success">{cartMessage}</p>}

      {quote && (
        <div className="quote">
          <div className="quote__header">
            <div>
              <p className="eyebrow">შეთავაზება</p>
              <h2>Quote #{quote.quote_id}</h2>
              <p className="muted">
                Part number: {quote.part_number}
                {quote.vin ? ` · VIN: ${quote.vin}` : ""}
              </p>
            </div>
          </div>

          {quote.results.map((item) => {
            const cartItemId = buildCartItemId({
              quote_id: quote.quote_id,
              part_option_id: item.part_option_id,
              part_number: quote.part_number,
            });

            const isInCart = addedCartItemIds.includes(cartItemId);
            const quantity = getQuantity(cartItemId);
            const hasFinalPrice = item.final_price_gel !== null;
            const needsWeight = item.requires_weight_input === true;
            const canAddToCart = !isInCart && hasFinalPrice && !needsWeight;
            const lineTotalGel = hasFinalPrice
              ? Number(item.final_price_gel) * quantity
              : 0;

            return (
              <article className="part-option" key={cartItemId}>
                <div>
                  <h3>{item.name}</h3>

                  <p className="muted">
                    {item.brand} · {item.condition} · ETA: {item.eta_days} დღე
                  </p>

                  <p className="muted">{item.note}</p>
                </div>

                <div className="part-option__side">
                  <span
                    className={
                      isInCart
                        ? "availability availability--cart"
                        : "availability"
                    }
                  >
                    {isInCart ? "კალათაშია" : item.availability}
                  </span>

                  <div className="part-price-box">
                    <span>ერთეულის ფასი</span>
                    <strong>
                      {hasFinalPrice
                        ? `${Number(item.final_price_gel).toLocaleString("ka-GE")} ₾`
                        : "წონა საჭიროა"}
                    </strong>

                    {quantity > 1 && (
                      <>
                        <span>ჯამი</span>
                        <strong>{lineTotalGel.toLocaleString("ka-GE")} ₾</strong>
                      </>
                    )}
                  </div>

                  <label className="part-quantity">
                    <span>რაოდენობა</span>
                    <input
                      type="number"
                      min="1"
                      value={quantity}
                      onChange={(event) =>
                        handleQuantityChange(cartItemId, event.target.value)
                      }
                      disabled={isInCart}
                    />
                  </label>

                  <button
                    type="button"
                    onClick={() => handleAddToCart(item, quantity)}
                    disabled={!canAddToCart}
                  >
                    {isInCart
                      ? "დამატებულია"
                      : needsWeight
                        ? "საჭიროა წონა"
                        : "კალათაში დამატება"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

            {canRequestQuote ? (
        <div className="quote-request-box">
          <div>
            <p className="eyebrow">ვერ იპოვე სასურველი ვარიანტი?</p>
            <h2>მოითხოვე შეთავაზება ოპერატორისგან</h2>
            <p className="muted">
              თუ ძიებაში არ ჩანს სწორი ნაწილი, ფასი არ გაწყობს ან VIN-ით
              გადამოწმება გჭირდება, დატოვე მოთხოვნა და ოპერატორი გადაამოწმებს.
            </p>
          </div>

          <form
            className="quote-request-form"
            onSubmit={handleQuoteRequestSubmit}
          >
            <input
              value={quoteRequestName}
              onChange={(event) => setQuoteRequestName(event.target.value)}
              placeholder="სახელი — არასავალდებულო"
            />

            <input
              value={quoteRequestPhone}
              onChange={(event) => setQuoteRequestPhone(event.target.value)}
              placeholder="ტელეფონი"
            />

            <textarea
              className="quote-request-form__full"
              value={quoteRequestComment}
              onChange={(event) => setQuoteRequestComment(event.target.value)}
              placeholder="კომენტარი — მაგ: მინდა მხოლოდ ორიგინალი, მარჯვენა მხარე, ფერი შავი..."
            />

            {quoteRequestError && (
              <p className="form-error quote-request-form__full">
                {quoteRequestError}
              </p>
            )}

            {quoteRequestMessage && (
              <p className="form-success quote-request-form__full">
                {quoteRequestMessage}
              </p>
            )}

            <button
              className="quote-request-form__full"
              type="submit"
              disabled={isQuoteRequestSubmitting}
            >
              {isQuoteRequestSubmitting
                ? "იგზავნება..."
                : "შეთავაზების მოთხოვნა"}
            </button>
          </form>
        </div>
      ) : (
        <div className="quote-request-box quote-request-box--locked">
          <p className="eyebrow">შეთავაზების მოთხოვნა</p>
          <h2>ეს ფუნქცია ჩართულია მხოლოდ შერჩეულ მომხმარებლებზე</h2>
          <p className="muted">
            თუ ხშირად უკვეთავთ ნაწილებს ან გჭირდებათ ოპერატორის ხელით ძიება,
            შეგვიძლია ეს ფუნქცია თქვენს ანგარიშზე ჩავრთოთ.
          </p>
        </div>
      )}
    </section>
  );
}

export default SearchPage;