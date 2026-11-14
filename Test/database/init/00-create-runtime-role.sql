\getenv app_db_password APP_DB_PASSWORD

SELECT 'CREATE ROLE student_app'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'student_app')
\gexec

ALTER ROLE student_app
    LOGIN
    PASSWORD :'app_db_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS
    NOINHERIT;

SELECT format('REVOKE %I FROM student_app', parent_role.rolname)
FROM pg_auth_members AS membership
JOIN pg_roles AS parent_role ON parent_role.oid = membership.roleid
WHERE membership.member = 'student_app'::regrole
\gexec
