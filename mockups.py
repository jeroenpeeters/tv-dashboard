#!/usr/bin/env python3
"""Render the morning board with made-up data for the main weather types, to review the design.

Writes one PNG per case to out/mockups/ plus a contact sheet. No Home Assistant access needed.
"""
from pathlib import Path

from PIL import Image

import tvdash

OUT = tvdash.ROOT / "out" / "mockups"


def person(name: str, *items: tuple[str | None, str]) -> dict:
    return {"name": name, "items": [{"time": t, "title": title} for t, title in items]}


NEWS = [
    "Kabinet presenteert nieuwe plannen voor woningbouw",
    "Treinen rijden komend weekend minder door werkzaamheden",
    "Ajax wint spektakelstuk in de Johan Cruijff ArenA",
]

# A normal, busy and an empty agenda so the layout is checked against all three.
NORMAL = [
    person("Ben", (None, "🏢 Op kantoor"), ("12:30", "🚌 Kids uit"), ("19:30", "Padel met Tim")),
    person("Anna", ("08:30", "Tandarts"), ("16:00", "Developer meeting 📅")),
    person("Kids", ("08:30", "Schoolreisje groep 6"), ("16:00", "Zwemles C-diploma"), ("18:00", "Rugby training")),
]
BUSY = [
    person("Ben", (None, "🏢 Op kantoor"), ("08:15", "Standup"), ("10:00", "Klantgesprek over de planning van Q1"),
           ("12:30", "🚌 Kids uit"), ("14:00", "Retro"), ("15:30", "Bellen met de accountant"), ("17:00", "Boodschappen doen")),
    person("Anna", (None, "🐺"), ("08:30", "APK en kleine beurt"), ("09:30", "Overleg"), ("12:00", "Lunch met Sophie"),
           ("16:00", "Company session including food and drink"), ("19:00", "Yoga")),
    person("Kids", ("08:30", "Ouderavond groep 6"), ("09:30", "Fluitles"), ("14:30", "HPV-vaccinatie"),
           ("16:00", "Zwemles C-diploma"), ("17:00", "Voetbaltraining"), ("18:00", "Rugby training"), ("19:00", "Kinderfeestje")),
]
EMPTY = [person("Ben"), person("Anna"), person("Kids")]

#   name, date, weather(code, min, max, legs, jacket, umbrella), agenda, wissel, bins, bags
CASES = [
    ("01-zonnig-warm", "2026-07-08", (0, 16, 27, "Korte broek", "Geen jas", False), NORMAL, "mama", ["Restafval"], ["Zwemspullen"]),
    ("02-zonnig-koud", "2026-12-09", (0, -2, 5, "Lange broek", "Dikke jas", False), NORMAL, None, ["Papier"], []),
    ("03-half-bewolkt", "2026-05-13", (2, 11, 19, "Lange broek", "Dunne jas", False), NORMAL, None, ["PMD"], ["Gymspullen"]),
    ("04-bewolkt", "2026-09-16", (3, 9, 14, "Lange broek", "Dunne jas", False), BUSY, "papa", [], ["Rugbyspullen"]),
    ("05-mist", "2026-11-11", (45, 3, 8, "Lange broek", "Dikke jas", False), NORMAL, "papa", ["GFT"], []),
    ("06-motregen", "2026-10-14", (51, 9, 14, "Lange broek", "Dunne jas", True), NORMAL, None, ["GFT", "PMD"], ["Zwemspullen", "Gymspullen"]),
    ("07-regen", "2026-11-04", (63, 7, 11, "Lange broek", "Dunne jas", True), BUSY, "mama", [], ["Sportkleding aan"]),
    ("08-sneeuw", "2026-01-14", (73, -4, 0, "Lange broek", "Dikke jas", False), EMPTY, None, [], []),
    ("09-onweer", "2026-08-12", (95, 17, 24, "Lange broek", "Dunne jas", True), NORMAL, None, ["Restafval"], []),
    ("10-heet", "2026-07-22", (1, 21, 33, "Korte broek", "Geen jas", False), NORMAL, None, [], ["Zwemspullen"]),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, day, (code, lo, hi, legs, jacket, umbrella), people, wissel, bins, bags in CASES:
        people = [dict(p) for p in people]
        if wissel:
            people[2]["wissel"] = wissel   # the Kids card
        data = {
            "date": day, "weekday": 0, "bins": bins, "bags": bags, "people": people, "news": NEWS,
            "weather": {"code": code, "min": lo, "max": hi, "legs": legs, "jacket": jacket, "umbrella": umbrella},
        }
        tvdash.render(tvdash.MORNING.as_uri(), OUT / f"{name}.png", data)
        print("wrote", OUT / f"{name}.png")

    # Contact sheet: two columns, five rows, each tile 960x540.
    sheet = Image.new("RGB", (1920, 540 * 5))
    for i, case in enumerate(CASES):
        tile = Image.open(OUT / f"{case[0]}.png").convert("RGB").resize((960, 540), Image.LANCZOS)
        sheet.paste(tile, ((i % 2) * 960, (i // 2) * 540))
    sheet.save(OUT / "overzicht.png")
    print("wrote", OUT / "overzicht.png")


if __name__ == "__main__":
    main()
