"""Daily job: watch the Avolites personality site, import new/changed .d4 files.

    python -m app.updater            # run forever, check once a day
    python -m app.updater --once     # single check, then exit
    python -m app.updater --once --force   # ignore cached etag, re-import

Env:
    UPDATE_AT   HH:MM (UTC) daily check time, default 03:30
    SOURCE_URL  override installer URL (default: newest TitanFixtureLibraryV*.exe)
    LOCAL_EXE   import this local installer instead of downloading (testing)
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

from . import db

BASE = "https://personalities.avolites.com"
DOWNLOADS_PAGE = BASE + "/Downloads.asp"
UA = "avolites-fixtures-mirror/1.0 (+https://avolites.olliecross.com)"
UPDATE_AT = os.environ.get("UPDATE_AT", "03:30")
SOURCE_URL = os.environ.get("SOURCE_URL", "")
LOCAL_EXE = os.environ.get("LOCAL_EXE", "")
TMP_DIR = os.path.join(db.DATA_DIR, "tmp")
RETRY_AFTER_ERROR = 3600


def log(msg):
    print(f"[updater {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60)


# ---------------------------------------------------------------- source ----

def find_latest_installer():
    """Return (url, label) of the newest Titan fixture library installer."""
    if SOURCE_URL:
        return SOURCE_URL, os.path.basename(SOURCE_URL)
    html = http(DOWNLOADS_PAGE).read().decode("utf-8", "replace")
    found = re.findall(r'/PersonalityFiles/Downloads/(TitanFixtureLibraryV(\d+)\.exe)', html)
    if not found:
        raise RuntimeError("no TitanFixtureLibraryV*.exe link found on Downloads.asp")
    fname, _ = max(found, key=lambda m: int(m[1]))
    label = fname
    m = re.search(re.escape(fname) + r'"[^>]*>.*?<span[^>]*>([^<]+)</span>', html, re.S)
    if m:
        label = "Titan " + m.group(1).strip()
    return f"{BASE}/PersonalityFiles/Downloads/{fname}", label


def remote_signature(url):
    with http(url, "HEAD") as r:
        h = r.headers
        return {
            "url": url,
            "etag": h.get("ETag", ""),
            "lastmod": h.get("Last-Modified", ""),
            "size": h.get("Content-Length", ""),
        }


def download(url, dest, expect_size):
    for attempt in range(1, 4):
        try:
            log(f"downloading {url} (attempt {attempt})")
            with http(url) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            got = os.path.getsize(dest)
            if expect_size and int(expect_size) != got:
                raise RuntimeError(f"size mismatch: expected {expect_size}, got {got}")
            return
        except Exception as e:  # noqa: BLE001
            log(f"download failed: {e}")
            if attempt == 3:
                raise
            time.sleep(30 * attempt)


def extract_d4(exe, out_dir):
    log("extracting *.d4 from installer")
    subprocess.run(
        ["7z", "x", "-y", "-r", "-bso0", "-bsp0", exe, "*.d4", f"-o{out_dir}"],
        check=True,
    )
    found = []
    for root, _, files in os.walk(out_dir):
        for fn in files:
            if fn.lower().endswith(".d4"):
                found.append(os.path.join(root, fn))
    return found


# ---------------------------------------------------------------- parsing ---

def parse_d4(path):
    """Pull listing metadata out of the .d4 XML. Falls back to the filename."""
    base = os.path.basename(path)[:-3]
    company, _, name = base.partition("_")
    info = {"company": company or "Unknown", "name": name or base,
            "short_name": "", "version": "", "revised": None, "modes": []}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return info
    info["company"] = (root.get("Company") or info["company"]).strip() or "Unknown"
    info["name"] = (root.get("Name") or info["name"]).strip()
    info["short_name"] = (root.get("ShortName") or "").strip()
    info["version"] = (root.get("Version") or "").strip()
    for h in root.findall("History"):
        try:
            d = dt.datetime.strptime(h.get("Date", ""), "%d-%m-%y").date().isoformat()
        except ValueError:
            continue
        if info["revised"] is None or d > info["revised"]:
            info["revised"] = d
    for m in root.findall("Mode"):
        try:
            ch = int(m.get("Channels", "0"))
        except ValueError:
            ch = 0
        info["modes"].append({"n": m.get("Name", ""), "c": ch})
    return info


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- import ----

def import_files(con, paths, label, run_id):
    ts = now_iso()
    baseline = con.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0] == 0
    existing = {r["filename"]: r for r in con.execute("SELECT * FROM fixtures")}
    active_before = sum(1 for r in existing.values() if not r["removed"])
    if active_before and len(paths) < active_before * 0.5:
        raise RuntimeError(
            f"extraction suspicious: {len(paths)} files vs {active_before} known; aborting")

    seen = set()
    counts = {"new": 0, "updated": 0, "removed": 0, "unchanged": 0}

    def record(kind, fn, info):
        counts[kind] += 1
        if not baseline:
            con.execute(
                "INSERT INTO changes(run_id, ts, kind, filename, company, name) VALUES(?,?,?,?,?,?)",
                (run_id, ts, kind, fn, info["company"], info["name"]))

    for path in sorted(paths):
        fn = os.path.basename(path)
        digest = sha256_of(path)
        size = os.path.getsize(path)
        row = existing.get(fn)
        seen.add(fn)
        if row is not None and row["sha256"] == digest and not row["removed"]:
            counts["unchanged"] += 1
            continue
        info = parse_d4(path)
        dest = os.path.join(db.FILES_DIR, fn)
        shutil.copyfile(path, dest + ".part")
        os.replace(dest + ".part", dest)
        modes = json.dumps(info["modes"], separators=(",", ":"))
        if row is None:
            con.execute(
                "INSERT INTO fixtures(filename, company, name, short_name, version, revised, modes,"
                " sha256, size, first_seen, new_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (fn, info["company"], info["name"], info["short_name"], info["version"],
                 info["revised"], modes, digest, size, ts, None if baseline else ts))
            record("new", fn, info)
        else:
            con.execute(
                "UPDATE fixtures SET company=?, name=?, short_name=?, version=?, revised=?, modes=?,"
                " sha256=?, size=?, updated_at=?, removed=0, removed_at=NULL WHERE id=?",
                (info["company"], info["name"], info["short_name"], info["version"],
                 info["revised"], modes, digest, size, ts, row["id"]))
            record("updated", fn, info)

    for fn, row in existing.items():
        if fn not in seen and not row["removed"]:
            con.execute("UPDATE fixtures SET removed=1, removed_at=? WHERE id=?", (ts, row["id"]))
            record("removed", fn, {"company": row["company"], "name": row["name"]})
    return counts


# ------------------------------------------------------------------ run -----

def run_update(force=False):
    db.init()
    con = db.connect()
    ts = now_iso()
    cur = con.execute("INSERT INTO runs(ts, status) VALUES(?, 'running')", (ts,))
    run_id = cur.lastrowid
    con.commit()
    work = None
    try:
        if LOCAL_EXE:
            sig = {"url": "local:" + LOCAL_EXE, "etag": "local",
                   "lastmod": "", "size": str(os.path.getsize(LOCAL_EXE))}
            label = "local test installer"
        else:
            url, label = find_latest_installer()
            sig = remote_signature(url)
        sig_json = json.dumps(sig, sort_keys=True)
        have = con.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
        if not force and have and db.get_meta(con, "source_sig") == sig_json:
            log("installer unchanged, nothing to do")
            con.execute("UPDATE runs SET status='unchanged', source_label=? WHERE id=?",
                        (label, run_id))
            con.commit()
            return True

        shutil.rmtree(TMP_DIR, ignore_errors=True)
        work = os.path.join(TMP_DIR, "work")
        os.makedirs(work)
        if LOCAL_EXE:
            exe = LOCAL_EXE
        else:
            exe = os.path.join(TMP_DIR, "installer.exe")
            download(sig["url"], exe, sig["size"])
        paths = extract_d4(exe, os.path.join(work, "x"))
        log(f"found {len(paths)} .d4 files")
        if not paths:
            raise RuntimeError("installer contained no .d4 files")
        counts = import_files(con, paths, label, run_id)
        db.set_meta(con, "source_sig", sig_json)
        db.set_meta(con, "source_label", label)
        con.execute(
            "UPDATE runs SET status='ok', source_label=?, n_new=?, n_updated=?, n_removed=?,"
            " n_unchanged=? WHERE id=?",
            (label, counts["new"], counts["updated"], counts["removed"], counts["unchanged"], run_id))
        con.commit()
        log(f"done: {counts}")
        return True
    except Exception as e:  # noqa: BLE001
        con.rollback()
        con.execute("UPDATE runs SET status='error', message=? WHERE id=?", (str(e)[:500], run_id))
        con.commit()
        log(f"ERROR: {e}")
        return False
    finally:
        con.close()
        if not LOCAL_EXE:
            shutil.rmtree(TMP_DIR, ignore_errors=True)
        elif work:
            shutil.rmtree(work, ignore_errors=True)


def seconds_until_next():
    hh, mm = (int(x) for x in UPDATE_AT.split(":"))
    now = dt.datetime.now(dt.timezone.utc)
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt += dt.timedelta(days=1)
    return (nxt - now).total_seconds()


def main():
    once = "--once" in sys.argv
    force = "--force" in sys.argv
    ok = run_update(force=force)
    if once:
        sys.exit(0 if ok else 1)
    while True:
        wait = seconds_until_next() if ok else RETRY_AFTER_ERROR
        log(f"next check in {int(wait // 60)} min")
        time.sleep(wait)
        ok = run_update()


if __name__ == "__main__":
    main()
