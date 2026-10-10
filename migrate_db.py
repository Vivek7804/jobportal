import os
from app import app, db
from sqlalchemy import text

def migrate():
    with app.app_context():
        # Create all newly defined tables (Post, Like, Comment, Connection, Message)
        db.create_all()
        
        # Add new columns to User table manually
        try:
            db.session.execute(text("ALTER TABLE user ADD COLUMN headline VARCHAR(255)"))
            db.session.commit()
            print("Added headline column to user.")
        except Exception as e:
            db.session.rollback()
            print(f"Headline column might already exist: {e}")

        try:
            db.session.execute(text("ALTER TABLE user ADD COLUMN cover_photo VARCHAR(100)"))
            db.session.commit()
            print("Added cover_photo column to user.")
        except Exception as e:
            db.session.rollback()
            print(f"Cover_photo column might already exist: {e}")

        # RMS Updates
        try:
            db.session.execute(text("ALTER TABLE job_application ADD COLUMN status VARCHAR(50) DEFAULT 'applied'"))
            db.session.execute(text("ALTER TABLE job_application ADD COLUMN recruiter_notes TEXT"))
            db.session.commit()
            print("Added status and notes to job_application.")
        except Exception as e:
            db.session.rollback()
            print(f"JobApplication columns might already exist: {e}")

        # Notification Updates
        try:
            db.session.execute(text("ALTER TABLE notification ADD COLUMN notif_type VARCHAR(50)"))
            db.session.execute(text("ALTER TABLE notification ADD COLUMN target_id INTEGER"))
            db.session.commit()
            print("Added notif_type and target_id to notification.")
        except Exception as e:
            db.session.rollback()
            print(f"Notification columns might already exist: {e}")

if __name__ == '__main__':
    migrate()
