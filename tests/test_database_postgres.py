"""Read-only PostgreSQL regressions; opt in with TEST_POSTGRES_URL."""
import os
import unittest

from app.database import _pg_sql


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'PostgreSQL connection not configured')
class PostgresQueryTests(unittest.TestCase):
    def test_docket_bill_list_ignores_nulls_and_keeps_separator(self):
        import psycopg
        with psycopg.connect(os.environ['TEST_POSTGRES_URL']) as conn:
            result = conn.execute(_pg_sql(
                "SELECT group_concat(b.bill_number, ', ') "
                "FROM (VALUES ('BILL-A'), (NULL), ('BILL-B')) b(bill_number)"
            )).fetchone()[0]
            self.assertEqual(result, 'BILL-A, BILL-B')
            empty = conn.execute(_pg_sql(
                "SELECT group_concat(b.bill_number, ', ') "
                "FROM (VALUES (NULL::text)) b(bill_number)"
            )).fetchone()[0]
            self.assertIsNone(empty)
