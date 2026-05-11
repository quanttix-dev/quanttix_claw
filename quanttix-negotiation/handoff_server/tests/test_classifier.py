"""
Tests do classifier do telegram_webhook — keyword-based, sem dependencias
externas. Pode rodar com `pytest` puro a partir do diretorio handoff_server/.
"""

import pytest

from handoff_server.telegram_webhook import classify_reply


class TestClassifyReply:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("sim",             "ACCEPT"),
            ("Sim, aceito",     "ACCEPT"),
            ("aceito",          "ACCEPT"),
            ("fechado",         "ACCEPT"),
            ("ok",              "ACCEPT"),
            ("tudo bem, pode fechar", "ACCEPT"),
            ("nao",             "REJECT"),
            ("não",             "REJECT"),
            ("não quero",       "REJECT"),
            ("sem interesse",   "REJECT"),
            ("recuso",          "REJECT"),
            ("podemos negociar 8%?", "COUNTER"),
            ("",                "COUNTER"),
            ("apenas com parcelamento", "COUNTER"),
            ("amanha respondo", "COUNTER"),
        ],
    )
    def test_classification(self, text, expected):
        assert classify_reply(text) == expected
