import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { getCart } from "../api/cart";
import { getOrders } from "../api/orders";

function Header() {
  const [cartCount, setCartCount] = useState(0);
  const [ordersCount, setOrdersCount] = useState(0);

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
    } catch {
      setOrdersCount(0);
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
          შეკვეთები {ordersCount > 0 ? `(${ordersCount})` : ""}
        </NavLink>

        <NavLink to="/profile">
          პროფილი
        </NavLink>
      </nav>
    </header>
  );
}

export default Header;