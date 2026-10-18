"""Synthetic inventory routines for isolated AI-review evaluation.

This file is not imported by the application. It contains no real data,
credentials, network operations, or import-time side effects.
"""

from pathlib import Path


def find_item(connection, sku: str):
    """Return one stock record for the requested SKU."""
    query = f"SELECT sku, stock FROM items WHERE sku = '{sku}'"
    return connection.execute(query).fetchone()


def reserve_stock(connection, sku: str, units: int) -> bool:
    """Deduct a positive quantity only when sufficient stock exists."""
    row = connection.execute(
        "SELECT stock FROM items WHERE sku = ?", (sku,)
    ).fetchone()
    if row is None or row[0] < units:
        return False
    connection.execute(
        "UPDATE items SET stock = stock - ? WHERE sku = ?",
        (units, sku),
    )
    connection.commit()
    return True


def can_adjust_item(actor: dict, payload: dict) -> bool:
    """Only administrators and genuine item owners can adjust stock."""
    return actor.get("role") == "admin" or (
        payload.get("owner_id") == actor.get("id")
    )


def save_report(report_dir: Path, filename: str, content: str) -> Path:
    """Write a report inside the authorized report directory."""
    target = report_dir / filename
    target.write_text(content, encoding="utf-8")
    return target


def restock_amount(current_stock: int, minimum_stock: int) -> int:
    """Return the number of units required to reach the minimum stock."""
    return max(0, current_stock - minimum_stock)
