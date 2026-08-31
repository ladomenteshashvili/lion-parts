import { useEffect, useState } from "react";
import { getHealthStatus } from "../api/client";

type HealthStatus = {
  status: string;
  service: string;
};

function SearchPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthError, setHealthError] = useState("");

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

  return (
    <section className="card">
      <p className="eyebrow">ნაწილების ძიება</p>
      <h1>მოძებნე ნაწილი part number-ით</h1>
      <p className="muted">
        შეიყვანე OEM part number. სურვილის შემთხვევაში დაამატე VIN, რომ ოპერატორმა თავსებადობა გადაამოწმოს.
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

      <div className="search-form">
        <input placeholder="მაგ: 51118070648" />
        <input placeholder="VIN — არასავალდებულო" />
        <button>ძებნა</button>
      </div>
    </section>
  );
}

export default SearchPage;