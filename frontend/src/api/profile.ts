import { getSessionId } from "./cart";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

export type CustomerProfile = {
  id: number;
  session_id: string;
  customer_name: string;
  customer_phone: string;
  is_phone_verified: boolean;
  created_at: string;
  updated_at: string;
};

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
    const errorText = await response.text();
    throw new Error(errorText || "Profile save failed");
  }

  return response.json();
}