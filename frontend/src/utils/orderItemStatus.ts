export function getOrderItemStatusLabel(status: string) {
  switch (status) {
    case "created":
      return "შექმნილია";
    case "payment_confirmed":
      return "გადახდა დადასტურებულია";
    case "checking":
      return "მოწმდება";
    case "action_required":
      return "საჭიროა მოქმედება";
    case "purchased":
      return "ნაწილი შეძენილია";
    case "received_usa":
      return "მიღებულია აშშ-ში";
    case "shipped_to_georgia":
      return "გამოგზავნილია საქართველოში";
    case "received_georgia":
      return "ჩამოსულია საქართველოში";
    case "ready_for_pickup":
      return "მზადაა გასაცემად";
    case "completed":
      return "დასრულებულია";
    case "cancelled":
      return "გაუქმებულია";
    default:
      return status;
  }
}

export function getActionTypeLabel(actionType: string) {
  switch (actionType) {
    case "price_change":
      return "ფასის ცვლილება";
    case "eta_change":
      return "ვადის ცვლილება";
    case "weight_change":
      return "წონის/გაბარიტის ცვლილება";
    case "fitment_issue":
      return "თავსებადობის პრობლემა";
    case "alternative_required":
      return "ალტერნატივის დადასტურება";
    case "other":
      return "სხვა საკითხი";
    default:
      return "";
  }
}