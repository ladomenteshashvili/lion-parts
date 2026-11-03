"""Admin actions that need operator input: an intermediate form page, then the same service the API and
demo endpoints use, once per selected row (G§10: the admin never edits money or status fields)."""

from django.contrib import messages
from django.template.response import TemplateResponse

from api.errors import ApiError

from .services import OperatorError


def form_action(modeladmin, request, queryset, form_class, title, run):
    """`run(obj, cleaned_data)` per selected row; a refused row shows its message and changes nothing."""
    form = form_class(request.POST if "apply" in request.POST else None)
    if not form.is_bound or not form.is_valid():
        return TemplateResponse(request, "admin/orders/action_form.html", {
            **modeladmin.admin_site.each_context(request), "title": title, "form": form, "queryset": queryset,
            "action": request.POST["action"], "opts": modeladmin.model._meta})
    for obj in queryset:
        try:
            done = run(obj, form.cleaned_data)
        except (OperatorError, ApiError) as error:
            text = error.message if isinstance(error, OperatorError) else error.detail
            modeladmin.message_user(request, f"{obj}: {text}", messages.ERROR)
        else:
            modeladmin.message_user(request, f"{obj}: {done}", messages.SUCCESS)
    return None


class OperatePermissionMixin:
    """Actions with permissions=["operate"] need the model's change permission (models stay read-only)."""

    operate_permission = None

    def has_operate_permission(self, request):
        opts = self.model._meta
        return request.user.has_perm(self.operate_permission or f"{opts.app_label}.change_{opts.model_name}")
