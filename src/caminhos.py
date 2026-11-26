"""Caminhos do projeto, calculados a partir deste arquivo (independe de onde o programa é rodado)."""

from __future__ import annotations

from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
PASTA_RESULTADOS = RAIZ_PROJETO / "resultados"


def pasta_resultados() -> Path:
    """Devolve a pasta onde buscas e exportações são salvas, criando-a se não existir.

    Returns:
        Path: ``<projeto>/resultados``.
    """
    PASTA_RESULTADOS.mkdir(exist_ok=True)
    return PASTA_RESULTADOS
