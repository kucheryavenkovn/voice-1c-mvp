"""Lemmatization to nominative singular for stock matching (pymorphy3)."""

import pytest

pytest.importorskip("pymorphy3")
import onec


@pytest.mark.parametrize(
    "word,lemma",
    [
        ("телевизоры", "телевизор"),
        ("телевизоров", "телевизор"),
        ("стулья", "стул"),
        ("молока", "молоко"),
        ("сахара", "сахар"),
        ("конфет", "конфета"),
    ],
)
def test_lemmatize_russian_plural_and_cases(word, lemma):
    assert onec._lemmatize(word) == lemma


@pytest.mark.parametrize("token", ["7777", "SHARP", "Арт-7777", "Т-123456"])
def test_lemmatize_keeps_codes_and_latin(token):
    assert onec._lemmatize(token) == token


def test_build_query_lemmatizes_plural_multiword():
    q = onec._build_query("телевизоры SHARP")
    # телевизоры -> телевизор; SHARP (latin) untouched
    assert q.count('ВРЕГ("%телевизор%SHARP%")') == 2


def test_build_query_lemmatizes_single_plural():
    q = onec._build_query("стулья")
    assert 'ВРЕГ("%стул%")' in q
