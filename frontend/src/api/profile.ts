const PROFILE_STORAGE_KEY = "lion_parts_profile";

export type CustomerProfile = {
  customer_name: string;
  customer_phone: string;
};

export function getProfile(): CustomerProfile | null {
  const rawProfile = localStorage.getItem(PROFILE_STORAGE_KEY);

  if (!rawProfile) {
    return null;
  }

  try {
    return JSON.parse(rawProfile) as CustomerProfile;
  } catch {
    return null;
  }
}

export function saveProfile(profile: CustomerProfile) {
  localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profile));
}

export function clearProfile() {
  localStorage.removeItem(PROFILE_STORAGE_KEY);
}