"""DRF ships no Georgian catalog. These are DRF's own msgids, listed so `makemessages` keeps
their ka translations in locale/ka (our LOCALE_PATHS catalog takes priority over DRF's).
Russian comes from DRF's ru catalog."""

from django.utils.translation import gettext_lazy as _

DRF_MESSAGES = {
    "required": _("This field is required."),
    "null": _("This field may not be null."),
    "blank": _("This field may not be blank."),
    "invalid_string": _("Not a valid string."),
    "max_length": _("Ensure this field has no more than {max_length} characters."),
    "min_length": _("Ensure this field has at least {min_length} characters."),
    "invalid_choice": _('"{input}" is not a valid choice.'),
    "invalid_boolean": _("Must be a valid boolean."),
    "invalid_integer": _("A valid integer is required."),
    "invalid_number": _("A valid number is required."),
    "max_value": _("Ensure this value is less than or equal to {max_value}."),
    "min_value": _("Ensure this value is greater than or equal to {min_value}."),
    "max_digits": _("Ensure that there are no more than {max_digits} digits in total."),
    "max_decimal_places": _("Ensure that there are no more than {max_decimal_places} decimal places."),
    "not_a_list": _('Expected a list of items but got type "{input_type}".'),
    "empty_list": _("This list may not be empty."),
    "not_a_dict": _('Expected a dictionary of items but got type "{input_type}".'),
    "invalid_data": _("Invalid data. Expected a dictionary, but got {datatype}."),
}
