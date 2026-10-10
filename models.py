from extensions import dbfrom datetime import datetime
class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    username = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    message = db.Column(db.Text, nullable=False)

    user = db.relationship('User', backref=db.backref('email_logs', lazy=True))

    def __repr__(self):
        return f'<EmailLog {self.id} - {self.username}>'

class JobApplication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('job.id'), nullable=False)
    username = db.Column(db.String(100), nullable=False)  # Store the username here as before
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Add user_id column
    applied_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    status = db.Column(db.String(50), default='applied') # applied, reviewed, shortlisted, interviewed, hired, rejected
    recruiter_notes = db.Column(db.Text, nullable=True)

    job = db.relationship('Job', backref=db.backref('applications', lazy=True))
    user = db.relationship('User', backref='applications', lazy=True)  # Relationship with User

    def __repr__(self):
        return f"<JobApplication {self.id}>"
    
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(50), nullable=True, unique=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    headline = db.Column(db.String(255), nullable=True)
    profile_picture = db.Column(db.String(100), nullable=True)
    cover_photo = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(100), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)

    @property
    def connected_count(self):
        return Connection.query.filter(
            ((Connection.requester_id == self.id) | (Connection.receiver_id == self.id)),
            (Connection.status == 'accepted')
        ).count()

    def get_connection_ids(self):
        # Helper to get all user IDs this user is connected with
        conns_sent = db.session.query(Connection.receiver_id).filter_by(requester_id=self.id, status='accepted')
        conns_rcvd = db.session.query(Connection.requester_id).filter_by(receiver_id=self.id, status='accepted')
        return set([cid[0] for cid in conns_sent.union(conns_rcvd).all()])

    def get_mutual_count(self, other_user):
        if not other_user: return 0
        my_friends = self.get_connection_ids()
        their_friends = other_user.get_connection_ids()
        return len(my_friends.intersection(their_friends))

    def __repr__(self):
        return f'<User {self.username}>'




class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    company = db.Column(db.String(100), nullable=False)
    photo = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(100), nullable=False)
    job_type = db.Column(db.String(50), nullable=False)
    job_status = db.Column(db.String(50), nullable=False)
    qualifications = db.Column(db.Text, nullable=True)
    requirements = db.Column(db.Text, nullable=True)
    responsibilities = db.Column(db.Text, nullable=True)
    experience = db.Column(db.Integer, nullable=True)
    vacancy = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    salary = db.Column(db.Numeric(10, 2), nullable=True)
    recruiter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Link to User

    recruiter = db.relationship('User', backref='jobs', lazy=True)  # Relationship with User

    def __repr__(self):
        return f'<Job {self.title}>'

class SavedJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey('job.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    location = db.Column(db.String(255), nullable=False)
    job_type = db.Column(db.String(50), nullable=False)
    photo = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(255), nullable=False)
    notif_type = db.Column(db.String(50), nullable=True) # like, comment, connect, application, job
    target_id = db.Column(db.Integer, nullable=True)
    is_read = db.Column(db.Boolean, default=False)  # Add this line for is_read
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Notification {self.id}>"
    
class WorkExperience(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), nullable=False)  # Fix typo: compan_name -> company_name
    role = db.Column(db.String(100), nullable=False)
    job_title = db.Column(db.String(100), nullable=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)
    description = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f'<WorkExperience {self.company_name}, {self.role}, {self.job_title}>'

class Resume(db.Model):
    __tablename__ = 'resume'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    resume = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    # Relationship with User model
    user = db.relationship('User', backref='resumes')

    def __repr__(self):
        return f"<Resume(id={self.id}, resume={self.resume}, created_at={self.created_at})>"

class Education(db.Model):
    __tablename__ = 'education'
    education_id = db.Column(db.Integer, primary_key=True)  # Auto-incremented primary key
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Foreign key to User table
    degree = db.Column(db.String(255), nullable=False)  # Degree name (e.g., Bachelors, Masters)
    institution = db.Column(db.String(255), nullable=False)  # Institution name
    year_of_passing = db.Column(db.Integer, nullable=False)  # Year of graduation
    tenth = db.Column(db.String(50), nullable=True)  # Tenth grade details
    twelfth = db.Column(db.String(50), nullable=True)  # Twelfth grade details

    # Relationship to User (allows access to user's education from User object)
    user = db.relationship('User', backref='education')

    def __repr__(self):
        return f"<Education(degree={self.degree}, institution={self.institution}, year_of_passing={self.year_of_passing})>"
class skills(db.Model):
    __tablename__ = 'skills'  # Explicitly naming the table as 'skills'

    id = db.Column(db.Integer, primary_key=True)
    skill_name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# Additional fields as required

# Feedback model
class Feedback(db.Model):
    __tablename__ = 'feedback'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    feedback_type = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.now)

    def __repr__(self):
        return f"<Feedback(name={self.name}, email={self.email}, feedback_type={self.feedback_type})>"

# Database Model
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    industry = db.Column(db.String(100))
    location = db.Column(db.String(200))
    contact = db.Column(db.String(50))
    website = db.Column(db.String(200))
    about = db.Column(db.Text)
    logo = db.Column(db.String(200))

# --- NEW LinkedIn Style Models ---
class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    media_url = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('posts', lazy=True))
    likes = db.relationship('Like', backref='post', lazy=True, cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='post', lazy=True, cascade='all, delete-orphan')

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('comments', lazy=True))

class Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, accepted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
