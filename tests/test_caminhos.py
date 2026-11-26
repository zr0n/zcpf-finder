"""Garante que buscas são salvas na pasta resultados/ (e não na pasta de onde o programa foi rodado)."""

from __future__ import annotations

import caminhos
import cpf_finder


def test_pasta_resultados_fica_na_raiz_do_projeto():
    assert caminhos.PASTA_RESULTADOS == caminhos.RAIZ_PROJETO / "resultados"
    assert (caminhos.RAIZ_PROJETO / "src" / "caminhos.py").exists()


def test_save_grava_em_resultados(tmp_path, monkeypatch):
    destino = tmp_path / "resultados"
    monkeypatch.setattr(caminhos, "PASTA_RESULTADOS", destino)
    monkeypatch.chdir(tmp_path)  # rodar de outra pasta não pode mudar o destino

    cpf_finder._save("XXX.456.789.XX", {"confirmados": [], "divergentes": []}, 1000, None, False)

    assert (destino / "XXX.456.789.XX.txt").exists()
    assert not (tmp_path / "XXX.456.789.XX.txt").exists()
