import { useState, type FormEvent } from "react";

import {
  saveLegalEntityProfile,
  sendLegalEntityMobileVerificationCode,
  verifyLegalEntityMobileCode,
  type CustomerProfile,
  type LegalEntityProfilePayload,
} from "../api/profile";

type LegalEntityProfileFormProps = {
  profile: CustomerProfile | null;
  onSaved: (profile: CustomerProfile) => void;
};

type FeedbackType = "success" | "error" | "info";

function getInitialForm(profile: CustomerProfile | null): LegalEntityProfilePayload {
  const legalEntity = profile?.legal_entity;

  return {
    company_identification_code: legalEntity?.company_identification_code || "",
    company_official_name: legalEntity?.company_official_name || "",
    legal_address: legalEntity?.legal_address || "",
    contact_first_name: legalEntity?.contact_first_name || "",
    contact_last_name: legalEntity?.contact_last_name || "",
    email: legalEntity?.email || "",
    mobile_phone: legalEntity?.mobile_phone || profile?.customer_phone || "",
  };
}

function LegalEntityProfileForm({
  profile,
  onSaved,
}: LegalEntityProfileFormProps) {
  const [form, setForm] = useState<LegalEntityProfilePayload>(() =>
    getInitialForm(profile)
  );
  const [isSaving, setIsSaving] = useState(false);
  const [isSendingCode, setIsSendingCode] = useState(false);
  const [isVerifyingCode, setIsVerifyingCode] = useState(false);
  const [verificationCode, setVerificationCode] = useState("");
  const [feedbackType, setFeedbackType] = useState<FeedbackType>("info");
  const [message, setMessage] = useState("");

  if (!profile?.is_phone_verified) {
    return null;
  }

  const legalEntity = profile.legal_entity || null;
  const needsMobileVerification =
    Boolean(legalEntity) && legalEntity?.is_mobile_verified === false;
  const isBusy = isSaving || isSendingCode || isVerifyingCode;

  function updateField(field: keyof LegalEntityProfilePayload, value: string) {
    setForm((currentForm) => ({
      ...currentForm,
      [field]: value,
    }));
  }

  function showFeedback(type: FeedbackType, text: string) {
    setFeedbackType(type);
    setMessage(text);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const requiredFields: Array<keyof LegalEntityProfilePayload> = [
      "company_identification_code",
      "company_official_name",
      "legal_address",
      "contact_first_name",
      "contact_last_name",
      "email",
      "mobile_phone",
    ];

    const hasEmptyRequiredField = requiredFields.some(
      (field) => !form[field].trim()
    );

    if (hasEmptyRequiredField) {
      showFeedback("error", "ყველა ველი აუცილებელია");
      return;
    }

    setIsSaving(true);
    setMessage("");

    try {
      const updatedProfile = await saveLegalEntityProfile({
        company_identification_code: form.company_identification_code.trim(),
        company_official_name: form.company_official_name.trim(),
        legal_address: form.legal_address.trim(),
        contact_first_name: form.contact_first_name.trim(),
        contact_last_name: form.contact_last_name.trim(),
        email: form.email.trim(),
        mobile_phone: form.mobile_phone.trim(),
      });

      const savedLegalEntity = updatedProfile.legal_entity;

      onSaved(updatedProfile);

      showFeedback(
        "success",
        savedLegalEntity?.is_mobile_verified
          ? "კომპანიის მონაცემები შენახულია და მობილური დადასტურებულია"
          : "კომპანიის მონაცემები შენახულია. მობილურის დასადასტურებლად გაგზავნეთ კოდი."
      );
    } catch (error) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : "კომპანიის მონაცემების შენახვა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSendMobileCode() {
    setIsSendingCode(true);
    setMessage("");

    try {
      const response = await sendLegalEntityMobileVerificationCode();

      if (response.already_verified) {
        showFeedback("success", "კომპანიის მობილური უკვე დადასტურებულია");
        return;
      }

      const demoCodeText = response.demo_code
        ? ` სატესტო კოდი: ${response.demo_code}`
        : "";

      showFeedback("success", `კოდი გაგზავნილია.${demoCodeText}`);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "კოდის გაგზავნა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsSendingCode(false);
    }
  }

  async function handleVerifyMobileCode() {
    const cleanedCode = verificationCode.trim();

    if (!cleanedCode) {
      showFeedback("error", "შეიყვანეთ დადასტურების კოდი");
      return;
    }

    setIsVerifyingCode(true);
    setMessage("");

    try {
      const updatedProfile = await verifyLegalEntityMobileCode({
        code: cleanedCode,
      });

      onSaved(updatedProfile);
      setVerificationCode("");
      showFeedback("success", "კომპანიის მობილური დადასტურებულია");
    } catch (error) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : "მობილურის დადასტურება ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsVerifyingCode(false);
    }
  }

  const feedbackClassName =
    feedbackType === "error" ? "form-error" : "form-success";

  return (
    <div className="note-box">
      <strong>იურიდიული პირის მონაცემები</strong>
      <p className="muted">
        შეავსეთ კომპანიის მონაცემები, თუ შეკვეთების გაფორმება გსურთ იურიდიულ
        პირზე. ამ ეტაპზე მონაცემები ინახება პროფილში და ოპერატორს გამოუჩნდება
        admin-ში.
      </p>

      {legalEntity && (
        <p className="muted">
          შენახულია: {legalEntity.company_official_name} ·{" "}
          {legalEntity.company_identification_code}
        </p>
      )}

      <form className="checkout-form" onSubmit={handleSubmit}>
        <label>
          კომპანიის საიდენტიფიკაციო კოდი
          <input
            value={form.company_identification_code}
            onChange={(event) =>
              updateField("company_identification_code", event.target.value)
            }
            disabled={isBusy}
          />
        </label>

        <label>
          კომპანიის ოფიციალური სახელწოდება
          <input
            value={form.company_official_name}
            onChange={(event) =>
              updateField("company_official_name", event.target.value)
            }
            disabled={isBusy}
          />
        </label>

        <label>
          იურიდიული მისამართი
          <textarea
            value={form.legal_address}
            onChange={(event) => updateField("legal_address", event.target.value)}
            disabled={isBusy}
          />
        </label>

        <label>
          სახელი
          <input
            value={form.contact_first_name}
            onChange={(event) =>
              updateField("contact_first_name", event.target.value)
            }
            disabled={isBusy}
          />
        </label>

        <label>
          გვარი
          <input
            value={form.contact_last_name}
            onChange={(event) =>
              updateField("contact_last_name", event.target.value)
            }
            disabled={isBusy}
          />
        </label>

        <label>
          ელ.ფოსტა
          <input
            type="email"
            value={form.email}
            onChange={(event) => updateField("email", event.target.value)}
            disabled={isBusy}
          />
        </label>

        <label>
          მობილური ნომერი
          <input
            value={form.mobile_phone}
            onChange={(event) => updateField("mobile_phone", event.target.value)}
            placeholder="მაგ: 599123456 ან +995599123456"
            disabled={isBusy}
          />
        </label>

        {legalEntity && (
          <p className="muted">
            მობილურის სტატუსი:{" "}
            {legalEntity.is_mobile_verified
              ? "დადასტურებულია"
              : "დასადასტურებელია"}
          </p>
        )}

        <div className="profile-actions">
          <button type="submit" disabled={isBusy}>
            {isSaving ? "ინახება..." : "კომპანიის მონაცემების შენახვა"}
          </button>
        </div>
      </form>

      {needsMobileVerification && (
        <div className="note-box">
          <strong>კომპანიის მობილურის დადასტურება</strong>
          <p className="muted">
            კოდი გაიგზავნება შენახულ კომპანიის მობილურზე:{" "}
            {legalEntity?.mobile_phone}
          </p>

          <div className="profile-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={handleSendMobileCode}
              disabled={isBusy}
            >
              {isSendingCode ? "იგზავნება..." : "კოდის გაგზავნა"}
            </button>
          </div>

          <label>
            დადასტურების კოდი
            <input
              value={verificationCode}
              onChange={(event) => setVerificationCode(event.target.value)}
              disabled={isBusy}
            />
          </label>

          <div className="profile-actions">
            <button
              type="button"
              onClick={handleVerifyMobileCode}
              disabled={isBusy}
            >
              {isVerifyingCode ? "მოწმდება..." : "მობილურის დადასტურება"}
            </button>
          </div>
        </div>
      )}

      {message && <p className={feedbackClassName}>{message}</p>}
    </div>
  );
}

export default LegalEntityProfileForm;
