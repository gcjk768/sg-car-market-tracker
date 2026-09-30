"""Builds fixtures/lta_m03.xlsx, a SYNTHETIC stand in for LTA table M03 as a spreadsheet.

Built on 2026-09-30 to the layout LTA documents for the table: make, importer type, fuel type,
the half year total, then for each month the body type columns HB, SDN, MPV, STW, SUV and
CPE/ Conv followed by the month total. Makes are written once per block, as a merged cell would
read, and empty cells stay empty. Counts are illustrative. Replace the fixture with a saved copy
of the live file (sources.lta_registrations_by_make_xlsx) once it has been downloaded.

Run: uv run python fixtures/make_lta_m03_xlsx.py
"""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

CODES = ["HB", "SDN", "MPV", "STW", "SUV", "CPE/ Conv"]


def sheet(ws, title: str, months: list[datetime], rows: list[tuple]) -> None:
    ws.append([title])
    header = ["Make", "Importer Type", "Fuel Type", "Total"]
    for m in months:
        header += [m] + [None] * len(CODES)
    ws.append(header)
    codes = [None, None, None, None]
    for _ in months:
        codes += CODES + ["Total"]
    ws.append(codes)
    for make, importer, fuel, per_month in rows:
        line = [make, importer, fuel, sum(sum(v or 0 for v in m) for m in per_month)]
        for m in per_month:
            line += list(m) + [sum(v or 0 for v in m)]
        ws.append(line)


def build(path: Path) -> None:
    wb = Workbook()
    first = wb.active
    first.title = "2026 1st Half"
    #                         HB    SDN   MPV   STW   SUV   CPE
    sheet(first, "NEW REGISTRATION OF CARS BY MAKE IN 2026 1st HALF",
          [datetime(2026, 1, 1), datetime(2026, 2, 1)], [
              ("B.M.W.", "AMD", "Electric", [(None, 40, None, None, 25, None), (None, 38, None, None, 30, 2)]),
              (None, "AMD", "Petrol", [(12, 60, None, 4, 30, 3), (10, 55, None, 3, 28, 2)]),
              ("BYD", "AMD", "Electric", [(210, 120, 80, None, 600, None), (190, 110, 75, None, 640, None)]),
              (None, "AMD", "Petrol-Electric (Plug-In)", [(None, 20, None, None, 90, None), (None, 25, None, None, 95, None)]),
              ("TESLA", "AMD", "Electric", [(None, 180, None, None, 300, None), (None, 170, None, None, 320, None)]),
              ("AION", "AMD", "Electric", [(90, 10, None, None, 40, None), (95, 12, None, None, 44, None)]),
              ("MG", "AMD", "Electric", [(70, None, None, None, 60, None), (65, None, None, None, 58, None)]),
              ("DENZA", "AMD", "Electric", [(None, None, 30, None, None, None), (None, None, 28, None, None, None)]),
              ("Total", None, None, [(0, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 0)]),
          ])
    second = wb.create_sheet("2026 2nd Half")
    sheet(second, "NEW REGISTRATION OF CARS BY MAKE IN 2026 2nd HALF",
          [datetime(2026, 7, 1), datetime(2026, 8, 1)], [
              ("BYD", "AMD", "Electric", [(149, 195, 60, None, 651, None), (1, 123, 161, None, 572, None)]),
              ("TESLA", "AMD", "Electric", [(None, 150, None, None, 290, None), (None, 160, None, None, 310, None)]),
              ("ZEEKR", "AMD", "Electric", [(None, None, None, None, 120, None), (None, None, None, None, 130, None)]),
          ])
    wb.save(path)


if __name__ == "__main__":
    build(Path(__file__).resolve().parent / "lta_m03.xlsx")
    print("wrote fixtures/lta_m03.xlsx")
