import os
import unittest
os.environ["SECRET_KEY"] = "test-secret-key-1234567890123456"
from webapp.app import create_app
from webapp import db

class TicketViewRenderingTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    def test_sync_financial_url_building(self):
        with self.app.test_request_context():
            from flask import url_for
            url1 = url_for("tickets.sync_financial", ticket_id=194)
            self.assertEqual(url1, "/tickets/194/sync-financial")
            url2 = url_for("sync_financial", ticket_id=194)
            self.assertEqual(url2, "/tickets/194/sync-financial")
            url3 = url_for("ticket_sync_financial", ticket_id=194)
            self.assertEqual(url3, "/tickets/194/sync-financial")

    def test_ticket_view_renders_template_cleanly(self):
        with self.app.test_request_context("/tickets/1?edit=1&step=data#step-data"):
            from flask import session, render_template
            session["user_id"] = 1
            session["role"] = "admin"
            conn = db.connect()
            row = conn.execute("SELECT * FROM tickets LIMIT 1").fetchone()
            conn.close()
            if row:
                t = dict(row)
                rendered = render_template(
                    "ticket_view.html",
                    ticket=t,
                    related={"quantities": [], "photos": [], "coordination": [], "metering": [], "warehouse_tx": [], "boq_lines": [], "new_coordinations": [], "issued_licenses": [], "invoices": [], "clearances": []},
                    quality_workflow=None,
                    has_boq_catalog=False,
                    boq_file=None,
                    emergency_ratio=0.0,
                    edit_mode=True,
                    can_mutate=True,
                    wizard_steps=[("data", "البيانات"), ("boq", "الكميات")],
                    edit_step="data",
                    next_step="boq",
                    step_labels={"data": "البيانات", "boq": "الكميات"},
                    whatsapp_share_url=None,
                )
                self.assertIn("step-data", rendered)
                self.assertIn("/tickets/1/sync-financial", rendered)

if __name__ == "__main__":
    unittest.main()
