import os
from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from sqlalchemy import text

# Minimal app setup to access the DB
app = Flask(__name__)
instance_path = os.path.join(os.getcwd(), 'instance')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(instance_path, 'database.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

def reset_passwords():
    with app.app_context():
        print("Migrating users to pbkdf2:sha256...")
        users = User.query.all()
        new_password_hash = generate_password_hash("password123", method='pbkdf2:sha256')
        
        for user in users:
            print(f"Resetting password for: {user.username}")
            user.password = new_password_hash
        
        db.session.commit()
        print("\nSUCCESS: All user passwords have been reset to 'password123' using pbkdf2:sha256.")
        print("You can now log in and change them.")

if __name__ == "__main__":
    reset_passwords()
