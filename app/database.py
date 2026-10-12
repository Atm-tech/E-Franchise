"""SQLite for local development and a namespaced PostgreSQL production backend."""
from __future__ import annotations

import re
import sqlite3
import os
from decimal import Decimal
from typing import Any


SCHEMA = os.getenv("E_FRANCHISE_DB_SCHEMA", "e_franchise")
if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", SCHEMA):
    raise RuntimeError("E_FRANCHISE_DB_SCHEMA must be a simple PostgreSQL schema name")
ID_TABLES = {
    "users", "franchisees", "products", "orders", "order_items", "bills", "dockets",
    "docket_bills", "handover_logs", "delivery_tasks", "wallet_ledger", "earnings_wallet_ledger",
    "wallet_transfers", "target_programs", "target_slabs", "franchise_target_progress",
    "franchise_advances", "franchise_bank_accounts", "payment_transactions",
    "payment_webhook_events", "otp_challenges", "commission_rules", "promotions",
    "order_events", "audit_logs", "outlets", "login_attempts", "proofs", "settlements",
    "config_versions", "device_readings",
}


class Row(dict):
    """Mapping row with sqlite.Row-style positional indexing."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def _pg_sql(sql: str, *, returning: bool = False) -> str:
    text = sql.strip()
    if re.match(r"PRAGMA\s+busy_timeout", text, re.I):
        return "SELECT 1"
    if re.match(r"PRAGMA\s+user_version\s*=", text, re.I):
        return "SELECT 1"
    info = re.fullmatch(r"PRAGMA\s+table_info\((\w+)\)", text, re.I)
    if info:
        return "SELECT column_name AS name FROM information_schema.columns " \
               "WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position"
    text = re.sub(r"\bBEGIN\s+IMMEDIATE\b", "BEGIN", text, flags=re.I)
    text = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", text, flags=re.I)
    ignored = bool(re.match(r"INSERT\s+OR\s+IGNORE\s+INTO", sql.strip(), re.I))
    text = re.sub(r"datetime\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", text, flags=re.I)
    text = re.sub(r"\bsubstr\s*\(", "substring(", text, flags=re.I)
    text = re.sub(r"\?", "%s", text)
    if ignored and not re.search(r"\bON\s+CONFLICT\b", text, re.I):
        text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if returning and re.match(r"INSERT\s+INTO\s+\w+\b", text, re.I) and not re.search(r"\bRETURNING\b", text, re.I):
        table = re.match(r"INSERT\s+INTO\s+(\w+)", text, re.I).group(1).lower()
        if table in ID_TABLES:
            text = text.rstrip().rstrip(";") + " RETURNING id"
    return text


def _pg_ddl(sql: str) -> str:
    text = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    text = re.sub(r"\bid\s+INTEGER\s+PRIMARY\s+KEY\b", "id BIGSERIAL PRIMARY KEY", text, flags=re.I)
    text = re.sub(r"\bREAL\b", "NUMERIC", text, flags=re.I)
    return text


class PostgresCursor:
    def __init__(self, raw: Any):
        self.raw = raw
        self.is_postgres = True
        self.lastrowid = None
        self._buffer: list[Row] = []

    @property
    def rowcount(self) -> int:
        return self.raw.rowcount

    def execute(self, sql: str, params: Any = ()) -> "PostgresCursor":
        transformed = _pg_sql(sql, returning=True)
        if transformed.startswith("SELECT column_name AS name"):
            params = (re.search(r"PRAGMA\s+table_info\((\w+)\)", sql, re.I).group(1).lower(),)
        try:
            if params:
                transformed = re.sub(r"'(?:''|[^'])*'", lambda match: match.group(0).replace('%', '%%'), transformed)
                self.raw.execute(transformed, params)
            else:
                self.raw.execute(transformed)
        except Exception as exc:
            import psycopg
            if isinstance(exc, psycopg.Error):
                self.raw.connection.rollback()
                if isinstance(exc, psycopg.IntegrityError):
                    raise sqlite3.IntegrityError(str(exc)) from exc
                raise sqlite3.DatabaseError(str(exc)) from exc
            raise
        self.lastrowid = None
        self._buffer = []
        if re.search(r"\bRETURNING\s+id\s*$", transformed, re.I):
            returned = self.raw.fetchone()
            if returned is not None:
                self.lastrowid = returned[0]
                self._buffer.append(Row(id=returned[0]))
        return self

    def executemany(self, sql: str, seq: Any) -> "PostgresCursor":
        transformed = _pg_sql(sql)
        transformed = re.sub(r"'(?:''|[^'])*'", lambda match: match.group(0).replace('%', '%%'), transformed)
        try:
            self.raw.executemany(transformed, seq)
        except Exception as exc:
            import psycopg
            if isinstance(exc, psycopg.Error):
                self.raw.connection.rollback()
                if isinstance(exc, psycopg.IntegrityError):
                    raise sqlite3.IntegrityError(str(exc)) from exc
                raise sqlite3.DatabaseError(str(exc)) from exc
            raise
        self.lastrowid = None
        self._buffer = []
        return self

    def executescript(self, sql: str) -> None:
        sql = re.sub(r"CREATE TRIGGER IF NOT EXISTS\b.*?END\s*;", "", sql, flags=re.I | re.S)
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(_pg_ddl(statement))

    def fetchone(self) -> Row | None:
        if self._buffer:
            return self._buffer.pop(0)
        values = self.raw.fetchone()
        return self._row(values) if values is not None else None

    def fetchall(self) -> list[Row]:
        rows = self._buffer
        self._buffer = []
        return rows + [self._row(row) for row in self.raw.fetchall()]

    def _row(self, values: Any) -> Row:
        return Row((column.name, float(value) if isinstance(value, Decimal) else value)
                   for column, value in zip(self.raw.description, values))

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


class PostgresConnection:
    def __init__(self, url: str):
        import psycopg
        self.raw = psycopg.connect(url, autocommit=True)
        self.raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
        self.raw.execute(f'SET search_path TO "{SCHEMA}"')
        self.raw.autocommit = False
        self.execute("BEGIN")

    def cursor(self) -> PostgresCursor:
        return PostgresCursor(self.raw.cursor())

    def execute(self, sql: str, params: Any = ()) -> PostgresCursor:
        return self.cursor().execute(sql, params)

    def executescript(self, sql: str) -> None:
        sql = re.sub(r"CREATE TRIGGER IF NOT EXISTS\b.*?END\s*;", "", sql, flags=re.I | re.S)
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(_pg_ddl(statement))

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self.rollback() if exc_type else self.commit()
        finally:
            self.close()
