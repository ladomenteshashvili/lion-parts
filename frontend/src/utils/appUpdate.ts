const CURRENT_BUILD_ID = import.meta.env.VITE_APP_BUILD_ID || "dev";

const BUILD_INFO_URL = "/build-info.json";
const RELOAD_MARKER_KEY = "lion_parts_frontend_reload_marker";

type BuildInfo = {
  build_id?: string;
  commit_sha?: string;
  built_at?: string;
};

function cleanString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

async function clearBrowserCaches() {
  if (!("caches" in window)) {
    return;
  }

  try {
    const cacheKeys = await caches.keys();
    await Promise.all(cacheKeys.map((cacheKey) => caches.delete(cacheKey)));
  } catch (error) {
    console.warn("Browser cache cleanup failed", error);
  }
}

async function unregisterServiceWorkers() {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  try {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(
      registrations.map((registration) => registration.unregister())
    );
  } catch (error) {
    console.warn("Service worker cleanup failed", error);
  }
}

function getReloadUrl(nextBuildId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("app_build", nextBuildId);
  return url.toString();
}

export async function checkForFrontendUpdate() {
  if (CURRENT_BUILD_ID === "dev") {
    await unregisterServiceWorkers();
    return;
  }

  try {
    const response = await fetch(`${BUILD_INFO_URL}?ts=${Date.now()}`, {
      cache: "no-store",
    });

    if (!response.ok) {
      return;
    }

    const buildInfo = (await response.json()) as BuildInfo;
    const latestBuildId = cleanString(buildInfo.build_id);

    if (!latestBuildId || latestBuildId === CURRENT_BUILD_ID) {
      localStorage.removeItem(RELOAD_MARKER_KEY);
      return;
    }

    const reloadMarker = `${CURRENT_BUILD_ID}->${latestBuildId}`;

    if (localStorage.getItem(RELOAD_MARKER_KEY) === reloadMarker) {
      return;
    }

    localStorage.setItem(RELOAD_MARKER_KEY, reloadMarker);

    await clearBrowserCaches();
    await unregisterServiceWorkers();

    window.location.replace(getReloadUrl(latestBuildId));
  } catch (error) {
    console.warn("Frontend update check failed", error);
  }
}
