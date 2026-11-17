"""Load the built-in gazetteer and (optionally) GeoNames dumps supplied by the operator."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.text import fold
from app.geography.gazetteer import invalidate_gazetteer
from app.models import GeographicEntity

log = logging.getLogger(__name__)

DATA = Path(__file__).parent / "data" / "countries.psv"
BD_DATA = Path(__file__).parent / "data" / "bangladesh.psv"
BN_NAMES = Path(__file__).parent / "data" / "bangla_names.psv"

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


def _rows(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            yield [p.strip() for p in line.split("|")]


def _merge_alts(ent: GeographicEntity, extra: list[str]) -> bool:
    """Add names the entity does not have yet. Returns True when something changed."""
    current = list(ent.alt_names or [])
    have = set(current) | {ent.name}
    added = [a for a in dict.fromkeys(extra) if a and a not in have]
    if not added:
        return False
    ent.alt_names = current + added  # a new list, so the JSON column is marked dirty
    return True


def seed_bangladesh(db: Session) -> dict[str, int]:
    """Bangladesh divisions, districts and towns plus Bangla names for built-in places.

    Runs on every start and is idempotent: rows are created once, then only updated when
    the bundled data files gain names. Requires the built-in countries.
    """
    stats = {"created": 0, "updated": 0, "missing": 0}
    builtin = list(db.scalars(select(GeographicEntity).where(GeographicEntity.data_source == "builtin")))
    countries = {e.country_code: e for e in builtin if e.kind == "country" and e.country_code}
    bd = countries.get("BD")
    if bd is None:
        return stats
    continents = {e.code: e for e in builtin if e.kind == "continent"}
    admin = {(e.kind, e.code): e for e in builtin if e.kind in ("admin1", "admin2") and e.country_code == "BD"}
    cities: dict[tuple[str, str], GeographicEntity] = {}
    for e in builtin:
        if e.kind == "city" and e.country_code:
            for n in [e.name, *(a.lstrip("~") for a in (e.alt_names or []))]:
                cities.setdefault((e.country_code, n), e)

    rows = list(_rows(BD_DATA))
    # A district's bare name belongs to its headquarters town when the data lists one.
    town_owned = {r[5] for r in rows if r[0] == "city" and any(
        d[0] == "district" and d[1] == r[5] and d[2] == r[2] for d in rows)}

    def upsert_admin(kind: str, code: str, name: str, alts: list[str], parent: GeographicEntity) -> GeographicEntity:
        ent = admin.get((kind, code))
        if ent is None:
            ent = GeographicEntity(name=name, kind=kind, code=code, country_code="BD", parent_id=parent.id,
                                   alt_names=alts, data_source="builtin")
            db.add(ent)
            db.flush()
            admin[(kind, code)] = ent
            stats["created"] += 1
        elif ent.name != name or list(ent.alt_names or []) != alts or ent.parent_id != parent.id:
            ent.name, ent.alt_names, ent.parent_id = name, alts, parent.id
            stats["updated"] += 1
        return ent

    for kind, code, name, bangla, other, parent_code in ((r + [""] * 6)[:6] for r in rows):
        en_names = [name, *_split(other)]
        bn_names = _split(bangla)
        if kind == "division":
            alts = [f"{n} Division" for n in en_names[1:]] + [f"{b} বিভাগ" for b in bn_names]
            upsert_admin("admin1", code, f"{name} Division", alts, bd)
        elif kind == "district":
            parent = admin.get(("admin1", parent_code))
            if parent is None:
                stats["missing"] += 1
                continue
            alts = [f"{n} District" for n in en_names[1:]] + [f"{b} জেলা" for b in bn_names]
            if code not in town_owned:
                alts += en_names + bn_names
            upsert_admin("admin2", code, f"{name} District", alts, parent)
        elif kind == "city":
            district = admin.get(("admin2", parent_code))
            if district is None:
                stats["missing"] += 1
                continue
            ent = next((cities[("BD", n)] for n in en_names if ("BD", n) in cities), None)
            if ent is None:
                ent = GeographicEntity(name=name, kind="city", country_code="BD", parent_id=district.id,
                                       alt_names=en_names[1:] + bn_names, data_source="builtin")
                db.add(ent)
                for n in en_names:
                    cities[("BD", n)] = ent
                stats["created"] += 1
                continue
            changed = _merge_alts(ent, [n for n in en_names if n != ent.name] + bn_names)
            if ent.parent_id != district.id:
                ent.parent_id = district.id
                changed = True
            stats["updated"] += int(changed)

    for r in _rows(BN_NAMES):
        kind = r[0]
        if kind == "continent":
            ent, extra = continents.get(r[1]), _split(r[2])
        elif kind == "country":
            ent = countries.get(r[1])
            extra = _split(r[2]) + [f"#{d}" for d in _split(r[3] if len(r) > 3 else "")]
        else:
            ent, extra = cities.get((r[1], r[2])), _split(r[3])
        if ent is None:
            stats["missing"] += 1
            log.warning("Bangla names: no built-in %s for %s", kind, "|".join(r[1:3]))
            continue
        stats["updated"] += int(_merge_alts(ent, extra))

    if stats["created"] or stats["updated"]:
        db.flush()
        invalidate_gazetteer()
        log.info("Bangladesh gazetteer: %d created, %d updated", stats["created"], stats["updated"])
    return stats


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
    # Countries with curated built-in regions (Bangladesh) keep them instead of GeoNames'.
    curated = {e.country_code for e in admin1.values() if e.data_source == "builtin"}
    # Built-in places by country and folded name, so GeoNames does not add a second copy.
    builtin_names: dict[tuple[str, str], GeographicEntity] = {}
    for e in db.scalars(
        select(GeographicEntity).where(
            GeographicEntity.data_source == "builtin", GeographicEntity.kind.in_(("admin1", "admin2", "city"))
        )
    ):
        for n in [e.name, *(a.lstrip("~") for a in (e.alt_names or []) if a[:1] not in "@#=")]:
            builtin_names.setdefault((e.country_code or "", fold(n)), e)

    if admin1_path:
        with open(admin1_path, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) < 4:
                    continue
                code, name, ascii_name, gid = row[0], row[1], row[2], int(row[3])
                cc, _, a1 = code.partition(".")
                if gid in known_geonames or cc not in countries or cc in curated:
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
                existing_builtin = builtin_names.get((cc, fold(name))) or builtin_names.get((cc, fold(ascii_name)))
                if existing_builtin is not None and existing_builtin.kind != "city":
                    # A curated district/region already owns this name (e.g. "Bogra").
                    stats["skipped"] += 1
                    continue
                if existing_builtin is not None:
                    if existing_builtin.geonames_id is None:
                        existing_builtin.geonames_id = gid
                        existing_builtin.population = population
                    # Only refine the parent; never move a curated town back up to its country.
                    if existing_builtin.parent_id == countries[cc].id:
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
