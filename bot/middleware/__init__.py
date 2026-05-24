"""Middleware пакет."""

from bot.middleware.resilience import HandlerResilienceMiddleware

__all__ = ["HandlerResilienceMiddleware"]
