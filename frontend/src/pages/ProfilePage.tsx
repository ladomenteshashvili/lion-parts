import { useEffect, useState } from "react";

import LegalEntityProfileForm from "../components/LegalEntityProfileForm";
import PasswordAccountForm from "../components/PasswordAccountForm";
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
        key={
          profile
            ? `phone-${profile.id}-${profile.customer_phone}`
            : "phone-guest"
        }
        initialProfile={profile}
        onVerified={(verifiedProfile) => setProfile(verifiedProfile)}
      />

      <PasswordAccountForm
        key={profile ? `password-${profile.id}` : "password-guest"}
        profile={profile}
        onAuthenticated={(authenticatedProfile) => setProfile(authenticatedProfile)}
        onPasswordUpdated={(updatedProfile) => setProfile(updatedProfile)}
      />

      <LegalEntityProfileForm
        key={profile ? `legal-${profile.id}` : "legal-guest"}
        profile={profile}
        onSaved={(updatedProfile) => setProfile(updatedProfile)}
      />
    </section>
  );
}

export default ProfilePage;
