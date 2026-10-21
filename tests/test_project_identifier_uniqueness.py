import sqlite3
import unittest

from webapp import db


class ProjectIdentifierUniquenessTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE tickets (
                id INTEGER PRIMARY KEY,
                ticket_no TEXT,
                work_order TEXT
            );
            CREATE TABLE reinforcement_works (
                id INTEGER PRIMARY KEY,
                ticket_no TEXT,
                work_order TEXT
            );
        """)
        db.install_project_identifier_constraints(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_fault_number_cannot_be_repeated_but_can_be_referenced_by_improvement(self):
        self.conn.execute(
            "INSERT INTO tickets(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-1", "WO-1"),
        )
        self.conn.execute(
            "INSERT INTO reinforcement_works(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-1", "WO-2"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "رقم العطل"):
            self.conn.execute("INSERT INTO tickets(ticket_no) VALUES (?)", (" fault-1 ",))

    def test_work_order_is_unique_across_faults_and_improvements(self):
        self.conn.execute(
            "INSERT INTO tickets(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-1", "WO-1"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "رقم أمر العمل"):
            self.conn.execute(
                "INSERT INTO reinforcement_works(work_order) VALUES (?)",
                (" wo-1 ",),
            )
        self.conn.execute(
            "INSERT INTO reinforcement_works(work_order) VALUES (?)",
            ("WO-2",),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "رقم أمر العمل"):
            self.conn.execute(
                "INSERT INTO tickets(ticket_no, work_order) VALUES (?, ?)",
                ("FAULT-2", "wo-2"),
            )

    def test_updates_cannot_duplicate_identifiers_between_records(self):
        self.conn.execute(
            "INSERT INTO tickets(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-1", "WO-1"),
        )
        self.conn.execute(
            "INSERT INTO reinforcement_works(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-1", "WO-2"),
        )
        self.conn.execute(
            "INSERT INTO tickets(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-2", "WO-3"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "رقم أمر العمل"):
            self.conn.execute(
                "UPDATE reinforcement_works SET work_order=? WHERE id=1",
                ("WO-1",),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "رقم العطل"):
            self.conn.execute(
                "UPDATE tickets SET ticket_no=? WHERE id=1",
                ("FAULT-2",),
            )

    def test_preflight_reports_duplicates_and_ignores_current_record(self):
        self.conn.execute(
            "INSERT INTO tickets(ticket_no, work_order) VALUES (?, ?)",
            ("FAULT-1", "WO-1"),
        )
        self.assertIn(
            "رقم العطل",
            db.project_identifier_conflict(
                "ticket", ticket_no="fault-1", conn=self.conn) or "",
        )
        self.assertIn(
            "رقم أمر العمل",
            db.project_identifier_conflict(
                "reinforcement", work_order="wo-1", conn=self.conn) or "",
        )
        self.assertIsNone(db.project_identifier_conflict(
            "ticket", ticket_no="FAULT-1", work_order="WO-1",
            record_id=1, conn=self.conn))


if __name__ == "__main__":
    unittest.main()
