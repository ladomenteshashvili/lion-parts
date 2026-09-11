from django.conf import settings
from django.utils import timezone
from django.utils.html import escape

from .models import Order, Payment


def build_invoice_number(order):
    prefix = getattr(settings, "INVOICE_NUMBER_PREFIX", "INV")
    created_at = order.created_at or timezone.now()
    year = timezone.localtime(created_at).year
    return f"{prefix}-{year}-{order.id:06d}"


def format_invoice_money(value):
    if value is None:
        return "0.00"

    return f"{value:.2f}"


def render_order_invoice_html(order):
    if order.billing_type == Order.BILLING_LEGAL_ENTITY:
        buyer_type = "იურიდიული პირი"
        buyer_name = order.legal_entity_company_official_name
        buyer_code = order.legal_entity_company_identification_code
        buyer_address = order.legal_entity_legal_address
        buyer_contact = (
            f"{order.legal_entity_contact_first_name} "
            f"{order.legal_entity_contact_last_name}"
        ).strip()
        buyer_phone = order.legal_entity_mobile_phone
        buyer_email = order.legal_entity_email
    else:
        buyer_type = "ფიზიკური პირი"
        buyer_name = order.current_customer_name
        buyer_code = ""
        buyer_address = ""
        buyer_contact = order.current_customer_name
        buyer_phone = order.current_customer_phone
        buyer_email = ""

    try:
        payment = order.payment
        payment_status = payment.status
        payment_status_label = payment.get_status_display()
        paid_at = (
            timezone.localtime(payment.paid_at).strftime("%Y-%m-%d %H:%M")
            if payment.paid_at
            else ""
        )
    except Payment.DoesNotExist:
        payment_status = "missing"
        payment_status_label = "Missing"
        paid_at = ""

    order_status_label = order.get_status_display()

    invoice_document_title = getattr(settings, "INVOICE_DOCUMENT_TITLE", "INVOICE")
    invoice_document_subtitle = getattr(settings, "INVOICE_DOCUMENT_SUBTITLE", "")
    invoice_show_draft_watermark = getattr(
        settings,
        "INVOICE_SHOW_DRAFT_WATERMARK",
        False,
    )
    invoice_draft_watermark_text = getattr(
        settings,
        "INVOICE_DRAFT_WATERMARK_TEXT",
        "DRAFT",
    )

    seller_name = getattr(settings, "INVOICE_SELLER_NAME", "Lion Parts")
    seller_identification_code = getattr(
        settings,
        "INVOICE_SELLER_IDENTIFICATION_CODE",
        "",
    )
    seller_address = getattr(settings, "INVOICE_SELLER_ADDRESS", "")
    seller_phone = getattr(settings, "INVOICE_SELLER_PHONE", "")
    seller_email = getattr(settings, "INVOICE_SELLER_EMAIL", "")
    seller_bank_details = getattr(settings, "INVOICE_SELLER_BANK_DETAILS", "")
    invoice_footer_text = getattr(
        settings,
        "INVOICE_FOOTER_TEXT",
        "This invoice was generated from Lion Parts admin order data.",
    )
    payment_due_text = getattr(
        settings,
        "INVOICE_PAYMENT_DUE_TEXT",
        "Payment due upon receipt.",
    )
    invoice_amount_note = getattr(
        settings,
        "INVOICE_AMOUNT_NOTE",
        "All amounts are in GEL and include VAT unless otherwise noted.",
    )
    invoice_show_payment_badge = getattr(
        settings,
        "INVOICE_SHOW_PAYMENT_BADGE",
        True,
    )

    invoice_number = build_invoice_number(order)
    issue_date = timezone.localtime(order.created_at).strftime("%Y-%m-%d")
    created_at = timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M")

    payment_badge_class = (
        "paid" if payment_status == Payment.STATUS_PAID else "not-paid"
    )
    payment_badge_text = (
        "PAID" if payment_status == Payment.STATUS_PAID else payment_status_label.upper()
    )

    seller_lines = [f"<strong>{escape(seller_name or 'Seller')}</strong>"]

    for label, value in [
        ("ID Code", seller_identification_code),
        ("Address", seller_address),
        ("Phone", seller_phone),
        ("Email", seller_email),
        ("Bank", seller_bank_details),
    ]:
        if value:
            seller_lines.append(
                f"<div><strong>{escape(label)}:</strong> {escape(value)}</div>"
            )

    seller_html = "\n".join(seller_lines)
    subtitle_html = (
        f'<div class="subtitle">{escape(invoice_document_subtitle)}</div>'
        if invoice_document_subtitle
        else ""
    )
    watermark_html = (
        f'<div class="watermark">{escape(invoice_draft_watermark_text)}</div>'
        if invoice_show_draft_watermark
        else ""
    )
    payment_badge_html = (
        f'<div class="payment-badge {payment_badge_class}">'
        f'{escape(payment_badge_text)}</div>'
        if invoice_show_payment_badge
        else ""
    )

    rows = []
    row_number = 1

    for item in order.items.all():
        line_total = item.final_price_gel * item.quantity
        rows.append(
            f"""
            <tr>
              <td>{row_number}</td>
              <td>{escape(item.part_number)}</td>
              <td>{escape(item.name)}</td>
              <td class="right">{item.quantity}</td>
              <td class="right">{format_invoice_money(item.final_price_gel)}</td>
              <td class="right">{format_invoice_money(line_total)}</td>
            </tr>
            """
        )
        row_number += 1

    if order.courier_delivery_fee_gel and order.courier_delivery_fee_gel > 0:
        rows.append(
            f"""
            <tr>
              <td>{row_number}</td>
              <td></td>
              <td>{escape("კურიერით მიწოდება")}</td>
              <td class="right">1</td>
              <td class="right">{format_invoice_money(order.courier_delivery_fee_gel)}</td>
              <td class="right">{format_invoice_money(order.courier_delivery_fee_gel)}</td>
            </tr>
            """
        )

    return f"""
<!doctype html>
<html lang="ka">
<head>
  <meta charset="utf-8">
  <title>{escape(invoice_document_title)} {escape(invoice_number)}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 32px;
      color: #111;
      font-size: 14px;
    }}
    .watermark {{
      position: fixed;
      top: 42%;
      left: 12%;
      transform: rotate(-28deg);
      font-size: 96px;
      font-weight: 700;
      color: rgba(0, 0, 0, 0.08);
      z-index: -1;
      letter-spacing: 8px;
      pointer-events: none;
    }}
    .payment-badge {{
      display: inline-block;
      margin-top: 10px;
      padding: 6px 12px;
      border: 2px solid #222;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 1px;
    }}
    .payment-badge.paid {{
      border-color: #1f7a1f;
      color: #1f7a1f;
    }}
    .payment-badge.not-paid {{
      border-color: #777;
      color: #555;
    }}
    .top {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      border-bottom: 2px solid #111;
      padding-bottom: 16px;
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    h2 {{
      margin: 24px 0 8px;
      font-size: 16px;
    }}
    .subtitle {{
      margin-top: 4px;
      color: #555;
      font-size: 13px;
      max-width: 360px;
    }}
    .muted {{
      color: #555;
    }}
    .box {{
      border: 1px solid #ddd;
      padding: 14px;
      border-radius: 8px;
      margin-bottom: 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 16px;
    }}
    th, td {{
      border: 1px solid #ddd;
      padding: 9px;
      vertical-align: top;
    }}
    th {{
      background: #f3f3f3;
      text-align: left;
    }}
    .right {{
      text-align: right;
      white-space: nowrap;
    }}
    .total {{
      margin-top: 18px;
      text-align: right;
      font-size: 20px;
      font-weight: bold;
    }}
    .actions {{
      margin-bottom: 20px;
    }}
    @media print {{
      .actions {{
        display: none;
      }}
      body {{
        margin: 18mm;
      }}
    }}
  </style>
</head>
<body>
  {watermark_html}

  <div class="actions">
    <button onclick="window.print()">Print / Save as PDF</button>
  </div>

  <div class="top">
    <div>
      <h1>{escape(invoice_document_title)}</h1>
      {subtitle_html}
      {payment_badge_html}
      <div><strong>Invoice number:</strong> {escape(invoice_number)}</div>
      <div><strong>Issue date:</strong> {escape(issue_date)}</div>
      <div><strong>Order:</strong> {escape(order.order_number)}</div>
      <div><strong>Created:</strong> {escape(created_at)}</div>
      <div><strong>Status:</strong> {escape(order_status_label)}</div>
      <div><strong>Payment:</strong> {escape(payment_status_label)}</div>
      <div><strong>Paid at:</strong> {escape(paid_at or "—")}</div>
      <div><strong>Payment due:</strong> {escape(payment_due_text)}</div>
    </div>
    <div>
      <strong>Seller</strong><br>
      {seller_html}
    </div>
  </div>

  <div class="box">
    <h2>Buyer</h2>
    <div><strong>Type:</strong> {escape(buyer_type)}</div>
    <div><strong>Name:</strong> {escape(buyer_name or "—")}</div>
    <div><strong>ID Code:</strong> {escape(buyer_code or "—")}</div>
    <div><strong>Contact:</strong> {escape(buyer_contact or "—")}</div>
    <div><strong>Phone:</strong> {escape(buyer_phone or "—")}</div>
    <div><strong>Email:</strong> {escape(buyer_email or "—")}</div>
    <div><strong>Address:</strong> {escape(buyer_address or "—")}</div>
  </div>

  <div class="box">
    <h2>Order info</h2>
    <div><strong>VIN:</strong> {escape(order.vin or "—")}</div>
    <div><strong>Note:</strong> {escape(order.note or "—")}</div>
    <div><strong>Courier requested:</strong> {"Yes" if order.courier_delivery_requested else "No"}</div>
  </div>

  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Part number</th>
        <th>Description</th>
        <th class="right">Qty</th>
        <th class="right">Unit GEL</th>
        <th class="right">Line GEL</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>

  <div class="total">
    Total: {format_invoice_money(order.total_gel)} GEL
  </div>

  <p class="muted">
    {escape(invoice_amount_note)}
  </p>

  <p class="muted">
    {escape(invoice_footer_text)}
  </p>
</body>
</html>
"""
