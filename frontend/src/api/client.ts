const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

export async function getHealthStatus() {
  const response = await fetch(`${API_BASE_URL}/api/health/`);

  if (!response.ok) {
    throw new Error("Backend health check failed");
  }

  return response.json();
}

export type PartSearchPayload = {
  part_number: string;
  vin?: string;
  session_id?: string;
};

export type PartOption = {
  part_option_id: string;
  name: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number;
  final_price_gel: number | null;
  currency: "GEL";
  requires_weight_input?: boolean;
  weight_kg?: number | null;
  note?: string;
  weight_source?: "api" | "customer" | "operator" | "";
  customer_notice?: string;  
};

export type PartSearchResponse = {
  quote_id: string;
  part_number: string;
  vin: string | null;
  results: PartOption[];
};

export type PartQuoteRequestPayload = {
  session_id: string;
  part_number: string;
  vin?: string;
  customer_name?: string;
  customer_phone: string;
  comment?: string;
  quote_id?: string;
  part_option_id?: string;
  name?: string;
  condition?: string;
  brand?: string;
  availability?: string;
  eta_days?: number;
};

export type PartQuoteRequestResponse = {
  id: number;
  session_id: string;
  part_number: string;
  vin: string;
  customer_name: string;
  customer_phone: string;
  comment: string;
  status: string;
  created_at: string;
  updated_at: string;
};


export type PartsFeedItem = {
  id: number | string;
  part_number: string;
  vin: string;
  provider: string;
  quote_id: string;
  found_count: number;
  top_result_name: string;
  top_result_price_gel: number | null;
  feed_status: "search_result" | "processing" | "price_ready" | "cancelled";
  quote_request_id: number | null;
  notification_token: string | null;
  part_option_id: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number | null;
  weight_kg: number | null;
  operator_message: string;
  created_at: string;
};

export type PartsFeedResponse = {
  requires_phone_verification: boolean;
  results: PartsFeedItem[];
};

export async function getPartsFeed(
  sessionId: string
): Promise<PartsFeedResponse> {
  const response = await fetch(
    `${API_BASE_URL}/api/parts/feed/?session_id=${encodeURIComponent(
      sessionId
    )}`
  );

  if (!response.ok) {
    throw new Error("Parts feed load failed");
  }

  return response.json();
}

export async function searchParts(
  payload: PartSearchPayload
): Promise<PartSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/api/parts/search/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error("Part search failed");
  }

  return response.json();
}

export async function createPartQuoteRequest(
  payload: PartQuoteRequestPayload
): Promise<PartQuoteRequestResponse> {
  const response = await fetch(`${API_BASE_URL}/api/parts/quote-requests/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Quote request failed");
  }

  return response.json();
}


export type CalculatePartPricePayload = {
  session_id: string;
  part_number: string;
  part_option_id: string;
  weight_kg: number;
};

export async function calculatePartPrice(
  payload: CalculatePartPricePayload
): Promise<PartOption> {
  const response = await fetch(`${API_BASE_URL}/api/parts/calculate-price/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || "Part price calculation failed");
  }

  return response.json();
}

export type PreparedQuote = {
  id: number;
  token: string;
  part_number: string;
  vin: string;
  quote_id: string;
  part_option_id: string;
  name: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number | null;
  weight_kg: string | null;
  final_price_gel: string;
  currency: "GEL";
  operator_message: string;
  price_ready_at: string;
  is_acknowledged: boolean;
};

export async function getPreparedQuote(token: string): Promise<PreparedQuote> {
  const response = await fetch(
    `${API_BASE_URL}/api/parts/public/quote-requests/${encodeURIComponent(token)}/`
  );

  if (!response.ok) {
    throw new Error("Prepared quote load failed");
  }

  return response.json();
}

export async function acknowledgePreparedQuote(
  token: string
): Promise<PreparedQuote> {
  const response = await fetch(
    `${API_BASE_URL}/api/parts/public/quote-requests/${encodeURIComponent(token)}/acknowledge/`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error("Prepared quote acknowledge failed");
  }

  return response.json();
}
