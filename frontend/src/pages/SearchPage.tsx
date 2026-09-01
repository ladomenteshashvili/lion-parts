import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { getHealthStatus, searchParts } from "../api/client";
import type { PartSearchResponse } from "../api/client";
import { addCartItem, buildCartItemId } from "../api/cart";

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
    setAddedCartItemIds([]);

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

  async function handleAddToCart(item: PartSearchResponse["results"][number]) {
    if (!quote) {
      return;
    }

    const cartItemId = buildCartItemId({
      quote_id: quote.quote_id,
      part_option_id: item.part_option_id,
      part_number: quote.part_number,
    });

    try {
      await addCartItem({
        ...item,
        cart_item_id: cartItemId,
        quote_id: quote.quote_id,
        part_number: quote.part_number,
        final_price_gel: Number(item.final_price_gel),
        quantity: 1,
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
                  <span className={isInCart ? "availability availability--cart" : "availability"}>
                    {isInCart ? "კალათაშია" : item.availability}
                  </span>

                  <strong>
                    {Number(item.final_price_gel).toLocaleString("ka-GE")} ₾
                  </strong>

                  <button
                    type="button"
                    onClick={() => handleAddToCart(item)}
                    disabled={isInCart}
                  >
                    {isInCart ? "დამატებულია" : "კალათაში დამატება"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default SearchPage;