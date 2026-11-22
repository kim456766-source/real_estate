from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Sign in with the username OR the email address (not case-sensitive)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        login = (username or kwargs.get(User.USERNAME_FIELD) or "").strip()
        if not login or password is None:
            return None
        user = None
        if "@" in login:
            matches = list(User.objects.filter(email__iexact=login)[:2])
            user = matches[0] if len(matches) == 1 else None
        if user is None:  # a username (usernames may also contain "@")
            user = User.objects.filter(username__iexact=login).first()
        if user is None:
            User().set_password(password)  # same work as a real check, so timing doesn't reveal unknown accounts
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
