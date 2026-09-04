import { useEffect, useState } from "react";

import PhoneVerificationForm from "../components/PhoneVerificationForm";
import { getProfile, type CustomerProfile } from "../api/profile";

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

      <PhoneVerificationForm
        initialProfile={profile}
        onVerified={(verifiedProfile) => setProfile(verifiedProfile)}
      />
    </section>
  );
}

export default ProfilePage;
