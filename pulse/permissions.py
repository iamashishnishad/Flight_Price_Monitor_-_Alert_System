from rest_framework.permissions import BasePermission

from .models import UserProfile


class IsAdminUser(BasePermission):
    """Allow access only to users whose profile role is ADMIN."""

    message = 'Only admin users can access this resource.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        profile = getattr(user, 'profile', None)
        return profile is not None and profile.role == UserProfile.Role.ADMIN
