from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analytics import queries as Q
from app.api.common import csv_response
from app.core.db import get_db
from app.core.errors import bad_request
from app.security import permissions as P
from app.security.deps import CurrentUser, require

router = APIRouter(prefix="/analytics", tags=["analytics"])

ReportName = Literal["volume", "countries", "publishers", "categories", "assignments", "sources", "publications", "throughput"]


@router.get("/overview")
def overview(tz: str | None = Query(None, max_length=64), db: Session = Depends(get_db), _: CurrentUser = Depends(require(P.READ))):
    """Dashboard numbers. `tz` is the viewer's IANA timezone so that "today" means their day."""
    return Q.overview(db, tz)


@router.get("/reports/{name}")
def report(
    name: ReportName,
    start: date | None = None,
    end: date | None = None,
    format: Literal["json", "csv"] = "json",
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require(P.READ)),
):
    s, e = Q.date_range(start, end)
    if s > e:
        raise bad_request("start must be before end")
    if (e - s).days > 366:
        raise bad_request("Reports cover at most one year")
    rows = Q.REPORTS[name](db, s, e)
    if format == "csv":
        return csv_response(rows, f"newswire-{name}-{s.date()}-{e.date()}.csv")
    return {"report": name, "start": s, "end": e, "rows": rows}
