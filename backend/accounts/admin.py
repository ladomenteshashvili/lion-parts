from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from api.admin_i18n import TranslatedFieldsAdminMixin
from api.errors import ApiError

from .services import anonymize_customer
from .validators import normalize_phone, validate_personal_id

from .models import Customer, CustomerTariff, LegalEntityProfile, PhoneVerificationCode


@admin.register(CustomerTariff)
class CustomerTariffAdmin(TranslatedFieldsAdminMixin, admin.ModelAdmin):
    textarea_i18n_fields = ("benefits_text_i18n",)
    list_display = ("name", "is_default", "markup_percent", "payment_terms", "split_upfront_percent",
                    "can_enter_weight", "can_request_quote", "updated_at")
    list_filter = ("is_default", "payment_terms", "can_enter_weight", "can_request_quote")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")

    # Customer.get_tariff() needs a default tariff: it can't be deleted, not even in bulk.
    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_default) and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class LegalEntityProfileInline(admin.StackedInline):
    model = LegalEntityProfile
    extra = 0
    readonly_fields = ("created_at", "updated_at")


class CustomerForm(forms.ModelForm):
    """Identity fields as support edits them (LP-BE-1102): the login email lives on the user; a changed phone
    or email is no longer verified."""

    email = forms.EmailField(label=_("Email"))
    phone = forms.CharField(label=_("Phone"), max_length=30, required=False)  # any format; normalized below

    class Meta:
        model = Customer
        fields = ("account_type", "first_name", "last_name", "personal_id", "phone", "email", "tariff",
                  "preferred_language", "marketing_opt_in")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["email"].initial = self.instance.user.email

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        return normalize_phone(phone) if phone else None

    def clean_personal_id(self):
        personal_id = self.cleaned_data.get("personal_id") or None
        if personal_id:
            validate_personal_id(personal_id)
        return personal_id

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exclude(pk=self.instance.user_id).exists():
            raise forms.ValidationError(_("This email is already linked to another account."))
        return email

    def save(self, commit=True):
        customer = super().save(commit=False)
        user, email = customer.user, self.cleaned_data["email"]
        if "phone" in self.changed_data:
            customer.is_phone_verified, customer.phone_verified_at = False, None
        if user.email.lower() != email:
            user.email = user.username = email
            user.save(update_fields=["email", "username"])
            customer.is_email_verified, customer.email_verified_at = False, None
        if commit:
            customer.save()
        return customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """G§10 / LP-BE-1102. No password here (use the user's admin page for a reset); verification only through
    the action; anonymized customers are read-only."""

    form = CustomerForm
    list_display = ("id", "full_name", "email", "phone", "account_type", "tariff",
                    "is_phone_verified", "is_email_verified", "preferred_language", "anonymized_at", "created_at")
    list_filter = ("account_type", "tariff", "is_phone_verified", "is_email_verified", "preferred_language")
    search_fields = ("first_name", "last_name", "user__email", "phone", "personal_id",
                     "company__company_official_name", "company__company_identification_code")
    list_select_related = ("user", "tariff")
    readonly_fields = ("user_link", "is_phone_verified", "phone_verified_at", "is_email_verified", "email_verified_at",
                       "terms_accepted_at", "anonymized_at", "created_at", "updated_at")
    inlines = [LegalEntityProfileInline]
    actions = ["anonymize", "mark_email_verified"]

    def has_add_permission(self, request):
        return False  # customers register through the API

    def has_delete_permission(self, request, obj=None):
        return False  # anonymize instead (orders and payments must stay)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and not (obj and obj.anonymized_at)

    def has_operate_permission(self, request):
        return request.user.has_perm("accounts.change_customer")

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="Email", ordering="user__email")
    def email(self, obj):
        return obj.user.email

    @admin.display(description=_("User account (password reset)"))
    def user_link(self, obj):
        url = reverse("admin:auth_user_change", args=[obj.user_id])
        return format_html('<a href="{}">{}</a>', url, obj.user.get_username())

    @admin.action(description=_("Anonymize (same rules as account deletion)"), permissions=["operate"])
    def anonymize(self, request, queryset):
        for customer in queryset:
            if customer.anonymized_at:
                continue
            try:
                anonymize_customer(customer)
            except ApiError as error:
                self.message_user(request, f"#{customer.pk}: {error.detail}", messages.ERROR)
            else:
                self.log_change(request, customer, "Anonymized")
                self.message_user(request, _("#%(id)s: anonymized.") % {"id": customer.pk}, messages.SUCCESS)

    @admin.action(description=_("Mark email verified"), permissions=["operate"])
    def mark_email_verified(self, request, queryset):
        for customer in queryset.filter(is_email_verified=False, anonymized_at__isnull=True):
            Customer.objects.filter(pk=customer.pk).update(is_email_verified=True, email_verified_at=timezone.now())
            self.log_change(request, customer, "Email marked verified")
            self.message_user(request, _("#%(id)s: email marked verified.") % {"id": customer.pk}, messages.SUCCESS)


@admin.register(PhoneVerificationCode)
class PhoneVerificationCodeAdmin(admin.ModelAdmin):
    """Read-only audit view; the code hash is never shown."""

    list_display = ("id", "phone", "purpose", "status", "attempts", "max_attempts", "expires_at", "created_at")
    list_filter = ("purpose", "status")
    search_fields = ("phone", "provider_message_id")
    fields = ("session_key", "customer", "phone", "purpose", "status", "attempts", "max_attempts",
              "provider_message_id", "expires_at", "verified_at", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False
