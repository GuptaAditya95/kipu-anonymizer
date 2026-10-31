"""Tests for the verification layer (no network: Jev and the reasoning model are off)."""
import json

from pipeline import knowledge, reasoning, verify


def test_residual_catches_leftovers():
    r = verify.residual
    assert r("mi tía Gloria Patiño también tiene cuenta")[0]["reason"] == "possible_name"
    assert r("consígnenme a la cuenta 4512 8890 3321")[0]["reason"] == "possible_id_number"
    assert r("TITULAR: MARIA FERNANDA LOPEZ")[0]["reason"] == "possible_name_caps"
    assert r("escríbanme a laura [at] gmail [dot] com")[0]["reason"] == "possible_contact"
    assert any(h["reason"] == "surname_next_to_placeholder" for h in r("hola [NOMBRE_1] Patiño, ¿cómo vas?"))


def test_residual_ignores_traps():
    r = verify.residual
    for t in ["me tomé un tinto en Juan Valdez", "nos vemos en la Plaza Botero", "el cupo es de 8.000.000 pesos",
              "el caso PQR-2026-0451 y la cuenta CL-12538", "fecha 2026-10-01", "Hola [NOMBRE_1], soy [NOMBRE_2]",
              "Compra [NOMBRE_1] Valdez Riomar"]:
        assert not [h for h in r(t) if h["strength"] == "strong"], t


def test_decide_code_hit_wins_over_jev():
    strong = [{"strength": "strong"}]
    assert verify.decide(strong, 0.01) == "review_high"
    assert verify.decide([], 0.9) == "review_high"
    assert verify.decide([], 0.4) == "review_low"
    assert verify.decide([], 0.05) == "pass"
    assert verify.decide([], None) == "pass"


def test_masker_masks_our_values_only():
    pmap = {"entities": {"cl:1": {"entity_id": "p_1", "first": "Sofía Elena", "s1": "Zambrano", "s2": "Naranjo",
                                  "values": {"email|x": "szambrano@gmail.com", "cedula|1": "1107252808"}}}}
    m = verify.Masker(pmap, {"p_1": {"r1"}})
    out, ents = m.mask("Hola Sofía Elena Zambrano, cc 1.107.252.808, correo szambrano@gmail.com. Mi tía Gloria Patiño", "r1")
    assert "Zambrano" not in out and "1.107" not in out and "szambrano" not in out
    assert "Gloria Patiño" in out and ents == ["p_1"]
    out2, _ = m.mask("Hola Sofía, ¿todo bien?", "other_record")  # single first name: only in its records
    assert "Sofía" in out2


def test_heuristic_reasoning_and_knowledge(tmp_path):
    case = {"case_id": "c1", "record_id": "r1", "span": "4512 8890 3321", "reason": "possible_id_number",
            "context": {"registry": {}, "same_text_elsewhere": {"count": 0}}}
    ai = reasoning.heuristic(case)
    assert ai["verdict"] == "pii" and ai["proposed_fix"]["action"] == "identifier"
    kp = tmp_path / "knowledge.json"
    k = knowledge.record_answer(case, {"action": "identifier", "value": case["span"]}, kp)
    assert k["identifiers"][0]["text"] == "4512 8890 3321" and len(k["answers"]) == 1
    k = knowledge.record_answer({"case_id": "c2", "span": "Juan Valdez"}, {"action": "keep"}, kp)
    assert "Juan Valdez" in k["keep"]
    assert json.loads(kp.read_text("utf-8"))["keep"] == ["Juan Valdez"]
