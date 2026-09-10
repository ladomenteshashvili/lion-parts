import { getSessionId } from "./cart";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

export type CustomerProfile = {
  id: number;
  session_id: string;
  customer_name: string;
  customer_phone: string;
  customer_tariff_id: number | null;
  customer_tariff_name: string | null;
  markup_percent: string;
  can_enter_weight: boolean;
  is_phone_verified: boolean;
  has_password: boolean;
  can_request_quote: boolean;
  created_at: string;
  updated_at: string;
};

export type SendPhoneVerificationResponse = {
  detail: string;
  phone: string;
  expires_in_seconds?: number;
  retry_after_seconds?: number;
  demo_code?: string;
  already_sent?: boolean;
};

export type VerifyPhoneCodeNeedsNameResponse = {
  detail: string;
  phone: string;
  requires_customer_name: true;
};

export type VerifyPhoneCodeResponse =
  | CustomerProfile
  | VerifyPhoneCodeNeedsNameResponse;

export function isCustomerProfile(
  response: VerifyPhoneCodeResponse
): response is CustomerProfile {
  return !("requires_customer_name" in response);
}

async function getErrorMessage(response: Response, fallback: string) {
  let data: unknown = null;

  try {
    data = await response.json();
  } catch {
    // Ignore JSON parsing errors and fallback below.
  }

  if (data && typeof data === "object") {
    const errorData = data as { detail?: unknown; password?: unknown };

    if (errorData.detail) {
      return String(errorData.detail);
    }

    if (Array.isArray(errorData.password)) {
      return errorData.password.map(String).join("\n");
    }

    if (errorData.password) {
      return String(errorData.password);
    }
  }

  return fallback;
}

export async function getProfile(): Promise<CustomerProfile | null> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/accounts/profile/?session_id=${encodeURIComponent(
      sessionId
    )}`
  );

  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Profile load failed");
  }

  return response.json();
}

export async function sendPhoneVerificationCode(payload: {
  customer_phone: string;
}): Promise<SendPhoneVerificationResponse> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/accounts/send-code/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      customer_phone: payload.customer_phone,
    }),
  });

  if (!response.ok) {
    const errorMessage = await getErrorMessage(
      response,
      "Verification code send failed"
    );

    throw new Error(errorMessage);
  }

  return response.json();
}

export async function verifyPhoneCode(payload: {
  customer_phone: string;
  code: string;
  customer_name?: string;
}): Promise<VerifyPhoneCodeResponse> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/accounts/verify-code/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      customer_phone: payload.customer_phone,
      code: payload.code,
      customer_name: payload.customer_name || "",
    }),
  });

  if (!response.ok) {
    const errorMessage = await getErrorMessage(
      response,
      "Verification code check failed"
    );

    throw new Error(errorMessage);
  }

  return response.json();
}

export async function setProfilePassword(payload: {
  current_password?: string;
  new_password: string;
}): Promise<CustomerProfile> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/accounts/profile/password/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      current_password: payload.current_password || "",
      new_password: payload.new_password,
    }),
  });

  if (!response.ok) {
    const errorMessage = await getErrorMessage(
      response,
      "Password save failed"
    );

    throw new Error(errorMessage);
  }

  return response.json();
}

export async function loginWithPassword(payload: {
  customer_phone: string;
  password: string;
}): Promise<CustomerProfile> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/accounts/login-password/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      customer_phone: payload.customer_phone,
      password: payload.password,
    }),
  });

  if (!response.ok) {
    const errorMessage = await getErrorMessage(
      response,
      "Password login failed"
    );

    throw new Error(errorMessage);
  }

  return response.json();
}

export async function sendPasswordResetCode(payload: {
  customer_phone: string;
}): Promise<SendPhoneVerificationResponse> {
  const sessionId = getSessionId();

  const response = await fetch(
    `${API_BASE_URL}/api/accounts/password-reset/send-code/`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
        customer_phone: payload.customer_phone,
      }),
    }
  );

  if (!response.ok) {
    const errorMessage = await getErrorMessage(
      response,
      "Password reset code send failed"
    );

    throw new Error(errorMessage);
  }

  return response.json();
}

export async function resetPassword(payload: {
  customer_phone: string;
  code: string;
  new_password: string;
}): Promise<CustomerProfile> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/accounts/password-reset/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      customer_phone: payload.customer_phone,
      code: payload.code,
      new_password: payload.new_password,
    }),
  });

  if (!response.ok) {
    const errorMessage = await getErrorMessage(
      response,
      "Password reset failed"
    );

    throw new Error(errorMessage);
  }

  return response.json();
}

// Keep old demo save available for temporary/manual testing.
export async function saveProfile(payload: {
  customer_name: string;
  customer_phone: string;
}): Promise<CustomerProfile> {
  const sessionId = getSessionId();

  const response = await fetch(`${API_BASE_URL}/api/accounts/demo-login/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      ...payload,
    }),
  });

  if (!response.ok) {
    const errorMessage = await getErrorMessage(response, "Profile save failed");
    throw new Error(errorMessage);
  }

  return response.json();
}
