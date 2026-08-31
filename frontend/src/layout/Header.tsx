import { NavLink } from "react-router-dom";

function Header() {
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
          კალათა
        </NavLink>
        <NavLink to="/orders">
          შეკვეთები
        </NavLink>
        <NavLink to="/profile">
          პროფილი
        </NavLink>
      </nav>
    </header>
  );
}

export default Header;