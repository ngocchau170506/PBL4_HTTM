export function resolveMediaUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return null;
  const trimmed = rawUrl.trim();
  if (!trimmed) return null;

  // Rewrite localhost:3000 upload URLs when Web is accessed from another LAN IP / Caddy
  if (typeof window !== "undefined") {
    const localBackendPrefix = /^https?:\/\/(localhost|127\.0\.0\.1):3000(\/uploads\/.+)$/i;
    const match = trimmed.match(localBackendPrefix);
    if (match) {
      const host = window.location.hostname;
      if (host !== "localhost" && host !== "127.0.0.1") {
        return match[2];
      }
    }
  }

  if (/^(https?:|blob:|data:)/i.test(trimmed)) {
    return trimmed;
  }

  if (trimmed.startsWith("/")) {
    const apiBase = import.meta.env.VITE_API_BASE_URL || "";
    if (/^https?:\/\//i.test(apiBase)) {
      try {
        return `${new URL(apiBase).origin}${trimmed}`;
      } catch {
        return trimmed;
      }
    }
    return trimmed;
  }

  return trimmed;
}

export default resolveMediaUrl;
