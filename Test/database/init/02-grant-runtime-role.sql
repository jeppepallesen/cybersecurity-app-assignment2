\getenv bootstrap_role POSTGRES_USER
\getenv bootstrap_password POSTGRES_PASSWORD

ALTER ROLE :"bootstrap_role" PASSWORD :'bootstrap_password';
REVOKE CONNECT, TEMPORARY ON DATABASE student_notes FROM PUBLIC;
GRANT CONNECT ON DATABASE student_notes TO student_app;
GRANT USAGE ON SCHEMA public TO student_app;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM student_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM student_app;

ALTER DEFAULT PRIVILEGES FOR ROLE :"bootstrap_role" IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM student_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"bootstrap_role" IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM student_app;

GRANT SELECT, INSERT ON TABLE "user" TO student_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE note, task, login_attempt_bucket, flask_sessions TO student_app;
GRANT USAGE ON SEQUENCE user_id_seq, note_id_seq, task_id_seq, flask_sessions_id_seq
    TO student_app;
