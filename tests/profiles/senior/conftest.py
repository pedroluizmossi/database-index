"""Testes do perfil `senior` (convenções da Senior: SDL/PDL, dono no DDD, handlers, nomes `snr-`).

Ficam fora dos testes do núcleo para sair junto com o pacote do perfil. Sem o perfil instalado, a pasta é ignorada.
Estes testes carregam o perfil explicitamente (`load_profiles(["senior"])` ou os módulos dele): valem em qualquer
rodada, inclusive com `--profiles default`.
"""

from __future__ import annotations

from code_index.profiles.registry import available_profiles

collect_ignore_glob = [] if "senior" in available_profiles() else ["test_*.py"]
