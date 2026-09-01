export function getOrderStatusLabel(status: string) {
  switch (status) {
    case "payment_pending":
      return "გადახდის მოლოდინში";
    case "paid":
      return "გადახდილია";
    case "processing":
      return "მუშავდება";
    case "action_required":
      return "საჭიროა მოქმედება";
    case "completed":
      return "დასრულებულია";
    case "cancelled":
      return "გაუქმებულია";
    default:
      return status;
  }
}