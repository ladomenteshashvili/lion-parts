import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { getCart } from "../api/cart";
import { getOrders, type BackendOrder } from "../api/orders";

function countActionRequiredItems(orders: BackendOrder[]) {
  return orders.reduce(
    (sum, order) =>
      sum + order.items.filter((item) => item.action_required).length,
    0
  );
}

function Header() {
  const [cartCount, setCartCount] = useState(0);
  const [ordersCount, setOrdersCount] = useState(0);
  const [actionRequiredCount, setActionRequiredCount] = useState(0);

  async function loadCounts() {
    try {
      const cart = await getCart();
      const count = cart.items.reduce((sum, item) => sum + item.quantity, 0);
      setCartCount(count);
    } catch {
      setCartCount(0);
    }

    try {
      const orders = await getOrders();
      setOrdersCount(orders.length);
      setActionRequiredCount(countActionRequiredItems(orders));
    } catch {
      setOrdersCount(0);
      setActionRequiredCount(0);
    }
  }

  useEffect(() => {
    loadCounts();

    window.addEventListener("lion-parts-cart-updated", loadCounts);
    window.addEventListener("lion-parts-orders-updated", loadCounts);

    return () => {
      window.removeEventListener("lion-parts-cart-updated", loadCounts);
      window.removeEventListener("lion-parts-orders-updated", loadCounts);
    };
  }, []);

  return (
    <header className="header">
      <div className="header__brand">
        <div className="header__logo">LP</div>
        <div>
          <div className="header__title">Lion Parts</div>
          <div className="header__subtitle">USA auto parts to Georgia</div>
        </div>
      </div>

      <nav className="header__nav">
        <NavLink to="/" end>
          ძიება
        </NavLink>

        <NavLink to="/cart">
          კალათა {cartCount > 0 ? `(${cartCount})` : ""}
        </NavLink>

        <NavLink to="/orders">
          <span>შეკვეთები {ordersCount > 0 ? `(${ordersCount})` : ""}</span>
          {actionRequiredCount > 0 && (
            <span
              className="nav-alert-badge"
              aria-label={`${actionRequiredCount} საჭირო მოქმედება`}
            >
              {actionRequiredCount}
            </span>
          )}
        </NavLink>

        <NavLink to="/profile">
          პროფილი
        </NavLink>
      </nav>
    </header>
  );
}

export default Header;
