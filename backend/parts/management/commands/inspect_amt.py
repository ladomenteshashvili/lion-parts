from decimal import Decimal
from pprint import pprint

from django.core.management.base import BaseCommand, CommandError

from parts.amt_provider import AMTProviderError, get_price_by_oem
from parts.exchange_rates import ExchangeRateError, get_usd_sell_rate
from parts.pricing import calculate_final_price_gel


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
            usd_rate = get_usd_sell_rate()
        except ExchangeRateError as error:
            raise CommandError(f"Kursi.ge error: {error}") from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Rows returned: {len(rows)}"))
        self.stdout.write(self.style.SUCCESS(f"USD sell rate: {usd_rate}"))
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

            final_price = calculate_final_price_gel(
                api_price_usd=Decimal(str(api_price)),
                weight_kg=Decimal(str(weight)),
                usd_to_gel_rate=usd_rate,
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Calculated final price: {final_price} GEL"
                )
            )
            self.stdout.write("")