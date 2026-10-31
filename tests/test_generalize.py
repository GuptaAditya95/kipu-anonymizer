"""Tests for quasi-identifier generalization. Jev is mocked (no network)."""
import json

import pytest

from pipeline import generalize, jev


@pytest.fixture(autouse=True)
def isolated_learned(tmp_path, monkeypatch):
    monkeypatch.setattr(generalize, "LEARNED", tmp_path / "learned.json")


def test_barrio_nearby_same_zone_and_deterministic():
    b = generalize.Barrios()
    out = b.nearby("Granada", "Cali", seed=1)
    assert out in {"Santa Mónica", "Versalles"}          # north Cali, not Granada
    assert out == b.nearby("Granada", "Cali", seed=1)    # deterministic


def test_barrio_unknown_falls_back_to_same_city():
    b = generalize.Barrios()
    cali = {x for zs in b.zones["Cali"].values() for x in zs}
    assert b.nearby("Barrio Inventado", "Cali", seed=7) in cali


def test_barrio_in_text_becomes_city():
    t = "trabajo cerca, en Granada, y entré"
    assert generalize.barrio_in_text(t, "Granada", "Cali") == "trabajo cerca, en Cali, y entré"


def test_all_35_real_occupations_map_exactly():
    occ = generalize.Occupations()
    for o in ["comerciante", "Contadora", "piloto comercial", "estudiante de medicina", "estilista"]:
        sector, src = occ.to_sector(o)
        assert src == "exact" and sector in occ.sectors


def test_keyword_rule():
    sector, src = generalize.Occupations().to_sector("ingeniera industrial")
    assert (sector, src) == ("profesional de ingeniería y tecnología", "keyword")


def test_jev_decision_is_learned_and_reused(monkeypatch):
    calls = []

    def fake_choose(instr, state, options, cache=None):
        calls.append(state)
        return ("profesional de la aviación", 0.91)

    monkeypatch.setattr(jev, "choose", fake_choose)
    occ = generalize.Occupations()
    assert occ.to_sector("auxiliar de vuelo") == ("profesional de la aviación", "jev")
    saved = json.loads(generalize.LEARNED.read_text("utf-8"))
    assert saved["auxiliar de vuelo"]["status"] == "pending_review"
    # a new instance reuses the learned decision without calling Jev again
    assert generalize.Occupations().to_sector("Auxiliar de vuelo") == ("profesional de la aviación", "learned")
    assert len(calls) == 1


def test_low_confidence_falls_back(monkeypatch):
    monkeypatch.setattr(jev, "choose", lambda *a, **k: ("técnico", 0.3))
    occ = generalize.Occupations()
    assert occ.to_sector("astrólogo") == (generalize.FALLBACK_SECTOR, "fallback")
    assert occ.misses == ["astrólogo"]


def test_jev_disabled_falls_back(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert generalize.Occupations().to_sector("astrólogo")[1] == "fallback"


def test_year_phrase():
    t = "entré con ustedes en 2025, así que"
    assert generalize.generalize_years(t) == "entré con ustedes en hace unos años, así que"


def test_jev_parse_choice_shapes():
    assert jev._parse_choice({"choice": "a", "probability": 0.8}) == ("a", 0.8)
    assert jev._parse_choice({"probabilities": {"a": 0.2, "b": 0.7}}) == ("b", 0.7)
    assert jev._parse_choice({}) is None
