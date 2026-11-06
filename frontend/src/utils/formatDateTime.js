export const formatFullDateTime = (isoString) => {
  if (!isoString) return "--:--";
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return "--:--";
  return date.toLocaleString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
};

export const formatTimeOnly = (isoString) => {
  if (!isoString) return "--:--";
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return "--:--";
  return date.toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
};
