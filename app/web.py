"""Read-only API + static frontend for the fixture mirror."""
import datetime as dt
import json
import os
import tempfile
import zipfile

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db

RECENT_DAYS = int(os.environ.get("RECENT_DAYS", "30"))
MAX_ZIP = 300
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

db.init()
app = FastAPI(title="Avolites fixtures", docs_url=None, redoc_url=None, openapi_url=None)


def esc_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def cutoff():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RECENT_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def item(row, cut):
    if row["removed"]:
        badge = "removed"
    elif row["new_at"] and row["new_at"] >= cut:
        badge = "new"
    elif row["updated_at"] and row["updated_at"] >= cut:
        badge = "updated"
    else:
        badge = None
    return {
        "id": row["id"], "company": row["company"], "name": row["name"],
        "short_name": row["short_name"], "version": row["version"], "revised": row["revised"],
        "size": row["size"], "badge": badge, "modes": json.loads(row["modes"]),
    }


@app.get("/api/stats")
def stats():
    con = db.connect()
    try:
        total, removed, makers = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(removed),0), COUNT(DISTINCT company) FROM fixtures"
        ).fetchone()
        last = con.execute(
            "SELECT ts, status, message FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_import = con.execute(
            "SELECT ts, n_new, n_updated, n_removed FROM runs WHERE status='ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total": total, "removed": removed, "manufacturers": makers,
            "source": db.get_meta(con, "source_label"),
            "last_check": last["ts"] if last else None,
            "last_status": last["status"] if last else None,
            "last_import": dict(last_import) if last_import else None,
            "recent_days": RECENT_DAYS,
            "ready": total > 0,
        }
    finally:
        con.close()


@app.get("/api/manufacturers")
def manufacturers():
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT company, COUNT(*) AS n FROM fixtures GROUP BY company "
            "ORDER BY company COLLATE NOCASE").fetchall()
        return [{"company": r["company"], "n": r["n"]} for r in rows]
    finally:
        con.close()


@app.get("/api/fixtures")
def fixtures(
    q: str = Query("", max_length=200),
    company: str = Query("", max_length=200),
    maker: str = Query("", max_length=200),
    status: str = Query("", pattern="^(|active|new|updated|removed)$"),
    sort: str = Query("name", pattern="^(name|recent)$"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    where, params = [], []
    for tok in q.split()[:8]:
        like = "%" + esc_like(tok) + "%"
        where.append(
            "(company LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' "
            "OR short_name LIKE ? ESCAPE '\\' OR filename LIKE ? ESCAPE '\\')")
        params += [like] * 4
    if company:
        where.append("company = ? COLLATE NOCASE")
        params.append(company)
    elif maker.strip():
        where.append("company LIKE ? ESCAPE '\\'")
        params.append("%" + esc_like(maker.strip()) + "%")
    cut = cutoff()
    if status == "active":
        where.append("removed = 0")
    elif status == "removed":
        where.append("removed = 1")
    elif status == "new":
        where.append("removed = 0 AND new_at >= ?")
        params.append(cut)
    elif status == "updated":
        where.append("removed = 0 AND updated_at >= ?")
        params.append(cut)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    order = ("COALESCE(updated_at, new_at, '') DESC, company COLLATE NOCASE, name COLLATE NOCASE"
             if sort == "recent" else "company COLLATE NOCASE, name COLLATE NOCASE")
    con = db.connect()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM fixtures {sql_where}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM fixtures {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        return {"total": total, "items": [item(r, cut) for r in rows]}
    finally:
        con.close()


@app.get("/api/changes")
def changes(limit: int = Query(400, ge=1, le=1000)):
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT ch.ts, ch.kind, ch.company, ch.name, f.id AS fid, f.removed AS removed "
            "FROM changes ch LEFT JOIN fixtures f ON f.filename = ch.filename "
            "ORDER BY ch.id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r["ts"], "kind": r["kind"], "company": r["company"], "name": r["name"],
                 "id": r["fid"] if r["fid"] and not r["removed"] else None} for r in rows]
    finally:
        con.close()


def _path_for(filename):
    base = os.path.realpath(db.FILES_DIR)
    path = os.path.realpath(os.path.join(base, filename))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        raise HTTPException(404, "file missing")
    return path


@app.get("/download/{fid}")
def download(fid: int):
    con = db.connect()
    try:
        row = con.execute("SELECT filename FROM fixtures WHERE id=?", (fid,)).fetchone()
    finally:
        con.close()
    if not row:
        raise HTTPException(404, "unknown fixture")
    return FileResponse(_path_for(row["filename"]), media_type="application/octet-stream",
                        filename=row["filename"])


@app.get("/zip")
def zip_download(ids: str = Query(..., max_length=4000)):
    try:
        id_list = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "bad ids")
    if not id_list:
        raise HTTPException(400, "no ids")
    if len(id_list) > MAX_ZIP:
        raise HTTPException(400, f"max {MAX_ZIP} fixtures per zip")
    con = db.connect()
    try:
        marks = ",".join("?" * len(id_list))
        rows = con.execute(f"SELECT filename FROM fixtures WHERE id IN ({marks})", id_list).fetchall()
    finally:
        con.close()
    if not rows:
        raise HTTPException(404, "nothing found")
    tmp = tempfile.SpooledTemporaryFile(max_size=64 << 20)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            z.write(_path_for(r["filename"]), arcname=r["filename"])
    size = tmp.tell()
    tmp.seek(0)

    def stream():
        try:
            while chunk := tmp.read(1 << 20):
                yield chunk
        finally:
            tmp.close()

    name = f"avolites-fixtures-{dt.date.today():%Y%m%d}.zip"
    return StreamingResponse(stream(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Content-Length": str(size)})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
