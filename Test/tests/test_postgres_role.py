import os
import unittest
import uuid

from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import DBAPIError

from app import create_app, db
from app.models import Note, Task, User


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"
    and os.environ.get("POSTGRES_ADMIN_PASSWORD"),
    "Set RUN_POSTGRES_INTEGRATION and the test-only bootstrap credentials",
)
class PostgreSQLRuntimeRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(
            {
                "SQLALCHEMY_DATABASE_URI": os.environ["DATABASE_URL"],
                "APP_ENV": "development",
                "SESSION_COOKIE_SECURE": False,
                "WTF_CSRF_ENABLED": False,
                "AUTO_CREATE_SCHEMA": False,
            }
        )
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        cls.runtime_engine = db.engine
        cls.admin_engine = create_engine(
            URL.create(
                "postgresql+psycopg2",
                username=os.environ.get("POSTGRES_ADMIN_USER", "student"),
                password=os.environ["POSTGRES_ADMIN_PASSWORD"],
                host=os.environ.get("POSTGRES_HOST", "db"),
                database="student_notes",
            )
        )

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.admin_engine.dispose()
        cls.app_context.pop()

    def test_restricted_role_supports_crud_and_server_session_revocation(self):
        username = f"t15_{uuid.uuid4().hex[:16]}"
        user = User(username=username)
        user.set_password("integration-only-password")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

        store_id = None
        try:
            privileges = db.session.execute(
                text(
                    """
                    SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                           r.rolreplication, r.rolbypassrls,
                           has_database_privilege(current_user, current_database(), 'CONNECT'),
                           has_database_privilege(current_user, current_database(), 'CREATE'),
                           has_database_privilege(current_user, current_database(), 'TEMPORARY'),
                           has_schema_privilege(current_user, 'public', 'USAGE'),
                           has_schema_privilege(current_user, 'public', 'CREATE'),
                           has_table_privilege(current_user, 'public.user', 'SELECT'),
                           has_table_privilege(current_user, 'public.user', 'INSERT'),
                           has_table_privilege(current_user, 'public.user', 'UPDATE'),
                           has_table_privilege(current_user, 'public.user', 'DELETE'),
                           has_table_privilege(current_user, 'public.note', 'SELECT'),
                           has_table_privilege(current_user, 'public.note', 'INSERT'),
                           has_table_privilege(current_user, 'public.note', 'UPDATE'),
                           has_table_privilege(current_user, 'public.note', 'DELETE'),
                           has_sequence_privilege(current_user, 'public.user_id_seq', 'USAGE')
                    FROM pg_roles AS r
                    WHERE r.rolname = current_user
                    """
                )
            ).one()
            self.assertEqual(privileges[:5], (False, False, False, False, False))
            self.assertEqual(privileges[5:8], (True, False, False))
            self.assertEqual(privileges[8:10], (True, False))
            self.assertEqual(privileges[10:14], (True, True, False, False))
            self.assertEqual(privileges[14:18], (True, True, True, True))
            self.assertTrue(privileges[18])
            memberships = db.session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_auth_members
                    WHERE member = (SELECT oid FROM pg_roles WHERE rolname = current_user)
                    """
                )
            ).scalar_one()
            self.assertEqual(memberships, 0)

            with self.assertRaises(DBAPIError):
                with self.runtime_engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as connection:
                    connection.exec_driver_sql(
                        "ALTER SYSTEM SET log_min_duration_statement = 1000"
                    )

            client = self.app.test_client()
            login_response = client.post(
                "/login",
                data={"username": username, "password": "integration-only-password"},
            )
            self.assertEqual(login_response.status_code, 302)

            cookie_name = self.app.config["SESSION_COOKIE_NAME"]
            copied_cookie = client.get_cookie(cookie_name)
            self.assertIsNotNone(copied_cookie)
            replay_client = self.app.test_client()
            replay_client.set_cookie(cookie_name, copied_cookie.value)

            client.post(
                "/notes/new",
                data={"title": "Restricted role note", "content": "PostgreSQL CRUD"},
            )
            client.post(
                "/tasks/new",
                data={
                    "title": "Restricted role task",
                    "description": "PostgreSQL CRUD",
                    "due_date": "2030-01-02",
                },
            )
            note = Note.query.filter_by(
                user_id=user.id,
                title="Restricted role note",
            ).first()
            task = Task.query.filter_by(
                user_id=user.id,
                title="Restricted role task",
            ).first()
            self.assertIsNotNone(note)
            self.assertIsNotNone(task)

            response = client.post(
                f"/notes/{note.id}/edit",
                data={"title": "Updated restricted note", "content": "Updated by PostgreSQL"},
            )
            self.assertEqual(response.status_code, 302)
            db.session.refresh(note)
            self.assertEqual(note.title, "Updated restricted note")

            response = client.post(
                f"/tasks/{task.id}/edit",
                data={
                    "title": "Updated restricted task",
                    "description": "Updated by PostgreSQL",
                    "due_date": "2030-02-03",
                    "completed": "on",
                },
            )
            self.assertEqual(response.status_code, 302)
            db.session.refresh(task)
            self.assertTrue(task.completed)
            self.assertEqual(task.title, "Updated restricted task")

            response = client.post(f"/tasks/{task.id}/toggle")
            self.assertEqual(response.status_code, 302)
            db.session.refresh(task)
            self.assertFalse(task.completed)

            response = client.post(f"/notes/{note.id}/delete")
            self.assertEqual(response.status_code, 302)
            response = client.post(f"/tasks/{task.id}/delete")
            self.assertEqual(response.status_code, 302)
            self.assertIsNone(db.session.get(Note, note.id))
            self.assertIsNone(db.session.get(Task, task.id))

            session_store = self.app.session_interface
            store_id = session_store._get_store_id(copied_cookie.value)
            session_model = session_store.sql_session_model
            self.assertIsNotNone(
                session_model.query.filter_by(session_id=store_id).first()
            )

            client.post("/logout")
            self.assertIsNone(
                session_model.query.filter_by(session_id=store_id).first()
            )
            replay_response = replay_client.get("/")
            self.assertEqual(replay_response.status_code, 302)
            self.assertEqual(replay_response.headers["Location"], "/login")
        finally:
            db.session.remove()
            with self.admin_engine.begin() as connection:
                if store_id is not None:
                    connection.execute(
                        text("DELETE FROM flask_sessions WHERE session_id = :store_id"),
                        {"store_id": store_id},
                    )
                connection.execute(
                    text('DELETE FROM "user" WHERE id = :user_id'),
                    {"user_id": user_id},
                )


if __name__ == "__main__":
    unittest.main()
