"""SQLite persistence. Every write is an upsert so reruns on the same day never duplicate rows."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from models import CoeResult, FuelPrice, NewEvVariant, PricePoint, UsedListing

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_date TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    sent_at TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    notes TEXT
);
CREATE TABLE IF NOT EXISTS coe_results (
    tender_date TEXT NOT NULL,
    exercise TEXT NOT NULL,
    category TEXT NOT NULL,
    quota_premium INTEGER NOT NULL,
    quota INTEGER,
    bids_received INTEGER,
    bids_successful INTEGER,
    source TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    PRIMARY KEY (tender_date, category)
);
CREATE TABLE IF NOT EXISTS new_ev_prices (
    scraped_on TEXT NOT NULL,
    source TEXT NOT NULL,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',
    price_with_coe INTEGER,
    price_without_coe INTEGER,
    coe_category TEXT,
    ves_band TEXT,
    ves_rebate INTEGER,
    battery_kwh REAL,
    range_km INTEGER,
    range_standard TEXT,
    power_kw REAL,
    vehicle_warranty TEXT,
    battery_warranty TEXT,
    battery_warranty_years REAL,
    promotion TEXT,
    body_type TEXT,
    listing_url TEXT NOT NULL,
    price_source_url TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    PRIMARY KEY (scraped_on, source, make, model, variant)
);
CREATE TABLE IF NOT EXISTS used_listings (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    url TEXT NOT NULL,
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',
    drivetrain TEXT NOT NULL,
    year INTEGER,
    reg_date TEXT,
    mileage_km INTEGER,
    owners INTEGER,
    price INTEGER NOT NULL,
    depreciation_per_year INTEGER,
    coe_expiry TEXT,
    coe_years_remaining REAL,
    omv INTEGER,
    arf INTEGER,
    dereg_value INTEGER,
    engine_cc INTEGER,
    power_kw REAL,
    seller_type TEXT,
    battery_health TEXT,
    flags TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    scraped_at TEXT NOT NULL,
    PRIMARY KEY (source, listing_id)
);
CREATE TABLE IF NOT EXISTS price_history (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    seen_on TEXT NOT NULL,
    price INTEGER NOT NULL,
    PRIMARY KEY (source, listing_id, seen_on)
);
CREATE TABLE IF NOT EXISTS sent_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fuel_prices (
    observed_on TEXT PRIMARY KEY,
    ron95_per_litre REAL NOT NULL,
    by_brand TEXT NOT NULL DEFAULT '{}',
    station_prices TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    scraped_at TEXT NOT NULL
);
"""


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Columns added after the first release. CREATE TABLE IF NOT EXISTS skips existing tables.
        try:
            self.conn.execute("ALTER TABLE fuel_prices ADD COLUMN grades TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass  # already there

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # Runs and idempotency

    def start_run(self, run_date: date) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO runs (run_date, started_at, status) VALUES (?, ?, 'started') "
                "ON CONFLICT(run_date) DO UPDATE SET started_at = excluded.started_at, status = 'started'",
                (run_date.isoformat(), datetime.now().isoformat(timespec="seconds")),
            )

    def finish_run(self, run_date: date, status: str = "ok", notes: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at = ?, status = ?, notes = ? WHERE run_date = ?",
                (datetime.now().isoformat(timespec="seconds"), status, notes, run_date.isoformat()),
            )

    def mark_sent(self, run_date: date) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET sent_at = ? WHERE run_date = ?",
                (datetime.now().isoformat(timespec="seconds"), run_date.isoformat()),
            )

    def already_sent(self, run_date: date) -> bool:
        row = self.conn.execute(
            "SELECT sent_at FROM runs WHERE run_date = ?", (run_date.isoformat(),)
        ).fetchone()
        return bool(row and row["sent_at"])

    # COE

    def upsert_coe_results(self, results: Iterable[CoeResult]) -> int:
        n = 0
        with self.tx() as c:
            for r in results:
                c.execute(
                    """INSERT INTO coe_results
                       (tender_date, exercise, category, quota_premium, quota, bids_received,
                        bids_successful, source, scraped_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(tender_date, category) DO UPDATE SET
                         exercise = excluded.exercise,
                         quota_premium = excluded.quota_premium,
                         quota = COALESCE(excluded.quota, coe_results.quota),
                         bids_received = COALESCE(excluded.bids_received, coe_results.bids_received),
                         bids_successful = COALESCE(excluded.bids_successful, coe_results.bids_successful),
                         source = excluded.source,
                         scraped_at = excluded.scraped_at""",
                    (
                        r.tender_date.isoformat(), r.exercise, r.category.value, r.quota_premium,
                        r.quota, r.bids_received, r.bids_successful, r.source,
                        r.scraped_at.isoformat(timespec="seconds"),
                    ),
                )
                n += 1
        return n

    def coe_keys(self) -> set[tuple[str, str]]:
        return {(r["tender_date"], r["category"]) for r in self.conn.execute("SELECT tender_date, category FROM coe_results")}

    def fill_coe_details(self, results: Iterable[CoeResult]) -> None:
        """Quota and bid counts from a fuller source into rows that lack them; premiums are never touched."""
        with self.tx() as c:
            for r in results:
                c.execute(
                    "UPDATE coe_results SET quota = COALESCE(quota, ?), bids_received = COALESCE(bids_received, ?),"
                    " bids_successful = COALESCE(bids_successful, ?) WHERE tender_date = ? AND category = ?",
                    (r.quota, r.bids_received, r.bids_successful, r.tender_date.isoformat(), r.category.value),
                )

    def drop_coe_source(self, source: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM coe_results WHERE source = ?", (source,))

    def coe_history(self, category: str, limit: int = 6) -> list[CoeResult]:
        rows = self.conn.execute(
            "SELECT * FROM coe_results WHERE category = ? ORDER BY tender_date DESC LIMIT ?",
            (category, limit),
        ).fetchall()
        return [CoeResult(**dict(r)) for r in rows]

    def latest_coe(self) -> list[CoeResult]:
        row = self.conn.execute("SELECT MAX(tender_date) AS d FROM coe_results").fetchone()
        if not row or not row["d"]:
            return []
        rows = self.conn.execute(
            "SELECT * FROM coe_results WHERE tender_date = ? ORDER BY category", (row["d"],)
        ).fetchall()
        return [CoeResult(**dict(r)) for r in rows]

    # New EVs

    def upsert_new_ev(self, variants: Iterable[NewEvVariant], scraped_on: date) -> int:
        n = 0
        with self.tx() as c:
            for v in variants:
                c.execute(
                    """INSERT OR REPLACE INTO new_ev_prices
                       (scraped_on, source, make, model, variant, price_with_coe, price_without_coe,
                        coe_category, ves_band, ves_rebate, battery_kwh, range_km, range_standard,
                        power_kw, vehicle_warranty, battery_warranty, battery_warranty_years,
                        promotion, body_type, listing_url, price_source_url, scraped_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scraped_on.isoformat(), v.source, v.make, v.model, v.variant,
                        v.price_with_coe, v.price_without_coe,
                        v.coe_category.value if v.coe_category else None, v.ves_band, v.ves_rebate,
                        v.battery_kwh, v.range_km, v.range_standard, v.power_kw,
                        v.vehicle_warranty, v.battery_warranty, v.battery_warranty_years,
                        v.promotion, v.body_type, v.listing_url, v.price_source_url,
                        v.scraped_at.isoformat(timespec="seconds"),
                    ),
                )
                n += 1
        return n

    def new_ev_on(self, scraped_on: date) -> list[NewEvVariant]:
        rows = self.conn.execute(
            "SELECT * FROM new_ev_prices WHERE scraped_on = ?", (scraped_on.isoformat(),)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("scraped_on")
            out.append(NewEvVariant(**d))
        return out

    # Used listings

    def upsert_used_listings(self, listings: Iterable[UsedListing], seen_on: date) -> dict[str, int]:
        """Insert or refresh listings. Returns counts of new listings and price drops."""
        stats = {"new": 0, "updated": 0, "drops": 0}
        with self.tx() as c:
            for l in listings:
                existing = c.execute(
                    "SELECT price, first_seen FROM used_listings WHERE source = ? AND listing_id = ?",
                    (l.source, l.listing_id),
                ).fetchone()
                first_seen = existing["first_seen"] if existing else seen_on.isoformat()
                if existing is None:
                    stats["new"] += 1
                else:
                    stats["updated"] += 1
                    if l.price < existing["price"]:
                        stats["drops"] += 1
                c.execute(
                    """INSERT INTO used_listings
                       (source, listing_id, url, make, model, variant, drivetrain, year, reg_date,
                        mileage_km, owners, price, depreciation_per_year, coe_expiry,
                        coe_years_remaining, omv, arf, dereg_value, engine_cc, power_kw, seller_type,
                        battery_health, flags, description, first_seen, last_seen, status, scraped_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                       ON CONFLICT(source, listing_id) DO UPDATE SET
                         url = excluded.url, make = excluded.make, model = excluded.model,
                         variant = excluded.variant, drivetrain = excluded.drivetrain,
                         year = excluded.year, reg_date = excluded.reg_date,
                         mileage_km = excluded.mileage_km, owners = excluded.owners,
                         price = excluded.price, depreciation_per_year = excluded.depreciation_per_year,
                         coe_expiry = excluded.coe_expiry, coe_years_remaining = excluded.coe_years_remaining,
                         omv = excluded.omv, arf = excluded.arf, dereg_value = excluded.dereg_value,
                         engine_cc = excluded.engine_cc,
                         power_kw = excluded.power_kw, seller_type = excluded.seller_type,
                         battery_health = excluded.battery_health, flags = excluded.flags,
                         description = excluded.description, last_seen = excluded.last_seen,
                         status = 'active', scraped_at = excluded.scraped_at""",
                    (
                        l.source, l.listing_id, l.url, l.make, l.model, l.variant, l.drivetrain.value,
                        l.year, _iso(l.reg_date), l.mileage_km, l.owners, l.price,
                        l.depreciation_per_year, _iso(l.coe_expiry), l.coe_years_remaining,
                        l.omv, l.arf, l.dereg_value, l.engine_cc, l.power_kw, l.seller_type, l.battery_health,
                        json.dumps(l.flags), l.description, first_seen, seen_on.isoformat(),
                        l.scraped_at.isoformat(timespec="seconds"),
                    ),
                )
                c.execute(
                    "INSERT OR REPLACE INTO price_history (source, listing_id, seen_on, price) VALUES (?, ?, ?, ?)",
                    (l.source, l.listing_id, seen_on.isoformat(), l.price),
                )
        return stats

    def mark_gone(self, source: str, seen_on: date) -> int:
        """Mark listings from a source that were not seen today as gone. Returns how many changed."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE used_listings SET status = 'gone' WHERE source = ? AND status = 'active' AND last_seen < ?",
                (source, seen_on.isoformat()),
            )
            return cur.rowcount

    def price_history(self, source: str, listing_id: str) -> list[PricePoint]:
        rows = self.conn.execute(
            "SELECT seen_on, price FROM price_history WHERE source = ? AND listing_id = ? ORDER BY seen_on",
            (source, listing_id),
        ).fetchall()
        return [PricePoint(seen_on=date.fromisoformat(r["seen_on"]), price=r["price"]) for r in rows]

    def active_listings(self, drivetrains: Iterable[str] | None = None) -> list[UsedListing]:
        sql = "SELECT * FROM used_listings WHERE status = 'active'"
        params: list = []
        if drivetrains:
            dts = list(drivetrains)
            sql += " AND drivetrain IN (%s)" % ",".join("?" * len(dts))
            params.extend(dts)
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("status")
            d["flags"] = json.loads(d["flags"] or "[]")
            listing = UsedListing(**d)
            listing.price_history = self.price_history(listing.source, listing.listing_id)
            out.append(listing)
        return out

    def count_gone_since(self, since: date) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM used_listings WHERE status = 'gone' AND last_seen >= ?",
            (since.isoformat(),),
        ).fetchone()
        return int(row["n"])

    # Sent state, used to decide whether today's report differs from the last one sent

    def get_state(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM sent_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO sent_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, datetime.now().isoformat(timespec="seconds")),
            )

    def last_sent_date(self) -> Optional[date]:
        row = self.conn.execute("SELECT MAX(run_date) AS d FROM runs WHERE sent_at IS NOT NULL").fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    # Fuel

    def upsert_fuel_price(self, fp: FuelPrice) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO fuel_prices (observed_on, ron95_per_litre, by_brand, station_prices, grades, source, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fp.observed_on.isoformat(), fp.ron95_per_litre, json.dumps(fp.by_brand), json.dumps(fp.station_prices), json.dumps(fp.grades), fp.source, fp.scraped_at.isoformat(timespec="seconds")),
            )

    def latest_fuel_price(self) -> Optional[FuelPrice]:
        row = self.conn.execute("SELECT * FROM fuel_prices ORDER BY observed_on DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["by_brand"] = json.loads(d.get("by_brand") or "{}")
        d["station_prices"] = json.loads(d.get("station_prices") or "{}")
        d["grades"] = json.loads(d.get("grades") or "{}")
        return FuelPrice(**d)
