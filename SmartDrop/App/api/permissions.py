from rest_framework.permissions import BasePermission


class IsAuthenticatedUser(BasePermission):
    def has_permission(self, request, view):
        return bool(getattr(request.user, 'is_authenticated', False))


class IsAdministrator(IsAuthenticatedUser):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.rol_id == 2
