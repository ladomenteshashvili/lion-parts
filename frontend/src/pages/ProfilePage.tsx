import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { getProfile, saveProfile } from "../api/profile";

function ProfilePage() {
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [isPhoneVerified, setIsPhoneVerified] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    getProfile()
      .then((profile) => {
        if (profile) {
          setCustomerName(profile.customer_name);
          setCustomerPhone(profile.customer_phone);
          setIsPhoneVerified(profile.is_phone_verified);
        }
      })
      .catch(() => {
        setMessage("პროფილის ჩატვირთვა ვერ მოხერხდა");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!customerName.trim()) {
      setMessage("სახელი აუცილებელია");
      return;
    }

    if (!customerPhone.trim()) {
      setMessage("ტელეფონის ნომერი აუცილებელია");
      return;
    }

    setIsSaving(true);
    setMessage("");

    try {
      const profile = await saveProfile({
        customer_name: customerName.trim(),
        customer_phone: customerPhone.trim(),
      });

      setCustomerName(profile.customer_name);
      setCustomerPhone(profile.customer_phone);
      setIsPhoneVerified(profile.is_phone_verified);
      setMessage("პროფილი შენახულია backend-ში");
    } catch {
      setMessage("პროფილის შენახვა ვერ მოხერხდა");
    } finally {
      setIsSaving(false);
    }
  }

  const hasProfile = Boolean(customerName && customerPhone);

  if (isLoading) {
    return (
      <section className="card">
        <p className="eyebrow">პროფილი</p>
        <h1>იტვირთება...</h1>
      </section>
    );
  }

  return (
    <section className="card">
      <p className="eyebrow">პროფილი</p>
      <h1>მომხმარებლის პროფილი</h1>
      <p className="muted">
        ეს არის backend profile skeleton. შემდეგ ეტაპზე აქ დაემატება SMS verification.
      </p>

      {hasProfile && (
        <div className="profile-status">
          <strong>Profile saved</strong>
          <span>{customerName} · {customerPhone}</span>
          <span>
            Phone verification: {isPhoneVerified ? "Verified" : "Not verified"}
          </span>
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
          <button type="submit" disabled={isSaving}>
            {isSaving ? "ინახება..." : "პროფილის შენახვა"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default ProfilePage;