"""Perfil `default`: nenhuma convenção de organização (comportamento genérico do núcleo)."""

from __future__ import annotations

from .base import BaseProfile


class DefaultProfile(BaseProfile):
    name = "default"
    label = "Genérico (sem convenções de organização)"
