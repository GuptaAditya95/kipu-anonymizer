"""Stage 6b - Generalize quasi-identifiers (neighborhood + occupation + year).

Status: SKELETON.

From the sample's ideal output:
  occupation "fotografa"  -> "profesional de las industrias creativas"
  occupation "periodista" -> "profesional de medios"
  barrio "Granada" (Cali) -> another barrio of the same city
  "entre en 2025"          -> "entre en hace unos anos" (only in identifying context)
  city and fecha_vinculacion stay intact

Must be applied to the clientes row AND to every text that mentions that person
(the indirect leak is judged on the union of sources).

TODO: fill resources/occupation_sector.json and resources/barrios_by_city.json
"""
from __future__ import annotations


def generalize_occupation(value: str, table: dict) -> str:
    return table.get(value.strip().lower(), value)


def generalize_barrio(value: str, city: str, seed: int, barrios_by_city: dict) -> str:
    options = [b for b in barrios_by_city.get(city, []) if b != value]
    return options[seed % len(options)] if options else value
