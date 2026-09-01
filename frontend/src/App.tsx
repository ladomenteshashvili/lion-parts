import { Navigate, Route, Routes } from "react-router-dom";
import Header from "./layout/Header";
import SearchPage from "./pages/SearchPage";
import CartPage from "./pages/CartPage";
import CheckoutPage from "./pages/CheckoutPage";
import OrdersPage from "./pages/OrdersPage";
import OrderDetailPage from "./pages/OrderDetailPage";
import ProfilePage from "./pages/ProfilePage";

function App() {
  return (
    <div className="app">
      <Header />

      <main className="page">
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/cart" element={<CartPage />} />
          <Route path="/checkout" element={<CheckoutPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/orders/:orderNumber" element={<OrderDetailPage />} />
          <Route path="/profile" element={<ProfilePage />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;