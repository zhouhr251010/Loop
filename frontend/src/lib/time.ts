export function parseUtcTimestamp(timestamp: string) {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(timestamp);
  return new Date(hasTimezone ? timestamp : `${timestamp}Z`);
}

export function formatFeedTime(timestamp: string) {
  const date = parseUtcTimestamp(timestamp);
  const diffMs = Date.now() - date.getTime();
  const diffMinutes = Math.max(0, Math.floor(diffMs / 60000));

  if (diffMinutes < 1) {
    return "just now";
  }

  if (diffMinutes < 60) {
    return `${diffMinutes} min ago`;
  }

  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hours}:${minutes}`;
}

export function formatLocalDateTime(timestamp: string) {
  return parseUtcTimestamp(timestamp).toLocaleString();
}
