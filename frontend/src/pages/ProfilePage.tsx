import { useEffect, useState } from "react";

import PhoneVerificationForm from "../components/PhoneVerificationForm";
import { getProfile, type CustomerProfile } from "../api/profile";
import { resetSessionId } from "../api/cart";

function ProfilePage() {
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getProfile()
      .then((loadedProfile) => {
        setProfile(loadedProfile);
        setError("");
      })
      .catch(() => {
        setError("პროფილის ჩატვირთვა ვერ მოხერხდა");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  function handleLogout() {
    resetSessionId();
    setProfile(null);
    setError("");

    window.dispatchEvent(new Event("lion-parts-cart-updated"));
    window.dispatchEvent(new Event("lion-parts-orders-updated"));
  }

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
      <h1>ტელეფონით შესვლა</h1>

      {error && <p className="form-error">{error}</p>}

      {profile && (
        <div className="note-box">
          <strong>გასვლა</strong>
          <p className="muted">
            ამ მოქმედებით ამ ბრაუზერში მიმდინარე სესია დაიხურება. ძველი კალათა
            აღარ გამოჩნდება, ხოლო შეკვეთები ისევ გამოჩნდება იმ ნომრით ხელახლა
            შესვლის შემდეგ, რომელზეც არის მიბმული.
          </p>
          <button
            type="button"
            className="button-secondary"
            onClick={handleLogout}
          >
            გასვლა
          </button>
        </div>
      )}

      <PhoneVerificationForm
        initialProfile={profile}
        onVerified={(verifiedProfile) => setProfile(verifiedProfile)}
      />
    </section>
  );
}

export default ProfilePage;
