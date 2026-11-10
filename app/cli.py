"""Account maintenance from the server, e.g. `docker exec -it rememberry python -m app.cli reset-password`."""
import argparse
import getpass
import sys

from . import auth, db


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
        confirm = password
    else:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm new password: ")
    error = auth.password_error(password, confirm)
    if error:
        sys.exit(error)
    return password


def reset_password(args):
    with db.connect() as conn:
        users = conn.execute("SELECT id, username FROM users ORDER BY id").fetchall()
    if not users:
        username = args.username or input("No account yet. Username to create: ").strip()
        if error := auth.username_error(username):
            sys.exit(error)
        user_id = auth.create_owner(username, read_password(args.password_stdin))
        print(f"Created account {username!r} (id {user_id}).")
        return
    if args.username:
        user = auth.find_user(args.username)
        if not user:
            sys.exit(f"No user called {args.username!r}. Users: {', '.join(u['username'] for u in users)}")
    elif len(users) == 1:
        user = users[0]
    else:
        sys.exit("Several users exist; pass --username.")
    auth.set_password(user["id"], read_password(args.password_stdin))
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM login_failures")
    print(f"Password for {user['username']!r} reset. All its sessions were signed out and lockouts cleared.")


def list_users(_args):
    with db.connect() as conn:
        rows = conn.execute("SELECT id, username, role, created_at, password_changed_at FROM users ORDER BY id").fetchall()
    if not rows:
        print("No accounts yet: the app shows first-run setup.")
    for r in rows:
        print(f"{r['id']}\t{r['username']}\t{r['role']}\tcreated {r['created_at']}\tpassword changed {r['password_changed_at']}")


def unlock(_args):
    with db.connect() as conn:
        n = conn.execute("DELETE FROM login_failures").rowcount
    print(f"Cleared {n} lockout/failure record(s).")


def delete_accounts(args):
    if not args.yes and input("Delete every account and session, and go back to first-run setup? [y/N] ").lower() != "y":
        sys.exit("Cancelled.")
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM login_failures")
        n = conn.execute("DELETE FROM users").rowcount
    print(f"Deleted {n} account(s). Your people and journal are untouched. Open the app to create a new account.")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Rememberry account maintenance.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("reset-password", help="Set a new password (creates the account if there is none).")
    p.add_argument("--username", help="Which account (needed only if there are several).")
    p.add_argument("--password-stdin", action="store_true", help="Read the new password from stdin instead of prompting.")
    p.set_defaults(func=reset_password)
    sub.add_parser("list-users", help="Show accounts.").set_defaults(func=list_users)
    sub.add_parser("unlock", help="Clear login lockouts after too many failed attempts.").set_defaults(func=unlock)
    p = sub.add_parser("delete-accounts", help="Remove all accounts so the app shows first-run setup again.")
    p.add_argument("--yes", action="store_true", help="Don't ask for confirmation.")
    p.set_defaults(func=delete_accounts)
    args = parser.parse_args(argv)
    db.init()
    args.func(args)


if __name__ == "__main__":
    main()
