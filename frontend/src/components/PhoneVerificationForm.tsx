import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  isCustomerProfile,
  sendPhoneVerificationCode,
  verifyPhoneCode,
  type CustomerProfile,
} from "../api/profile";

type FeedbackType = "success" | "error" | "info";

type PhoneVerificationFormProps = {
  initialProfile: CustomerProfile | null;
  onVerified?: (profile: CustomerProfile) => void;
};

function PhoneVerificationForm({
  initialProfile,
  onVerified,
}: PhoneVerificationFormProps) {
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [verificationCode, setVerificationCode] = useState("");

  const [isPhoneVerified, setIsPhoneVerified] = useState(false);
  const [isSendingCode, setIsSendingCode] = useState(false);
  const [isVerifyingCode, setIsVerifyingCode] = useState(false);

  const [isCodeSent, setIsCodeSent] = useState(false);
  const [requiresCustomerName, setRequiresCustomerName] = useState(false);
  const [demoCode, setDemoCode] = useState("");
  const [expiresInSeconds, setExpiresInSeconds] = useState<number | null>(null);

  const [feedbackType, setFeedbackType] = useState<FeedbackType>("info");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (initialProfile) {
      setCustomerName(initialProfile.customer_name);
      setCustomerPhone(initialProfile.customer_phone);
      setIsPhoneVerified(initialProfile.is_phone_verified);
    }
  }, [initialProfile]);

  function showFeedback(type: FeedbackType, text: string) {
    setFeedbackType(type);
    setMessage(text);
  }

  function resetVerificationState() {
    setIsPhoneVerified(false);
    setIsCodeSent(false);
    setRequiresCustomerName(false);
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
    setRequiresCustomerName(false);
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
        response.already_sent
          ? "კოდი უკვე გაგზავნილია. შეიყვანეთ მიღებული SMS კოდი."
          : "SMS კოდი გაგზავნილია. შეიყვანეთ მიღებული კოდი."
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

    if (!customerPhone.trim()) {
      showFeedback("error", "ტელეფონის ნომერი აუცილებელია");
      return;
    }

    if (!verificationCode.trim()) {
      showFeedback("error", "SMS კოდი აუცილებელია");
      return;
    }

    if (requiresCustomerName && !customerName.trim()) {
      showFeedback("error", "ახალი პროფილისთვის სახელი აუცილებელია");
      return;
    }

    setIsVerifyingCode(true);
    setMessage("");

    try {
      const response = await verifyPhoneCode({
        customer_phone: customerPhone.trim(),
        code: verificationCode.trim(),
        customer_name: requiresCustomerName ? customerName.trim() : undefined,
      });

      if (!isCustomerProfile(response)) {
        setRequiresCustomerName(true);
        showFeedback(
          "info",
          "ნომერი დადასტურდა. პროფილის შესაქმნელად შეიყვანეთ სახელი."
        );
        return;
      }

      setCustomerName(response.customer_name);
      setCustomerPhone(response.customer_phone);
      setIsPhoneVerified(response.is_phone_verified);
      setIsCodeSent(false);
      setRequiresCustomerName(false);
      setVerificationCode("");
      setDemoCode("");
      setExpiresInSeconds(null);

      showFeedback("success", "ტელეფონის ნომერი დადასტურებულია");

      window.dispatchEvent(new Event("lion-parts-orders-updated"));

      if (onVerified) {
        onVerified(response);
      }
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

  return (
    <>
      <p className="muted">
        შეიყვანეთ ქართული მობილური ნომერი. ნომერზე მიიღებთ SMS კოდს,
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

            {requiresCustomerName && (
              <label>
                სახელი
                <input
                  value={customerName}
                  onChange={(event) => setCustomerName(event.target.value)}
                  placeholder="მაგ: ლადო"
                  disabled={isVerifyingCode}
                />
              </label>
            )}

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
                {isVerifyingCode
                  ? "მოწმდება..."
                  : requiresCustomerName
                    ? "პროფილის დასრულება"
                    : "კოდის დადასტურება"}
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
    </>
  );
}

export default PhoneVerificationForm;
