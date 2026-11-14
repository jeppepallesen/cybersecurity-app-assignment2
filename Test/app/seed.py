import os
from datetime import date, timedelta

from app import db
from app.models import Note, Task, User


def seed_demo_data():
    alice_password = os.environ.get("DEMO_ALICE_PASSWORD")
    bob_password = os.environ.get("DEMO_BOB_PASSWORD")
    if not alice_password or not bob_password:
        raise RuntimeError(
            "DEMO_ALICE_PASSWORD and DEMO_BOB_PASSWORD are required when demo seeding is enabled"
        )

    if User.query.filter_by(username="alice").first() is None:
        alice = User(username="alice")
        alice.set_password(alice_password)
        alice.notes = [
            Note(title="Biology review", content="Revise cell structure and prepare questions about photosynthesis."),
            Note(title="Library plan", content="Find a quiet table on the second floor for Thursday's study session."),
        ]
        alice.tasks = [
            Task(title="Finish lab summary", description="Write up the fictional plant-growth experiment.", due_date=date.today() + timedelta(days=2)),
            Task(title="Return borrowed book", description="Bring the astronomy book back to the campus library.", due_date=date.today() + timedelta(days=5)),
        ]
        db.session.add(alice)

    if User.query.filter_by(username="bob").first() is None:
        bob = User(username="bob")
        bob.set_password(bob_password)
        bob.notes = [
            Note(title="Design lecture", content="Compare grid and flex layouts for the fictional campus notice board."),
            Note(title="Project idea", content="A small map showing accessible study spaces around campus."),
        ]
        bob.tasks = [
            Task(title="Sketch project screens", description="Make rough layouts for the map and study-space detail view.", due_date=date.today() + timedelta(days=1)),
            Task(title="Read chapter four", description="Take notes on interaction design principles.", due_date=date.today() + timedelta(days=4)),
        ]
        db.session.add(bob)

    db.session.commit()