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
};

export type PartOption = {
  part_option_id: string;
  name: string;
  condition: string;
  brand: string;
  availability: string;
  eta_days: number;
  final_price_gel: number;
  currency: "GEL";
  note?: string;
};

export type PartSearchResponse = {
  quote_id: string;
  part_number: string;
  vin: string | null;
  results: PartOption[];
};

export async function searchParts(payload: PartSearchPayload): Promise<PartSearchResponse> {
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