export function formatDateKa(dateValue: string | null) {
  if (!dateValue) {
    return "მითითებული არ არის";
  }

  return new Date(dateValue).toLocaleDateString("ka-GE", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}