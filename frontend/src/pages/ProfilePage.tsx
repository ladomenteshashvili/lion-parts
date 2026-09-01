import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { clearProfile, getProfile, saveProfile } from "../api/profile";

function ProfilePage() {
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const profile = getProfile();

    if (profile) {
      setCustomerName(profile.customer_name);
      setCustomerPhone(profile.customer_phone);
    }
  }, []);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!customerName.trim()) {
      setMessage("სახელი აუცილებელია");
      return;
    }

    if (!customerPhone.trim()) {
      setMessage("ტელეფონის ნომერი აუცილებელია");
      return;
    }

    saveProfile({
      customer_name: customerName.trim(),
      customer_phone: customerPhone.trim(),
    });

    setMessage("პროფილი შენახულია");
  }

  function handleLogout() {
    clearProfile();
    setCustomerName("");
    setCustomerPhone("");
    setMessage("პროფილი გასუფთავდა");
  }

  const isLoggedInDemo = Boolean(customerName && customerPhone);

  return (
    <section className="card">
      <p className="eyebrow">პროფილი</p>
      <h1>მომხმარებლის პროფილი</h1>
      <p className="muted">
        ეს არის demo profile. შემდეგ ეტაპზე აქ დაემატება ტელეფონის ნომრით შესვლა და SMS verification.
      </p>

      {isLoggedInDemo && (
        <div className="profile-status">
          <strong>Demo login active</strong>
          <span>{customerName} · {customerPhone}</span>
        </div>
      )}

      <form className="checkout-form" onSubmit={handleSubmit}>
        <label>
          სახელი
          <input
            value={customerName}
            onChange={(event) => setCustomerName(event.target.value)}
            placeholder="მაგ: ლადო"
          />
        </label>

        <label>
          ტელეფონის ნომერი
          <input
            value={customerPhone}
            onChange={(event) => setCustomerPhone(event.target.value)}
            placeholder="მაგ: 599123456"
          />
        </label>

        {message && <p className="form-success">{message}</p>}

        <div className="profile-actions">
          <button type="submit">პროფილის შენახვა</button>
          <button type="button" className="button-secondary" onClick={handleLogout}>
            გასუფთავება
          </button>
        </div>
      </form>
    </section>
  );
}

export default ProfilePage;