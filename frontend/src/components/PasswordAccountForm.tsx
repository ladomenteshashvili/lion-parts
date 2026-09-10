import { useState } from "react";
import type { FormEvent } from "react";

import {
  loginWithPassword,
  resetPassword,
  sendPasswordResetCode,
  setProfilePassword,
  type CustomerProfile,
} from "../api/profile";

type FeedbackType = "success" | "error" | "info";

type PasswordAccountFormProps = {
  profile: CustomerProfile | null;
  onAuthenticated: (profile: CustomerProfile) => void;
  onPasswordUpdated: (profile: CustomerProfile) => void;
};

const PASSWORD_RULES_TEXT =
  "პაროლი უნდა იყოს მინიმუმ 8 სიმბოლო და შეიცავდეს დიდ ასოს, პატარა ასოს, ციფრს და სიმბოლოს.";

function PasswordAccountForm({
  profile,
  onAuthenticated,
  onPasswordUpdated,
}: PasswordAccountFormProps) {
  const [loginPhone, setLoginPhone] = useState(profile?.customer_phone || "");
  const [loginPassword, setLoginPassword] = useState("");
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [repeatPassword, setRepeatPassword] = useState("");
  const [isSavingPassword, setIsSavingPassword] = useState(false);

  const [isResetOpen, setIsResetOpen] = useState(false);
  const [resetPhone, setResetPhone] = useState(profile?.customer_phone || "");
  const [resetCode, setResetCode] = useState("");
  const [resetNewPassword, setResetNewPassword] = useState("");
  const [resetRepeatPassword, setResetRepeatPassword] = useState("");
  const [isResetCodeSent, setIsResetCodeSent] = useState(false);
  const [resetDemoCode, setResetDemoCode] = useState("");
  const [isSendingResetCode, setIsSendingResetCode] = useState(false);
  const [isResettingPassword, setIsResettingPassword] = useState(false);

  const [feedbackType, setFeedbackType] = useState<FeedbackType>("info");
  const [message, setMessage] = useState("");

  function showFeedback(type: FeedbackType, text: string) {
    setFeedbackType(type);
    setMessage(text);
  }

  function clearPasswordFields() {
    setCurrentPassword("");
    setNewPassword("");
    setRepeatPassword("");
  }

  function passwordsMatch(password: string, repeatedPassword: string) {
    return password === repeatedPassword;
  }

  async function handleSavePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!newPassword) {
      showFeedback("error", "ახალი პაროლი აუცილებელია");
      return;
    }

    if (!passwordsMatch(newPassword, repeatPassword)) {
      showFeedback("error", "პაროლები ერთმანეთს არ ემთხვევა");
      return;
    }

    setIsSavingPassword(true);
    setMessage("");

    try {
      const updatedProfile = await setProfilePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });

      clearPasswordFields();
      showFeedback(
        "success",
        profile?.has_password ? "პაროლი შეიცვალა" : "პაროლი შეიქმნა"
      );
      onPasswordUpdated(updatedProfile);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "პაროლის შენახვა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsSavingPassword(false);
    }
  }

  async function handlePasswordLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!loginPhone.trim()) {
      showFeedback("error", "ტელეფონის ნომერი აუცილებელია");
      return;
    }

    if (!loginPassword) {
      showFeedback("error", "პაროლი აუცილებელია");
      return;
    }

    setIsLoggingIn(true);
    setMessage("");

    try {
      const loadedProfile = await loginWithPassword({
        customer_phone: loginPhone.trim(),
        password: loginPassword,
      });

      setLoginPassword("");
      showFeedback("success", "პაროლით შესვლა წარმატებულია");

      window.dispatchEvent(new Event("lion-parts-orders-updated"));
      window.dispatchEvent(new Event("lion-parts-cart-updated"));

      onAuthenticated(loadedProfile);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "პაროლით შესვლა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsLoggingIn(false);
    }
  }

  async function handleSendResetCode() {
    if (!resetPhone.trim()) {
      showFeedback("error", "ტელეფონის ნომერი აუცილებელია");
      return;
    }

    setIsSendingResetCode(true);
    setMessage("");
    setResetCode("");
    setResetDemoCode("");

    try {
      const response = await sendPasswordResetCode({
        customer_phone: resetPhone.trim(),
      });

      setIsResetCodeSent(true);

      if (response.demo_code) {
        setResetDemoCode(response.demo_code);
      }

      showFeedback(
        "success",
        response.already_sent
          ? "კოდი უკვე გაგზავნილია. შეიყვანეთ მიღებული SMS კოდი."
          : "პაროლის აღდგენის SMS კოდი გაგზავნილია."
      );
    } catch (error) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : "პაროლის აღდგენის კოდის გაგზავნა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsSendingResetCode(false);
    }
  }

  async function handleResetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!resetPhone.trim()) {
      showFeedback("error", "ტელეფონის ნომერი აუცილებელია");
      return;
    }

    if (!resetCode.trim()) {
      showFeedback("error", "SMS კოდი აუცილებელია");
      return;
    }

    if (!resetNewPassword) {
      showFeedback("error", "ახალი პაროლი აუცილებელია");
      return;
    }

    if (!passwordsMatch(resetNewPassword, resetRepeatPassword)) {
      showFeedback("error", "პაროლები ერთმანეთს არ ემთხვევა");
      return;
    }

    setIsResettingPassword(true);
    setMessage("");

    try {
      const loadedProfile = await resetPassword({
        customer_phone: resetPhone.trim(),
        code: resetCode.trim(),
        new_password: resetNewPassword,
      });

      setIsResetOpen(false);
      setIsResetCodeSent(false);
      setResetCode("");
      setResetNewPassword("");
      setResetRepeatPassword("");
      setResetDemoCode("");

      showFeedback("success", "პაროლი აღდგენილია და შესვლა შესრულდა");

      window.dispatchEvent(new Event("lion-parts-orders-updated"));
      window.dispatchEvent(new Event("lion-parts-cart-updated"));

      onAuthenticated(loadedProfile);
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "პაროლის აღდგენა ვერ მოხერხდა";

      showFeedback("error", errorMessage);
    } finally {
      setIsResettingPassword(false);
    }
  }

  const feedbackClassName =
    feedbackType === "error" ? "form-error" : "form-success";

  if (profile?.is_phone_verified) {
    return (
      <div className="note-box">
        <strong>{profile.has_password ? "პაროლის შეცვლა" : "პაროლის შექმნა"}</strong>
        <p className="muted">{PASSWORD_RULES_TEXT}</p>

        <form className="checkout-form" onSubmit={handleSavePassword}>
          {profile.has_password && (
            <label>
              მიმდინარე პაროლი
              <input
                type="password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                disabled={isSavingPassword}
              />
            </label>
          )}

          <label>
            ახალი პაროლი
            <input
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              disabled={isSavingPassword}
            />
          </label>

          <label>
            გაიმეორეთ ახალი პაროლი
            <input
              type="password"
              value={repeatPassword}
              onChange={(event) => setRepeatPassword(event.target.value)}
              disabled={isSavingPassword}
            />
          </label>

          <div className="profile-actions">
            <button type="submit" disabled={isSavingPassword}>
              {isSavingPassword
                ? "ინახება..."
                : profile.has_password
                  ? "პაროლის შეცვლა"
                  : "პაროლის შექმნა"}
            </button>
          </div>
        </form>

        {message && <p className={feedbackClassName}>{message}</p>}
      </div>
    );
  }

  return (
    <div className="note-box">
      <strong>პაროლით შესვლა</strong>
      <p className="muted">
        თუ ამ ნომერზე უკვე შექმნილი გაქვთ პაროლი, შეგიძლიათ SMS კოდის გარეშე
        შეხვიდეთ.
      </p>

      <form className="checkout-form" onSubmit={handlePasswordLogin}>
        <label>
          მობილური პაროლით შესვლისთვის
          <input
            value={loginPhone}
            onChange={(event) => setLoginPhone(event.target.value)}
            placeholder="მაგ: 599123456 ან +995599123456"
            disabled={isLoggingIn}
          />
        </label>

        <label>
          პაროლი
          <input
            type="password"
            value={loginPassword}
            onChange={(event) => setLoginPassword(event.target.value)}
            disabled={isLoggingIn}
          />
        </label>

        <div className="profile-actions">
          <button type="submit" disabled={isLoggingIn}>
            {isLoggingIn ? "მოწმდება..." : "პაროლით შესვლა"}
          </button>

          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              setIsResetOpen((value) => !value);
              setMessage("");
            }}
          >
            პაროლი დამავიწყდა
          </button>
        </div>
      </form>

      {isResetOpen && (
        <form className="checkout-form" onSubmit={handleResetPassword}>
          <p className="muted">{PASSWORD_RULES_TEXT}</p>

          <label>
            მობილური პაროლის აღდგენისთვის
            <input
              value={resetPhone}
              onChange={(event) => {
                setResetPhone(event.target.value);
                setIsResetCodeSent(false);
                setResetCode("");
                setResetDemoCode("");
              }}
              placeholder="მაგ: 599123456 ან +995599123456"
              disabled={isSendingResetCode || isResettingPassword}
            />
          </label>

          <div className="profile-actions">
            <button
              type="button"
              onClick={handleSendResetCode}
              disabled={isSendingResetCode || isResettingPassword}
            >
              {isSendingResetCode ? "იგზავნება..." : "აღდგენის კოდის გაგზავნა"}
            </button>
          </div>

          {isResetCodeSent && (
            <>
              <label>
                SMS კოდი
                <input
                  value={resetCode}
                  onChange={(event) => setResetCode(event.target.value)}
                  placeholder="6-ნიშნა კოდი"
                  inputMode="numeric"
                  maxLength={6}
                  disabled={isResettingPassword}
                />
              </label>

              {resetDemoCode && (
                <p className="muted">
                  სატესტო კოდი: <strong>{resetDemoCode}</strong>
                </p>
              )}

              <label>
                ახალი პაროლი
                <input
                  type="password"
                  value={resetNewPassword}
                  onChange={(event) => setResetNewPassword(event.target.value)}
                  disabled={isResettingPassword}
                />
              </label>

              <label>
                გაიმეორეთ ახალი პაროლი
                <input
                  type="password"
                  value={resetRepeatPassword}
                  onChange={(event) => setResetRepeatPassword(event.target.value)}
                  disabled={isResettingPassword}
                />
              </label>

              <div className="profile-actions">
                <button type="submit" disabled={isResettingPassword}>
                  {isResettingPassword ? "ინახება..." : "პაროლის აღდგენა"}
                </button>
              </div>
            </>
          )}
        </form>
      )}

      {message && <p className={feedbackClassName}>{message}</p>}
    </div>
  );
}

export default PasswordAccountForm;
