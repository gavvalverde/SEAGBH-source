"""Entirely invented input source, with no expected judgement or oracle."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    id: str
    path: str
    before: str
    after: str


CASES = (
    Case("K68E-01", "fictional/window.py",
         "def accepts(hour):\n    return 9 <= hour <= 18\n",
         "def accepts(hour):\n    return 9 <= hour < 18\n"),
    Case("K68E-02", "fictional/parcel.py",
         "def handoff(parcel):\n    if parcel['status'] != 'packed':\n        return False\n    parcel['status'] = 'sent'\n    return True\n",
         "def handoff(parcel):\n    if parcel['status'] == 'sent':\n        return False\n    parcel['status'] = 'sent'\n    return True\n"),
    Case("K68E-03", "fictional/shelves.py",
         "def names(rows):\n    return [row[0] for row in rows if row[1]]\n",
         "def names(rows):\n    return [name for name, active in rows if active]\n"),
    Case("K68E-04", "fictional/ribbon.py",
         "def suffix(labels):\n    return labels[-1]\n",
         "def suffix(labels):\n    return labels[-1] if labels else None\n"),
)
