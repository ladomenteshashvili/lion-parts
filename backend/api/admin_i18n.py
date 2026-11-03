"""Admin editing of translated JSON fields (G§5.8): one input per settings.LANGUAGES entry, so a new
language shows up without code changes. The default-language value is required once anything is entered
(and always, when the model field is not blank=True)."""

from django import forms
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TranslatedJSONWidget(forms.MultiWidget):
    template_name = "api/widgets/translated_json.html"

    def __init__(self, attrs=None, textarea=False):
        self.languages = list(settings.LANGUAGES)
        def make_input():
            return forms.Textarea(attrs={"rows": 3}) if textarea else forms.TextInput(attrs={"size": 60})

        super().__init__([make_input() for _lang in self.languages], attrs)

    def decompress(self, value):
        value = value if isinstance(value, dict) else {}
        return [value.get(code, "") for code, _name in self.languages]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        for subwidget, (code, lang_name) in zip(context["widget"]["subwidgets"], self.languages):
            subwidget["language"] = f"{lang_name} ({code})"
        return context


class TranslatedJSONField(forms.MultiValueField):
    def __init__(self, *, textarea=False, **kwargs):
        self.languages = list(settings.LANGUAGES)
        fields = [
            forms.CharField(
                required=code == settings.LANGUAGE_CODE,
                error_messages={"incomplete": _("The default-language value is required.")},
            )
            for code, _name in self.languages
        ]
        super().__init__(fields, widget=TranslatedJSONWidget(textarea=textarea), require_all_fields=False, **kwargs)

    def compress(self, data_list):
        return {code: value.strip() for (code, _name), value in zip(self.languages, data_list or [])
                if value and value.strip()}


class TranslatedFieldsAdminMixin:
    """Every JSONField named `*_i18n` is edited with TranslatedJSONField."""

    textarea_i18n_fields = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.JSONField) and db_field.name.endswith("_i18n"):
            return TranslatedJSONField(
                textarea=db_field.name in self.textarea_i18n_fields, required=not db_field.blank,
                label=db_field.verbose_name.capitalize(), help_text=db_field.help_text,
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)
