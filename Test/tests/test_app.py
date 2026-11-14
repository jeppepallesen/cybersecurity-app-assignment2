import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app import create_app, db
from app.models import Note, Task, User
from app.seed import seed_demo_data


class StudentNotesTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "WTF_CSRF_ENABLED": False,
            "SEED_DEMO_DATA": False,
            "SESSION_COOKIE_SECURE": False,
        })
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        self.alice = User(username="alice")
        self.alice.set_password("wonderland")
        self.bob = User(username="bob")
        self.bob.set_password("builder")
        db.session.add_all([self.alice, self.bob])
        db.session.flush()
        self.note = Note(user_id=self.alice.id, title="Private note", content="A fictional study detail.")
        self.bobs_note = Note(user_id=self.bob.id, title="Bob's note", content="Another fictional detail.")
        self.task = Task(user_id=self.alice.id, title="Read chapter", description="Chapter 2", due_date=date(2030, 1, 2))
        db.session.add_all([self.note, self.bobs_note, self.task])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self, username="alice", password="wonderland"):
        return self.client.post("/login", data={"username": username, "password": password})

    def test_app_requires_secret_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY must be set"):
                create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})

    def test_application_configuration_cannot_bypass_secret_requirement(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY must be set"):
                create_app({
                    "SECRET_KEY": "configuration-only-secret",
                    "SQLALCHEMY_DATABASE_URI": "sqlite://",
                })

    def test_application_requires_database_url_without_a_default_credential(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-secret"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL must be set"):
                create_app({
                    "TESTING": True,
                    "SECRET_KEY": "test-secret",
                })

    def test_demo_seeding_is_disabled_by_default(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-secret"}, clear=True):
            app = create_app({
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "WTF_CSRF_ENABLED": False,
            })
        with app.app_context():
            self.assertFalse(app.config["SEED_DEMO_DATA"])
            self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
            self.assertEqual(User.query.count(), 0)
            db.drop_all()
            db.session.remove()
            db.engine.dispose()

    def test_explicit_demo_seeding_requires_environment_passwords(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEMO_ALICE_PASSWORD"):
                seed_demo_data()

    def test_login_page_does_not_disclose_demo_credentials(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"alice123", response.data)
        self.assertNotIn(b"bob123", response.data)

    def test_login_and_logout(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"alice", self.client.get("/").data)
        cookie_name = self.app.config["SESSION_COOKIE_NAME"]
        copied_cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(copied_cookie)

        replay_client = self.app.test_client()
        replay_client.set_cookie(cookie_name, copied_cookie.value)

        self.client.post("/logout")
        self.assertEqual(self.client.get("/").status_code, 302)
        replay_response = replay_client.get("/")
        self.assertEqual(replay_response.status_code, 302)
        self.assertEqual(replay_response.headers["Location"], "/login")

    def test_authenticated_session_cookie_has_secure_attributes(self):
        self.app.config["SESSION_COOKIE_SECURE"] = True
        response = self.login()
        self.assertEqual(response.status_code, 302)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Expires=", cookie)
        self.assertIn("Secure", cookie)

    def test_successful_login_regenerates_the_session_id(self):
        with self.client.session_transaction() as browser_session:
            browser_session["pre_login_marker"] = "value"
        cookie_name = self.app.config["SESSION_COOKIE_NAME"]
        pre_login_cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(pre_login_cookie)

        response = self.login()
        self.assertEqual(response.status_code, 302)
        authenticated_cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(authenticated_cookie)
        self.assertNotEqual(pre_login_cookie.value, authenticated_cookie.value)

    def test_server_side_session_expires_after_eight_hours(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        cookie_name = self.app.config["SESSION_COOKIE_NAME"]
        copied_cookie = self.client.get_cookie(cookie_name)
        self.assertIsNotNone(copied_cookie)
        interface = self.app.session_interface
        store_id = interface._get_store_id(copied_cookie.value)
        session_model = interface.sql_session_model

        with self.app.app_context():
            record = session_model.query.filter_by(session_id=store_id).first()
            self.assertIsNotNone(record)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            remaining = record.expiry - now
            self.assertGreater(remaining, timedelta(hours=7, minutes=59))
            self.assertLessEqual(remaining, timedelta(hours=8, minutes=1))
            record.expiry = now - timedelta(seconds=1)
            db.session.commit()

        replay_client = self.app.test_client()
        replay_client.set_cookie(cookie_name, copied_cookie.value)
        response = replay_client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        with self.app.app_context():
            self.assertIsNone(
                session_model.query.filter_by(session_id=store_id).first()
            )

    def test_secure_cookie_can_only_be_disabled_in_development_or_tests(self):
        with patch.dict(
            os.environ,
            {"SECRET_KEY": "test-secret", "SESSION_COOKIE_SECURE": "false"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "only for testing or development"):
                create_app({
                    "SQLALCHEMY_DATABASE_URI": "sqlite://",
                    "APP_ENV": "production",
                })

            development_app = create_app({
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "APP_ENV": "development",
            })
        self.assertFalse(development_app.config["SESSION_COOKIE_SECURE"])
        with development_app.app_context():
            db.drop_all()
            db.session.remove()
            db.engine.dispose()

    def test_unauthenticated_access_redirects_to_login(self):
        response = self.app.test_client().get("/notes")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_security_headers_are_present_and_csp_allows_only_required_sources(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            response.headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self' https://fonts.googleapis.com", csp)
        self.assertIn("font-src 'self' https://fonts.gstatic.com", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=()",
        )

    def test_dynamic_and_authenticated_responses_are_not_cached(self):
        login_page = self.client.get("/login")
        self.assertIn("no-store", login_page.headers["Cache-Control"])
        self.assertIn("private", login_page.headers["Cache-Control"])

        self.login()
        private_page = self.client.get("/notes")
        self.assertIn("no-store", private_page.headers["Cache-Control"])
        self.assertIn("private", private_page.headers["Cache-Control"])

    def test_hsts_is_only_sent_when_enabled_and_request_is_https(self):
        self.app.config["ENABLE_HSTS"] = True
        http_response = self.app.test_client().get("/login")
        self.assertNotIn("Strict-Transport-Security", http_response.headers)

        https_response = self.app.test_client().get(
            "/login",
            environ_overrides={"wsgi.url_scheme": "https"},
        )
        self.assertEqual(
            https_response.headers["Strict-Transport-Security"],
            "max-age=31536000",
        )

    def test_production_configuration_forces_debug_off(self):
        production_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "APP_ENV": "production",
            "DEBUG": True,
            "SESSION_COOKIE_SECURE": True,
        })
        self.assertFalse(production_app.debug)
        with production_app.app_context():
            db.drop_all()
            db.session.remove()
            db.engine.dispose()

    def test_login_rate_limit_returns_generic_failure_after_five_attempts(self):
        generic_failure = b"Check your username and password, then try again."
        responses = [self.login(password="incorrect") for _ in range(5)]
        blocked_response = self.login(password="incorrect")
        unknown_user_response = self.login(
            username="not-a-real-account",
            password="incorrect",
        )

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(generic_failure, response.data)
        self.assertEqual(blocked_response.status_code, 200)
        self.assertIn(generic_failure, blocked_response.data)
        self.assertEqual(unknown_user_response.status_code, 200)
        self.assertIn(generic_failure, unknown_user_response.data)
        self.assertNotIn(b"not-a-real-account", unknown_user_response.data)
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("user_id", browser_session)

    def test_successful_login_is_not_limited_on_a_fresh_client(self):
        for attempt in range(6):
            response = self.login()
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "/")
            if attempt < 5:
                self.client.post("/logout")
        self.assertIn(b"alice", self.client.get("/").data)

    def test_invalid_login_is_rejected(self):
        response = self.login(password="incorrect")
        self.assertIn(b"Check your username and password", response.data)

    def test_notes_are_private_and_search_is_scoped(self):
        self.login()
        response = self.client.get("/notes")
        self.assertIn(b"Private note", response.data)
        self.assertNotIn(b"Bob&#39;s note", response.data)
        response = self.client.get("/search?q=fictional")
        self.assertIn(b"Private note", response.data)
        self.assertNotIn(b"Bob&#39;s note", response.data)

    def test_user_cannot_edit_another_users_note(self):
        self.login()
        response = self.client.post(f"/notes/{self.bobs_note.id}/edit", data={"title": "Changed", "content": "Nope"})
        self.assertEqual(response.status_code, 404)
        db.session.refresh(self.bobs_note)
        self.assertEqual(self.bobs_note.title, "Bob's note")

    def test_note_and_task_creation(self):
        self.login()
        self.client.post("/notes/new", data={"title": "New note", "content": "A new thought"})
        self.client.post("/tasks/new", data={"title": "New task", "description": "Details", "due_date": "2030-02-03"})
        self.assertIsNotNone(Note.query.filter_by(user_id=self.alice.id, title="New note").first())
        task = Task.query.filter_by(user_id=self.alice.id, title="New task").first()
        self.assertIsNotNone(task)
        self.assertFalse(task.completed)

    def test_edit_complete_and_delete_records(self):
        self.login()
        response = self.client.post(
            f"/notes/{self.note.id}/edit",
            data={"title": "Updated note", "content": "Revised content"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.note)
        self.assertEqual(self.note.title, "Updated note")

        response = self.client.post(
            f"/tasks/{self.task.id}/edit",
            data={"title": "Updated task", "description": "Done soon", "due_date": "2030-02-03", "completed": "on"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.task)
        self.assertTrue(self.task.completed)
        self.assertEqual(self.task.title, "Updated task")

        self.client.post(f"/tasks/{self.task.id}/toggle")
        db.session.refresh(self.task)
        self.assertFalse(self.task.completed)
        self.client.post(f"/notes/{self.note.id}/delete")
        self.client.post(f"/tasks/{self.task.id}/delete")
        self.assertIsNone(db.session.get(Note, self.note.id))
        self.assertIsNone(db.session.get(Task, self.task.id))


if __name__ == "__main__":
    unittest.main()