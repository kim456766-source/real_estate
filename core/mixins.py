from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class RoleRequiredMixin(LoginRequiredMixin):
    """Restrict a view to users with one of `allowed_roles`. Superusers always pass."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.is_superuser or request.user.role in self.allowed_roles):
            raise PermissionDenied("You do not have permission to access this page.")
        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = ("admin",)


class StaffRoleMixin(RoleRequiredMixin):
    """Admins, agents and owners: anyone who uses the management dashboard."""

    allowed_roles = ("admin", "agent", "owner")
