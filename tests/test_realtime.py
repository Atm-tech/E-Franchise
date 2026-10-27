"""Business revisions follow committed data, including writes outside HTTP routes."""
import sqlite3
import unittest

from app.realtime import install_change_tracking, revision


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, updated_at TEXT)')
        self.conn.execute('CREATE TABLE wallet_ledger(id INTEGER PRIMARY KEY, amount REAL)')
        self.conn.execute('CREATE TABLE audit_logs(id INTEGER PRIMARY KEY, details TEXT)')
        install_change_tracking(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_insert_update_delete_and_rollback(self):
        original = revision(self.conn)
        self.conn.execute("INSERT INTO products VALUES(1,'Rice','today')")
        self.assertNotEqual(revision(self.conn), original)
        self.conn.rollback()
        self.assertEqual(revision(self.conn), original)
        self.conn.execute("INSERT INTO products VALUES(1,'Rice','today')")
        self.conn.commit()
        inserted = revision(self.conn)
        self.conn.execute("UPDATE products SET name='Dal' WHERE id=1")
        self.assertNotEqual(revision(self.conn), inserted)
        updated = revision(self.conn)
        self.conn.execute('DELETE FROM products WHERE id=1')
        self.assertNotEqual(revision(self.conn), updated)

    def test_read_noop_and_bookkeeping_do_not_invalidate(self):
        self.conn.execute("INSERT INTO products VALUES(1,'Rice','today')")
        before = revision(self.conn)
        self.conn.execute('SELECT * FROM products').fetchall()
        self.conn.execute("UPDATE products SET name=name,updated_at='tomorrow'")
        self.conn.execute("INSERT INTO audit_logs VALUES(1,'Opened dashboard')")
        self.assertEqual(revision(self.conn), before)
        self.conn.execute('INSERT INTO wallet_ledger VALUES(1,500)')
        self.assertNotEqual(revision(self.conn), before)

    def test_install_is_idempotent_and_revision_is_opaque(self):
        before = revision(self.conn)
        install_change_tracking(self.conn)
        self.assertEqual(revision(self.conn), before)
        self.assertEqual(len(before), 64)


if __name__ == '__main__':
    unittest.main()
