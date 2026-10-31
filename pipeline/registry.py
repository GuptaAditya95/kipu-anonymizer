"""Stage 3 - Person registry: who exists, with every form of their identity.

Status: SKELETON.

Build order:
  1. clientes.csv  -> one Person per cliente_id (key "cl:<id>")
  2. empleados.xml -> one Person per KP-id (key "kp:<id>"); keep reporta_a for later mapping
  3. chats/users.json -> link to employees by @kipupay.co email; to clients by email/name;
     otherwise new Person (key "uid:<uid>")
  4. people found later in free text (detect stage) -> key "txt:<normalized name>"

Each Person stores every original form, because the judge sweeps for ALL of them:
  full name, first name, first+first surname, CAPS, no accents, filename slug,
  cedula with and without dots, phone with/without +57 and spaces, email, address,
  birth date, plate, passport, Slack handle/display name.

Hard cases handled here:
  - namesakes: key is the ID, never the name (2x "Camila Arango Velasquez")
  - agent named like a client: employees and clients are separate entries
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Person:
    key: str                      # stable key: cl:CL-12538 / kp:KP-005 / uid:U0... / txt:...
    kind: str                     # "client" | "employee" | "chat_user" | "mentioned"
    full_name: str = ""
    gender: str = "F"             # from first-name list; used for same-gender pseudonyms
    forms: set[str] = field(default_factory=set)       # every original string form
    ids: dict[str, str] = field(default_factory=dict)  # cedula, email, phone, address, birth...
    records: set[str] = field(default_factory=set)     # record ids where it appears


class Registry:
    def __init__(self):
        self.people: dict[str, Person] = {}

    def build(self, records: dict[str, list[dict]], users: dict) -> None:
        """TODO: steps 1-3 above."""
        raise NotImplementedError

    def name_variants(self, full_name: str) -> set[str]:
        """TODO: full, first, first + first surname, CAPS, accentless, slug."""
        raise NotImplementedError

    def lookup(self, span: str) -> list[Person]:
        """TODO: normalized lookup (casefold, no accents, confusables). Returns candidates;
        more than one candidate = namesakes -> resolve with strong ids in the same record."""
        raise NotImplementedError
