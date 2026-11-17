"""Load the built-in gazetteer and (optionally) GeoNames dumps supplied by the operator."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.geography.gazetteer import invalidate_gazetteer
from app.models import GeographicEntity

log = logging.getLogger(__name__)

DATA = Path(__file__).parent / "data" / "countries.psv"

CONTINENTS = {
    "AF": "Africa",
    "AN": "Antarctica",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
}


def _split(field: str) -> list[str]:
    return [p.strip() for p in field.split(";") if p.strip()]


def _city_names(spec: str) -> tuple[str, list[str]]:
    parts = [p.strip() for p in spec.split("/") if p.strip()]
    return parts[0], parts[1:]


def seed_builtin(db: Session) -> int:
    """Idempotent. Returns the number of entities created."""
    existing = db.scalar(select(GeographicEntity.id).where(GeographicEntity.data_source == "builtin").limit(1))
    if existing is not None:
        return 0
    created = 0
    continents: dict[str, GeographicEntity] = {}
    for code, name in CONTINENTS.items():
        ent = GeographicEntity(name=name, kind="continent", code=code, data_source="builtin")
        db.add(ent)
        continents[code] = ent
        created += 1
    db.flush()

    for line in DATA.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        iso2, iso3, name, cont, capital, alts, cities, demonyms, ambiguous = (line.split("|") + [""] * 9)[:9]
        # ISO3 codes are not used as aliases: all-caps headlines ("WHAT CAN…") would collide.
        alt_names = _split(alts)
        alt_names += [f"#{d}" for d in _split(demonyms)]
        country = GeographicEntity(
            name=name,
            kind="country",
            code=iso2,
            country_code=iso2,
            parent_id=continents[cont].id if cont in continents else None,
            alt_names=[a for a in alt_names if a],
            is_ambiguous=ambiguous.strip() == "1",
            data_source="builtin",
        )
        db.add(country)
        db.flush()
        created += 1
        city_specs = ([("capital", capital)] if capital else []) + [("city", c) for c in _split(cities)]
        for kind, spec in city_specs:
            amb = spec.startswith("~")
            cname, calts = _city_names(spec.lstrip("~"))
            if cname.lower() == name.lower():
                continue
            markers = ["@capital"] if kind == "capital" else []
            db.add(
                GeographicEntity(
                    name=cname,
                    kind="city",
                    country_code=iso2,
                    parent_id=country.id,
                    alt_names=calts + markers,
                    is_ambiguous=amb,
                    data_source="builtin",
                )
            )
            created += 1
    db.flush()
    invalidate_gazetteer()
    log.info("Seeded %d built-in geographic entities", created)
    return created


def import_geonames(
    db: Session,
    *,
    admin1_path: Path | None = None,
    cities_path: Path | None = None,
    min_population: int = 15000,
) -> dict[str, int]:
    """Import GeoNames admin1CodesASCII.txt and citiesNNNN.txt files downloaded by the operator.

    GeoNames data is licensed CC BY 4.0 (https://www.geonames.org/); keep the attribution in
    the deployment notes. Countries come from the built-in dataset.
    """
    countries = {
        e.country_code: e
        for e in db.scalars(select(GeographicEntity).where(GeographicEntity.kind == "country"))
        if e.country_code
    }
    known_geonames = set(db.scalars(select(GeographicEntity.geonames_id).where(GeographicEntity.geonames_id.is_not(None))))
    stats = {"admin1": 0, "cities": 0, "skipped": 0}
    admin1: dict[str, GeographicEntity] = {
        f"{e.country_code}.{e.code}": e
        for e in db.scalars(select(GeographicEntity).where(GeographicEntity.kind == "admin1"))
    }

    if admin1_path:
        with open(admin1_path, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) < 4:
                    continue
                code, name, ascii_name, gid = row[0], row[1], row[2], int(row[3])
                cc, _, a1 = code.partition(".")
                if gid in known_geonames or cc not in countries:
                    stats["skipped"] += 1
                    continue
                ent = GeographicEntity(
                    name=name, kind="admin1", code=a1, country_code=cc, parent_id=countries[cc].id,
                    geonames_id=gid, alt_names=[ascii_name] if ascii_name != name else [],
                    data_source="geonames",
                )
                db.add(ent)
                admin1[code] = ent
                known_geonames.add(gid)
                stats["admin1"] += 1
        db.flush()

    if cities_path:
        with open(cities_path, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
                if len(row) < 15:
                    continue
                gid = int(row[0])
                name, ascii_name = row[1], row[2]
                cc, a1 = row[8], row[10]
                population = int(row[14] or 0)
                if gid in known_geonames or cc not in countries or population < min_population:
                    stats["skipped"] += 1
                    continue
                parent = admin1.get(f"{cc}.{a1}") or countries[cc]
                existing_builtin = db.scalar(
                    select(GeographicEntity).where(
                        GeographicEntity.kind == "city",
                        GeographicEntity.country_code == cc,
                        GeographicEntity.name == name,
                        GeographicEntity.data_source == "builtin",
                    )
                )
                if existing_builtin is not None:
                    existing_builtin.geonames_id = gid
                    existing_builtin.population = population
                    existing_builtin.parent_id = parent.id
                    known_geonames.add(gid)
                    continue
                db.add(
                    GeographicEntity(
                        name=name, kind="city", country_code=cc, parent_id=parent.id, geonames_id=gid,
                        alt_names=[ascii_name] if ascii_name != name else [], population=population,
                        latitude=float(row[4]), longitude=float(row[5]), data_source="geonames",
                    )
                )
                known_geonames.add(gid)
                stats["cities"] += 1
        db.flush()
    invalidate_gazetteer()
    return stats
