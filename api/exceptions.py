"""API errors: clear JSON for the caller, full details only in the server log."""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("theo.api")


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)  # DRF's own errors (400, 401, 403, 404, 429 ...)
    if response is not None:
        return response
    view = context.get("view").__class__.__name__ if context.get("view") else "?"
    if isinstance(exc, DjangoValidationError):  # raised by model validation (e.g. payment rules)
        detail = exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages}
        return Response(detail, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, IntegrityError):
        logger.warning("Integrity error in %s: %s", view, exc)
        return Response({"detail": "This record conflicts with existing data (it may already exist)."},
                        status=status.HTTP_409_CONFLICT)
    if isinstance(exc, DatabaseError):
        logger.exception("Database error in %s", view)
        return Response({"detail": "The service is temporarily unavailable. Please try again."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    logger.exception("Unhandled API error in %s", view)
    return Response({"detail": "Something went wrong. Please try again later."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)
