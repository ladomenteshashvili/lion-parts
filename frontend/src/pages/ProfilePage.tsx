import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  getProfile,
  sendPhoneVerificationCode,
  verifyPhoneCode,
} from "../api/profile";

type FeedbackType = "success" | "error" | "info";

function ProfilePage() {
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [verificationCode, setVerificationCode] = useState("");

  const [isPhoneVerified, setIsPhoneVerified] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSendingCode, setIsSendingCode] = useState(false);
  const [isVerifyingCode, setIsVerifyingCode] = useState(false);

  const [isCodeSent, setIsCodeSent] = useState(false);
  const [demoCode, setDemoCode] = useState("");
  const [expiresInSeconds, setExpiresInSeconds] = useState<number | null>(null);

  const [feedbackType, setFeedbackType] = useState<FeedbackType>("info");
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
        showFeedback("error", "პროფილის ჩატვირთვა ვერ მოხერხდა");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  function showFeedback(type: FeedbackType, text: string) {
    setFeedbackType(type);
    setMessage(text);
  }

  function resetVerificationState() {
    setIsPhoneVerified(false);
    setIsCodeSent(false);
    setVerificationCode("");
    setDemoCode("");
    setExpiresInSeconds(null);
  }

  async function handleSendCode() {
    if (!customerPhone.trim()) {
      showFeedback("error", "ტელეფონის ნომერი აუცილებელია");
      return;
    }

    setIsSendingCode(true);
    setMessage("");
    setVerificationCode("");
    setDemoCode("");
    setExpiresInSeconds(null);

    try {
      const response = await sendPhoneVerificationCode({
        customer_phone: customerPhone.trim(),
      });

      setIsCodeSent(true);
      setExpiresInSeconds(response.expires_in_seconds || null);

      if (response.demo_code) {
        setDemoCode(response.demo_code);
      }

      showFeedback(
        "success",
        "SMS კოდი გაგზავნილია. შეიყვანეთ მიღებული კოდი დასადასტურებლად."
      );
    } catch (error) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : "SMS კოდის გაგზავნა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsSendingCode(false);
    }
  }

  async function handleVerifyCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!customerName.trim()) {
      showFeedback("error", "სახელი აუცილებელია");
      return;
    }

    if (!customerPhone.trim()) {
      showFeedback("error", "ტელეფონის ნომერი აუცილებელია");
      return;
    }

    if (!verificationCode.trim()) {
      showFeedback("error", "SMS კოდი აუცილებელია");
      return;
    }

    setIsVerifyingCode(true);
    setMessage("");

    try {
      const profile = await verifyPhoneCode({
        customer_name: customerName.trim(),
        customer_phone: customerPhone.trim(),
        code: verificationCode.trim(),
      });

      setCustomerName(profile.customer_name);
      setCustomerPhone(profile.customer_phone);
      setIsPhoneVerified(profile.is_phone_verified);
      setIsCodeSent(false);
      setVerificationCode("");
      setDemoCode("");
      setExpiresInSeconds(null);

      showFeedback("success", "ტელეფონის ნომერი დადასტურებულია");
      window.dispatchEvent(new Event("lion-parts-orders-updated"));
    } catch (error) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : "SMS კოდის დადასტურება ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsVerifyingCode(false);
    }
  }

  const hasProfile = Boolean(customerName && customerPhone);
  const canEditPhone = !isSendingCode && !isVerifyingCode;

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

      <p className="muted">
        შეიყვანეთ სახელი და ქართული მობილური ნომერი. ნომერზე მიიღებთ SMS კოდს,
        რომლის დადასტურების შემდეგ პროფილი გააქტიურდება.
      </p>

      {hasProfile && (
        <div className="profile-status">
          <strong>{isPhoneVerified ? "ტელეფონი დადასტურებულია" : "პროფილი"}</strong>
          <span>
            {customerName} · {customerPhone}
          </span>
          <span>
            სტატუსი: {isPhoneVerified ? "Verified" : "Not verified"}
          </span>
        </div>
      )}

      <form className="checkout-form" onSubmit={handleVerifyCode}>
        <label>
          სახელი
          <input
            value={customerName}
            onChange={(event) => setCustomerName(event.target.value)}
            placeholder="მაგ: ლადო"
            disabled={isVerifyingCode}
          />
        </label>

        <label>
          ტელეფონის ნომერი
          <input
            value={customerPhone}
            onChange={(event) => {
              setCustomerPhone(event.target.value);
              resetVerificationState();
            }}
            placeholder="მაგ: 599123456 ან +995599123456"
            disabled={!canEditPhone}
          />
        </label>

        <div className="profile-actions">
          <button
            type="button"
            onClick={handleSendCode}
            disabled={isSendingCode || isVerifyingCode}
          >
            {isSendingCode ? "იგზავნება..." : "SMS კოდის გაგზავნა"}
          </button>
        </div>

        {isCodeSent && (
          <>
            <label>
              SMS კოდი
              <input
                value={verificationCode}
                onChange={(event) => setVerificationCode(event.target.value)}
                placeholder="6-ნიშნა კოდი"
                inputMode="numeric"
                maxLength={6}
                disabled={isVerifyingCode}
              />
            </label>

            {expiresInSeconds && (
              <p className="muted">
                კოდი მოქმედებს დაახლოებით {Math.round(expiresInSeconds / 60)} წუთი.
              </p>
            )}

            {demoCode && (
              <p className="muted">
                სატესტო კოდი: <strong>{demoCode}</strong>
              </p>
            )}

            <div className="profile-actions">
              <button type="submit" disabled={isVerifyingCode}>
                {isVerifyingCode ? "მოწმდება..." : "კოდის დადასტურება"}
              </button>
            </div>
          </>
        )}

        {message && (
          <p className={feedbackType === "error" ? "form-error" : "form-success"}>
            {message}
          </p>
        )}
      </form>
    </section>
  );
}

export default ProfilePage;