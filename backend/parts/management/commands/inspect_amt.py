from decimal import Decimal
from pprint import pprint

from django.core.management.base import BaseCommand, CommandError

from parts.amt_provider import AMTProviderError, get_price_by_oem
from pricing.rates import RateUnavailable, get_usd_rate
from pricing.engine import PriceInput, default_shipping_usd_per_kg, price_offer


class Command(BaseCommand):
    help = "Inspect raw AMT API response and calculated customer prices."

    def add_arguments(self, parser):
        parser.add_argument("part_number", type=str)

    def handle(self, *args, **options):
        part_number = options["part_number"].strip()

        if not part_number:
            raise CommandError("part_number is required")

        self.stdout.write(self.style.WARNING(f"Searching AMT for: {part_number}"))

        try:
            rows = get_price_by_oem(part_number)
        except AMTProviderError as error:
            raise CommandError(f"AMT error: {error}") from error

        try:
            usd_rate = get_usd_rate()
        except RateUnavailable as error:
            raise CommandError("NBG rate unavailable") from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Rows returned: {len(rows)}"))
        self.stdout.write(self.style.SUCCESS(f"USD rate (NBG): {usd_rate}"))
        self.stdout.write("")

        for index, row in enumerate(rows, start=1):
            self.stdout.write(self.style.WARNING(f"--- Row #{index} ---"))

            pprint(row)

            api_price = row.get("list_price")
            weight = row.get("weight")

            if api_price in ("", None):
                self.stdout.write(self.style.ERROR("No list_price returned"))
                self.stdout.write("")
                continue

            if weight in ("", None, 0, 0.0):
                self.stdout.write(
                    self.style.ERROR("No valid weight returned. Weight input needed.")
                )
                self.stdout.write("")
                continue

            final_price = price_offer(
                PriceInput(Decimal(str(api_price)), Decimal(str(weight)), default_shipping_usd_per_kg()),
                customer=None, currency="GEL", part_number=part_number, brand=str(row.get("brand") or ""),
                usd_rate=usd_rate,
            ).unit_price  # guest (default tariff) price

            self.stdout.write(
                self.style.SUCCESS(
                    f"Calculated final price: {final_price} GEL"
                )
            )
            self.stdout.write("")