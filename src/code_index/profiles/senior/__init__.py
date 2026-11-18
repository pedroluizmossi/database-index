"""Perfil `senior`: convenções da plataforma da Senior Sistemas.

Pacote isolado para sair do repositório depois (vira um pacote instalado com entry point no grupo
`code_index.profiles`). O núcleo nunca importa este pacote diretamente: só pelo registro (profiles/registry.py).
"""

from .profile import SeniorProfile

__all__ = ["SeniorProfile"]
