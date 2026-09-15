from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError


OPERATOR_GROUP_NAME = "Operator"

OPERATOR_PERMISSIONS = [
    # Customer management
    ("accounts", "customer", "view_customer"),
    ("accounts", "customer", "change_customer"),

    # Tariff can be selected on customer, but tariff settings should not be edited by operator
    ("accounts", "customertariff", "view_customertariff"),

    # Orders
    ("orders", "order", "view_order"),
    ("orders", "order", "change_order"),

    # Order items: status changes, proposed price/ETA/alternative fields, action request actions
    ("orders", "orderitem", "view_orderitem"),
    ("orders", "orderitem", "change_orderitem"),

    # Payments: manual confirmation actions
    ("orders", "payment", "view_payment"),
    ("orders", "payment", "change_payment"),

    # Support messages: operator replies from admin
    ("orders", "ordersupportmessage", "view_ordersupportmessage"),
    ("orders", "ordersupportmessage", "add_ordersupportmessage"),
    ("orders", "ordersupportmessage", "change_ordersupportmessage"),

    # Notifications: copy customer magic links
    ("orders", "ordercustomernotification", "view_ordercustomernotification"),

    # Events: view history only
    ("orders", "orderitemevent", "view_orderitemevent"),
]


class Command(BaseCommand):
    help = "Create or update a limited Django Admin operator user."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Operator username")
        parser.add_argument(
            "--email",
            default="",
            help="Optional operator email",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Optional password. If omitted, you will be prompted.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        email = options["email"].strip()
        password = options["password"]

        if not username:
            raise CommandError("Username is required.")

        if not password:
            password = getpass("Operator password: ")
            password_confirm = getpass("Confirm password: ")

            if password != password_confirm:
                raise CommandError("Passwords do not match.")

        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters.")

        group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)

        permissions = []

        for app_label, model, codename in OPERATOR_PERMISSIONS:
            try:
                permission = Permission.objects.get(
                    content_type__app_label=app_label,
                    content_type__model=model,
                    codename=codename,
                )
            except Permission.DoesNotExist as exc:
                raise CommandError(
                    f"Permission not found: {app_label}.{model}.{codename}. "
                    "Run migrations first."
                ) from exc

            permissions.append(permission)

        group.permissions.set(permissions)

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": False,
            },
        )

        user.email = email or user.email
        user.is_staff = True
        user.is_superuser = False
        user.set_password(password)
        user.save()

        user.groups.add(group)

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} operator user: {username}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Group '{OPERATOR_GROUP_NAME}' permissions updated: {len(permissions)}"
            )
        )
