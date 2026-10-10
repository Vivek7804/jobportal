import os
import re
from datetime import datetime
from functools import wraps
from hmac import compare_digest
from secrets import token_urlsafe

from flask import Blueprint, Flask, abort, current_app, jsonify, render_template, request, redirect, url_for, flash, session
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


# Initialize the Flask app
app = Flask(__name__)
os.makedirs(app.instance_path, exist_ok=True)

default_sqlite_path = os.path.join(app.instance_path, "database.db")
database_uri = os.environ.get("DATABASE_URL", f"sqlite:///{default_sqlite_path}")
if database_uri.startswith("mysql://"):
    database_uri = database_uri.replace("mysql://", "mysql+mysqlconnector://", 1)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

if not app.config['SECRET_KEY']:
    raise RuntimeError("SECRET_KEY environment variable is required")
app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'static/uploads')
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'doc', 'docx', 'txt'}

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER') or app.config['MAIL_USERNAME']
app.config['MAIL_DEBUG'] = os.environ.get('MAIL_DEBUG', 'false').lower() == 'true'

mail = Mail(app)
reports_bp = Blueprint('reports', __name__)

# Initialize SQLAlchemy with the app
db = SQLAlchemy(app)

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

# Ensure the upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_SIGNUP_ROLES = {'jobseeker', 'recruiter'}
MAIL_ENABLED = bool(
    app.config['MAIL_USERNAME']
    and app.config['MAIL_PASSWORD']
    and app.config['MAIL_DEFAULT_SENDER']
)


@app.context_processor
def inject_user():
    user = get_current_user()
    return dict(get_current_user=lambda: user, role=session.get('role'))


def get_current_user():

    username = session.get('username')
    if not username:
        return None
    return User.query.filter_by(username=username).first()


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'username' not in session:
            flash('Please sign in to continue.', 'warning')
            return redirect(url_for('signin'))
        if not get_current_user():
            session.clear()
            flash('Session expired. Please sign in again.', 'warning')
            return redirect(url_for('signin'))
        return view(*args, **kwargs)
    return wrapped_view


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if session.get('role') not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped_view
    return decorator


def can_manage_user(target_user_id):
    current_user = get_current_user()
    if not current_user:
        return False
    return current_user.id == target_user_id or session.get('role') == 'superadmin'


def verify_password(user, plain_password):
    stored_password = user.password or ''
    if stored_password.startswith(('pbkdf2:', 'scrypt:')):
        return check_password_hash(stored_password, plain_password)

    # Backward compatibility for older plaintext rows, migrated on next login.
    if stored_password == plain_password:
        user.password = generate_password_hash(plain_password, method='pbkdf2:sha256')
        db.session.commit()
        return True
    return False


def send_email_safe(message):
    if not MAIL_ENABLED:
        current_app.logger.warning('Mail config is incomplete. Skipping email send.')
        return False
    try:
        mail.send(message)
        return True
    except Exception:
        current_app.logger.exception('Email send failed')
        return False


def create_notification(user_id, message, job_id=None, notif_type=None, target_id=None):
    notification = Notification(
        job_id=job_id or 0,
        user_id=user_id,
        message=message,
        notif_type=notif_type,
        target_id=target_id,
        created_at=datetime.now(),
        is_read=False,
    )
    db.session.add(notification)
    return notification


def allowed_file(filename):
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
    )


def generate_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': generate_csrf_token}


@app.before_request
def csrf_protect():
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        session_token = session.get('_csrf_token')
        request_token = request.form.get('_csrf_token') or request.headers.get('X-CSRFToken')
        if not session_token or not request_token or not compare_digest(session_token, request_token):
            abort(400, description='Invalid CSRF token')


DISCOVERY_PAGE_SIZE = 9


def _to_int(value, default=1, minimum=1, maximum=500):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value):
    return (value or '').strip().lower()


def get_user_skill_tokens(user):
    if not user:
        return set()
    user_skills = skills.query.filter_by(user_id=user.id).all()
    tokens = set()
    for skill_row in user_skills:
        for item in (skill_row.skill_name or '').split(','):
            token = item.strip().lower()
            if token:
                tokens.add(token)
    return tokens


def annotate_match_scores(job_items, user):
    tokens = get_user_skill_tokens(user)
    preferred_location = _normalize(user.location if user else '')

    for job in job_items:
        score = 0
        tags = []
        searchable_text = " ".join(
            filter(
                None,
                [
                    job.title,
                    job.description,
                    job.requirements,
                    job.qualifications,
                    job.responsibilities,
                    job.company,
                ],
            )
        ).lower()

        title_text = _normalize(job.title)
        for token in tokens:
            if token in searchable_text:
                score += 12 if token in title_text else 6
                if len(tags) < 3 and token not in tags:
                    tags.append(token.title())

        if preferred_location and preferred_location in _normalize(job.location):
            score += 14
            if len(tags) < 3:
                tags.append('Location Match')

        if score > 0:
            job.match_score = min(score, 98)
            job.match_tags = tags
        else:
            job.match_score = None
            job.match_tags = []


def build_job_discovery_query(filters):
    query = Job.query

    keyword = filters.get('query')
    if keyword:
        keyword_pattern = f"%{keyword}%"
        query = query.filter(
            or_(
                Job.title.ilike(keyword_pattern),
                Job.company.ilike(keyword_pattern),
                Job.location.ilike(keyword_pattern),
                Job.description.ilike(keyword_pattern),
                Job.requirements.ilike(keyword_pattern),
                Job.qualifications.ilike(keyword_pattern),
            )
        )

    location = filters.get('location')
    if location:
        query = query.filter(Job.location.ilike(f"%{location}%"))

    job_type = filters.get('job_type')
    if job_type:
        query = query.filter(Job.job_type == job_type)

    status = filters.get('status')
    if status:
        query = query.filter(Job.job_status == status)

    min_salary = filters.get('min_salary')
    if min_salary is not None:
        query = query.filter(Job.salary >= min_salary)

    max_salary = filters.get('max_salary')
    if max_salary is not None:
        query = query.filter(Job.salary <= max_salary)

    sort_by = filters.get('sort', 'newest')
    if sort_by == 'salary_high':
        query = query.order_by(Job.salary.desc(), Job.created_at.desc())
    elif sort_by == 'salary_low':
        query = query.order_by(Job.salary.asc(), Job.created_at.desc())
    elif sort_by == 'oldest':
        query = query.order_by(Job.created_at.asc())
    elif sort_by == 'company':
        query = query.order_by(Job.company.asc(), Job.created_at.desc())
    else:
        query = query.order_by(Job.created_at.desc())

    return query


def get_job_discovery_context(user):
    filters = {
        'query': request.args.get('query', '').strip(),
        'location': request.args.get('location', '').strip(),
        'job_type': request.args.get('job_type', '').strip(),
        'status': request.args.get('status', '').strip(),
        'sort': request.args.get('sort', 'newest').strip() or 'newest',
        'page': _to_int(request.args.get('page', 1), default=1, minimum=1, maximum=10000),
        'min_salary': _to_float(request.args.get('min_salary')),
        'max_salary': _to_float(request.args.get('max_salary')),
    }
    if (
        filters['min_salary'] is not None
        and filters['max_salary'] is not None
        and filters['min_salary'] > filters['max_salary']
    ):
        filters['min_salary'], filters['max_salary'] = filters['max_salary'], filters['min_salary']

    job_query = build_job_discovery_query(filters)
    pagination = job_query.paginate(page=filters['page'], per_page=DISCOVERY_PAGE_SIZE, error_out=False)
    jobs = list(pagination.items)
    annotate_match_scores(jobs, user)

    base_query = build_job_discovery_query({**filters, 'sort': 'newest'})
    total_results = base_query.order_by(None).count()
    open_jobs = base_query.filter(func.lower(Job.job_status) == 'open').order_by(None).count()
    avg_salary = base_query.with_entities(func.avg(Job.salary)).scalar()

    applied_job_ids = set()
    if user:
        applied_job_ids = {
            job_id for (job_id,) in db.session.query(JobApplication.job_id).filter_by(user_id=user.id).all()
        }

    job_types = [
        value
        for (value,) in db.session.query(Job.job_type)
        .filter(Job.job_type.isnot(None))
        .distinct()
        .order_by(Job.job_type.asc())
        .all()
    ]
    locations = [
        value
        for (value,) in db.session.query(Job.location)
        .filter(Job.location.isnot(None))
        .distinct()
        .order_by(Job.location.asc())
        .all()
    ]
    statuses = [
        value
        for (value,) in db.session.query(Job.job_status)
        .filter(Job.job_status.isnot(None))
        .distinct()
        .order_by(Job.job_status.asc())
        .all()
    ]

    return {
        'jobs': jobs,
        'pagination': pagination,
        'filters': filters,
        'job_types': job_types,
        'locations': locations,
        'statuses': statuses,
        'total_results': total_results,
        'open_jobs': open_jobs,
        'avg_salary': float(avg_salary or 0),
        'applied_job_ids': applied_job_ids,
    }


def render_jobseeker_dashboard_page():
    user = get_current_user()
    if not user:
        session.clear()
        flash('Session expired. Please sign in again.', 'warning')
        return redirect(url_for('signin'))
    context = get_job_discovery_context(user)
    context['username'] = user.username if user else ''
    return render_template('job_seeker.html', **context)


# Routes
@app.route('/')
def index():
    return render_template('landing_page.html')

@app.route('/home')
def home():
    if 'username' in session:
        return redirect(url_for('feed'))
    return render_template('home.html')

@app.route('/feed')
@login_required
def feed():
    user = get_current_user()
    page = request.args.get('page', 1, type=int)
    posts_pagination = Post.query.order_by(Post.created_at.desc()).paginate(page=page, per_page=10, error_out=False)
    posts = posts_pagination.items
    post_ids = [post.id for post in posts]
    
    # Check likes for current user
    liked_post_ids = set()
    if user:
        liked_post_ids = {like.post_id for like in Like.query.filter_by(user_id=user.id).all()}

    like_counts = {post_id: 0 for post_id in post_ids}
    comment_counts = {post_id: 0 for post_id in post_ids}
    if post_ids:
        likes_rows = (
            db.session.query(Like.post_id, func.count(Like.id))
            .filter(Like.post_id.in_(post_ids))
            .group_by(Like.post_id)
            .all()
        )
        comments_rows = (
            db.session.query(Comment.post_id, func.count(Comment.id))
            .filter(Comment.post_id.in_(post_ids))
            .group_by(Comment.post_id)
            .all()
        )
        like_counts.update({post_id: count for post_id, count in likes_rows})
        comment_counts.update({post_id: count for post_id, count in comments_rows})
    
    # Suggested users that are not already connected/requested with current user
    excluded_ids = {user.id}
    existing_links = Connection.query.filter(
        (Connection.requester_id == user.id) | (Connection.receiver_id == user.id)
    ).all()
    for link in existing_links:
        excluded_ids.add(link.requester_id)
        excluded_ids.add(link.receiver_id)
    suggested_users = User.query.filter(~User.id.in_(excluded_ids)).limit(3).all()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'posts': [{
                'id': p.id,
                'content': p.content,
                'media_url': p.media_url,
                'username': p.user.username,
                'user_id': p.user.id,
                'headline': p.user.headline or 'Professional',
                'created_at': p.created_at.strftime('%b %d, %H:%M'),
                'likes_count': like_counts.get(p.id, 0),
                'comments_count': comment_counts.get(p.id, 0),
                'is_liked': p.id in liked_post_ids
            } for p in posts],
            'has_next': posts_pagination.has_next,
            'next_page': posts_pagination.next_num
        })
    
    return render_template('feed.html', 
                           user=user, 
                           posts=posts, 
                           pagination=posts_pagination,
                           liked_post_ids=liked_post_ids,
                           like_counts=like_counts,
                           comment_counts=comment_counts,
                           suggested_users=suggested_users)


@app.route('/create_post', methods=['POST'])
@login_required
def create_post():
    user = get_current_user()
    content = request.form.get('content')
    if not content:
        flash('Post content cannot be empty.', 'warning')
        return redirect(url_for('feed'))
    
    filename = None
    media_file = request.files.get('media')
    if media_file and media_file.filename:
        filename = secure_filename(media_file.filename)
        media_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
    new_post = Post(user_id=user.id, content=content, media_url=filename)
    db.session.add(new_post)
    db.session.commit()
    flash('Post shared successfully!', 'success')
    return redirect(url_for('feed'))

@app.route('/like_post/<int:post_id>', methods=['POST'])
@login_required
def like_post(post_id):
    user = get_current_user()
    existing_like = Like.query.filter_by(user_id=user.id, post_id=post_id).first()
    
    if existing_like:
        db.session.delete(existing_like)
        status = 'unliked'
    else:
        new_like = Like(user_id=user.id, post_id=post_id)
        db.session.add(new_like)
        status = 'liked'
        
        # Notify post owner
        post = Post.query.get(post_id)
        if post and post.user_id != user.id:
            create_notification(post.user_id, f"{user.username} liked your post.", notif_type='like', target_id=post.id)
            
    db.session.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        likes_count = Like.query.filter_by(post_id=post_id).count()
        return jsonify({'status': status, 'likes_count': likes_count})
        
    return redirect(url_for('feed'))

@app.route('/comment_post/<int:post_id>', methods=['POST'])
@login_required
def comment_post(post_id):
    user = get_current_user()
    content = request.form.get('content')
    if not content:
        flash('Comment cannot be empty.', 'warning')
        return redirect(url_for('feed'))
        
    comment = Comment(user_id=user.id, post_id=post_id, content=content)
    db.session.add(comment)
    
    # Notify post owner
    post = Post.query.get(post_id)
    if post and post.user_id != user.id:
        create_notification(post.user_id, f"{user.username} commented on your post.", notif_type='comment', target_id=post.id)
        
    db.session.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'status': 'success',
            'username': user.username,
            'content': content,
            'comments_count': Comment.query.filter_by(post_id=post_id).count()
        })
        
    flash('Comment added.', 'success')
    return redirect(url_for('feed'))

@app.route('/mynetwork')
@login_required
def mynetwork():
    user = get_current_user()
    if not user:
        return redirect(url_for('signin'))
    
    # Pending requests for current user
    pending_requests = (
        db.session.query(Connection, User)
        .join(User, Connection.requester_id == User.id)
        .filter(Connection.receiver_id == user.id, Connection.status == 'pending')
        .all()
    )
    
    # Current connections
    connections_query = Connection.query.filter(
        ((Connection.requester_id == user.id) | (Connection.receiver_id == user.id)),
        Connection.status == 'accepted'
    ).all()
    
    connection_ids = [c.receiver_id if c.requester_id == user.id else c.requester_id for c in connections_query]
    connections = User.query.filter(User.id.in_(connection_ids)).all() if connection_ids else []
    
    # Suggested connections (not already connected or requested)
    excluded_ids = {user.id} | set(connection_ids)
    all_requests = Connection.query.filter((Connection.requester_id == user.id) | (Connection.receiver_id == user.id)).all()
    for r in all_requests:
        excluded_ids.add(r.requester_id)
        excluded_ids.add(r.receiver_id)
        
    suggestions = User.query.filter(~User.id.in_(excluded_ids)).limit(9).all()
    
    return render_template('mynetwork.html', 
                           pending_requests=pending_requests, 
                           connections=connections, 
                           suggestions=suggestions)


@app.route('/mynetworks')
@login_required
def mynetworks_alias():
    return redirect(url_for('mynetwork'))

@app.route('/connect/<int:receiver_id>', methods=['POST'])
@login_required
def connect(receiver_id):
    user = get_current_user()
    if user.id == receiver_id:
        return jsonify({'error': 'Cannot connect to self'}), 400
        
    existing = Connection.query.filter(
        ((Connection.requester_id == user.id) & (Connection.receiver_id == receiver_id)) |
        ((Connection.requester_id == receiver_id) & (Connection.receiver_id == user.id))
    ).first()
    
    if existing:
        return jsonify({'error': 'Connection already exists or pending'}), 400
        
    conn = Connection(requester_id=user.id, receiver_id=receiver_id, status='pending')
    db.session.add(conn)
    create_notification(receiver_id, f"{user.username} sent you a connection request.", notif_type='connect', target_id=user.id)
    db.session.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'pending'})
    return redirect(url_for('mynetwork'))

@app.route('/accept_connection/<int:connection_id>', methods=['POST'])
@login_required
def accept_connection(connection_id):
    user = get_current_user()
    conn = Connection.query.get_or_404(connection_id)
    if conn.receiver_id != user.id:
        abort(403)
        
    conn.status = 'accepted'
    create_notification(conn.requester_id, f"{user.username} accepted your connection request.")
    db.session.commit()
    flash('Connection request accepted.', 'success')
    return redirect(url_for('mynetwork'))

@app.route('/ignore_connection/<int:connection_id>', methods=['POST'])
@login_required
def ignore_connection(connection_id):
    user = get_current_user()
    conn = Connection.query.get_or_404(connection_id)
    if conn.receiver_id != user.id:
        abort(403)
        
    db.session.delete(conn)
    db.session.commit()
    flash('Connection request ignored.', 'info')
    return redirect(url_for('mynetwork'))

@app.route('/remove_connection/<int:user_id>', methods=['POST'])
@login_required
def remove_connection(user_id):
    user = get_current_user()
    conn = Connection.query.filter(
        ((Connection.requester_id == user.id) & (Connection.receiver_id == user_id)) |
        ((Connection.requester_id == user_id) & (Connection.receiver_id == user.id))
    ).first()
    
    if not conn:
        flash('Connection not found.', 'danger')
        return redirect(url_for('mynetwork'))
        
    db.session.delete(conn)
    db.session.commit()
    flash('Connection removed.', 'info')
    return redirect(url_for('mynetwork'))

@app.route('/messaging')
@login_required
def messaging():
    user = get_current_user()
    if not user:
        return redirect(url_for('signin'))
        
    # Get unique users the current user has chatted with
    sent_msgs = db.session.query(Message.receiver_id).filter_by(sender_id=user.id)
    rcvd_msgs = db.session.query(Message.sender_id).filter_by(receiver_id=user.id)
    chat_user_ids = list(set([uid[0] for uid in sent_msgs.union(rcvd_msgs).all()]))
    
    chat_users = User.query.filter(User.id.in_(chat_user_ids)).all() if chat_user_ids else []
    
    # If a specific user is targeted in query params
    active_user_id = request.args.get('user_id', type=int)
    active_user = None
    messages = []
    
    if active_user_id:
        active_user = User.query.get(active_user_id)
        if active_user:
            messages = Message.query.filter(
                ((Message.sender_id == user.id) & (Message.receiver_id == active_user_id)) |
                ((Message.sender_id == active_user_id) & (Message.receiver_id == user.id))
            ).order_by(Message.created_at.asc()).all()
            
            # Ensure targeted user is in the sidebar list
            found = False
            for cu in chat_users:
                if cu.id == active_user.id:
                    found = True
                    break
            if not found:
                chat_users.insert(0, active_user)
                
    elif chat_users:
        active_user = chat_users[0]
        messages = Message.query.filter(
            ((Message.sender_id == user.id) & (Message.receiver_id == active_user.id)) |
            ((Message.sender_id == active_user.id) & (Message.receiver_id == user.id))
        ).order_by(Message.created_at.asc()).all()
        
    return render_template('messaging.html', 
                           chat_users=chat_users, 
                           active_user=active_user, 
                           messages=messages)

@app.route('/get_messages/<int:receiver_id>')
@login_required
def get_messages(receiver_id):
    user = get_current_user()
    messages = Message.query.filter(
        ((Message.sender_id == user.id) & (Message.receiver_id == receiver_id)) |
        ((Message.sender_id == receiver_id) & (Message.receiver_id == user.id))
    ).order_by(Message.created_at.asc()).all()
    
    return jsonify([{
        'id': m.id,
        'sender_id': m.sender_id,
        'content': m.content,
        'created_at': m.created_at.strftime('%H:%M')
    } for m in messages])

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    user = get_current_user()
    receiver_id = request.form.get('receiver_id', type=int)
    content = request.form.get('content')
    
    if not receiver_id or not content:
        return jsonify({'error': 'Missing receiver or content'}), 400
        
    msg = Message(sender_id=user.id, receiver_id=receiver_id, content=content)
    db.session.add(msg)
    create_notification(receiver_id, f"You received a new message from {user.username}")
    db.session.commit()
    
    return jsonify({
        'id': msg.id,
        'sender_id': msg.sender_id,
        'content': msg.content,
        'created_at': msg.created_at.strftime('%H:%M')
    })


@app.route('/jobs')


def job_list():
    jobs = Job.query.all()  # Fetch all jobs
    return render_template('manage_job.html', jobs=jobs)

@app.route('/job_detail/<int:job_id>')
def job_detail(job_id):
    if session.get('username'):
        return redirect(url_for('job_details', job_id=job_id))
    job = Job.query.get_or_404(job_id)
    return render_template('job_detail.html', job=job, user_applied=False)

@app.route('/learn_more')
def learn():
    return render_template('learn_more.html')

#for notifications
@app.route('/notify')
def notify():
    # Legacy route kept for compatibility; real notification UI lives at /notifications.
    return redirect(url_for('notifications'))




@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if 'username' in session:
        return redirect(url_for('dashboard'))  # Redirect to dashboard if already logged in

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Ensure both fields are provided
        if not username or not password:
            flash('Username and password are required.', 'warning')
            return redirect(url_for('signin'))

        # Fetch user from the database
        user = User.query.filter_by(username=username).first()

        if user and verify_password(user, password):
            session.clear()
            session['username'] = username
            session['role'] = user.role
            session['user_id'] = user.id
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password. Please try again.', 'danger')

    return render_template('signin.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # Get form data
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password') or ''
        email = (request.form.get('email') or '').strip().lower()
        role = request.form.get('role')

        if not username or not email or not password or not role:
            flash('All fields are required.', 'warning')
            return redirect(url_for('signup'))

        # ✅ Validate role
        if role not in ALLOWED_SIGNUP_ROLES:
            flash('Invalid role selected.', 'danger')
            return redirect(url_for('signup'))

        # ✅ Validate email
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash('Invalid email address.', 'danger')
            return redirect(url_for('signup'))

        # ✅ Validate password
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'warning')
            return redirect(url_for('signup'))

        # ✅ Check existing user
        existing_user = User.query.filter(
            or_(User.username == username, User.email == email)
        ).first()

        if existing_user:
            flash('Username or email already exists.', 'warning')
            return redirect(url_for('signup'))

        # ✅ Create new user
        new_user = User(
            username=username,
            password=generate_password_hash(password, method='pbkdf2:sha256'),
            email=email,
            role=role
        )

        db.session.add(new_user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Username or email already exists.', 'warning')
            return redirect(url_for('signup'))

        # ✅ Send welcome email
        try:
            signin_url = url_for('signin', _external=True)

            msg = Message(
                subject="Welcome to Our Platform!",
                recipients=[email],
                body=f"""
Hello {username}! 🎉

Welcome to our platform 🚀

Login here:
{signin_url}

Enjoy your journey! 😊
"""
            )

            if send_email_safe(msg):
                email_log = EmailLog(
                    user_id=new_user.id,
                    username=username,
                    email=email,
                    message=msg.body,
                    sent_at=datetime.utcnow()
                )
                db.session.add(email_log)
                db.session.commit()

            flash('Signup successful! Welcome to the platform.', 'success')

        except Exception as e:
            flash(f'Signed up, but email failed: {str(e)}', 'warning')

        # ✅ Auto login
        session['username'] = new_user.username
        session['user_id'] = new_user.id
        session['role'] = new_user.role

        return redirect(url_for('dashboard'))

    return render_template('signup.html')


@app.route('/view_email_logs')
@login_required
@role_required('superadmin')
def view_email_logs():
    # Retrieve email logs (filter by user or as needed)
    email_logs = EmailLog.query.all()

    return render_template('email_logs.html', email_logs=email_logs)


# Save a job using session
@app.route('/save_job/<int:job_id>', methods=['POST'])
@login_required
def save_job_view(job_id):
    flash('Saved Jobs has been removed. Use Applied Jobs and Notifications to track progress.', 'info')
    return redirect(url_for('jobseeker_dashboard'))

@app.route('/saved_job')
@login_required
def saved_jobs_view():
    flash('Saved Jobs has been removed.', 'info')
    return redirect(url_for('jobseeker_dashboard'))

# Remove a saved job using session
@app.route('/remove_saved_job/<int:job_id>', methods=['POST'])
@login_required
def remove_saved_job_view(job_id):
    flash('Saved Jobs has been removed.', 'info')
    return redirect(url_for('jobseeker_dashboard'))
    
# code for search bar
@app.route('/search', methods=['GET'])
@login_required
@role_required('jobseeker')
def search_jobs():
    return render_jobseeker_dashboard_page()


@app.route('/add_education', methods=['GET', 'POST'])
@login_required
def add_education():
    # Get the logged-in user's ID (replace with session or authentication mechanism)
    user = User.query.filter_by(username=session['username']).first()
    user_id = user.id

    # Check if the user already has an education record
    existing_education = Education.query.filter_by(user_id=user_id).first()

    if request.method == 'POST':
        # Get form data
        degree = request.form['degree']
        institution = request.form['institution']
        year_of_passing = request.form['year_of_passing']
        tenth = request.form.get('tenth')
        twelfth = request.form.get('twelfth')

        if existing_education:
            # If education already exists, update the record
            existing_education.degree = degree
            existing_education.institution = institution
            existing_education.year_of_passing = year_of_passing
            existing_education.tenth = tenth
            existing_education.twelfth = twelfth
            
            db.session.commit()
            flash('Education details updated successfully!', 'success')
        else:
            # If no education record exists, create a new one
            new_education = Education(
                user_id=user_id,
                degree=degree,
                institution=institution,
                year_of_passing=year_of_passing,
                tenth=tenth,
                twelfth=twelfth
            )
            try:
                db.session.add(new_education)
                db.session.commit()
                flash('Education details added successfully!', 'success')
            except Exception as e:
                db.session.rollback()  # Rollback in case of error
                flash(f'An error occurred: {str(e)}', 'danger')

        return redirect(url_for('profile'))  # Redirect to profile page

    # Render the form to add or edit education details
    return render_template('add_education.html', existing_education=existing_education)

@app.route('/edit_education/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_education(user_id):
    if not can_manage_user(user_id):
        abort(403)
    user = User.query.get_or_404(user_id)

    # Fetch the user's existing education details
    existing_education = Education.query.filter_by(user_id=user_id).first()
    user_id=user.id

    if request.method == 'POST':
        # Retrieve form data
        degree = request.form.get('degree')
        institution = request.form.get('institution')
        year_of_passing = request.form.get('year_of_passing')

        # If education doesn't exist, create a new entry
        if not existing_education:
            existing_education = Education(user_id=user_id)

        # Update the education details
        existing_education.degree = degree
        existing_education.institution = institution
        existing_education.year_of_passing = year_of_passing

        # Save to the database
        db.session.add(existing_education)
        db.session.commit()

        flash('Education details updated successfully!', 'success')
        return redirect(url_for('profile', user_id=user_id))

    return render_template('edit_education.html', user=user, existing_education=existing_education)


@app.route('/add_skills', methods=['GET', 'POST'])
@login_required
def add_skills():
    user = User.query.filter_by(username=session['username']).first()
    user_id = user.id

    if request.method == 'POST':
        skill_name = request.form['skill_name']

        # Get the user's existing skills
        existing_skills = skills.query.filter_by(user_id=user_id).first()

        # If user already has skills
        if existing_skills:
            # Split the existing skills into a list
            skills_list = existing_skills.skill_name.split(", ")

            # Check if the skill already exists in the list
            if skill_name not in skills_list:
                skills_list.append(skill_name)  # Add the new skill to the list
                updated_skills = ", ".join(skills_list)  # Join skills with a comma

                existing_skills.skill_name = updated_skills  # Update the skills in the database
                db.session.commit()  # Save the changes to the database
                flash('Skills updated successfully!', 'success')
            else:
                flash('This skill is already added.', 'warning')
        else:
            # If no skills exist, create a new entry
            new_skill = skills(user_id=user_id, skill_name=skill_name)
            try:
                db.session.add(new_skill)
                db.session.commit()
                flash('Skill added successfully!', 'success')
            except Exception as e:
                db.session.rollback()  # Rollback in case of error
                flash(f'An error occurred: {str(e)}', 'danger')

        return redirect(url_for('add_skills'))  # Redirect to the same page to refresh the list

    # Retrieve the skills of the current user
    user_skills = skills.query.filter_by(user_id=user_id).all()

    return render_template('add_skills.html', user_skills=user_skills)

#edit skills
@app.route('/edit_skills/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_skills(user_id):
    if not can_manage_user(user_id):
        abort(403)

    user = User.query.get_or_404(user_id)
    existing_skills = skills.query.filter_by(user_id=user.id).first()
    if request.method == 'POST':
        if not existing_skills:
            existing_skills = skills(user_id=user.id, skill_name=request.form['skills'])
            db.session.add(existing_skills)
        else:
            existing_skills.skill_name = request.form['skills']
        db.session.commit()
        flash('Skills updated successfully!', 'success')
        return redirect(url_for('profile'))
    return render_template('edit_skills.html', user=user, existing_skills=existing_skills)

@app.route('/delete_skill/<int:skill_id>', methods=['POST'])
@login_required
def delete_skill(skill_id):
    skill = skills.query.get_or_404(skill_id)
    current_user = get_current_user()
    if not current_user or (skill.user_id != current_user.id and session.get('role') != 'superadmin'):
        abort(403)
    
    # Delete the skill
    try:
        db.session.delete(skill)
        db.session.commit()
        flash('Skill deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred: {str(e)}', 'danger')

    return redirect(url_for('add_skills'))  # Redirect back to the add_skills page



# Route to add work experience
@app.route('/add_work_experience', methods=['GET', 'POST'])
@login_required
def add_work_experience():
    if 'username' not in session:
        flash("Please log in to add work experience.", "warning")
        return redirect(url_for('signin'))

    user = User.query.filter_by(username=session['username']).first()
    user_id = user.id

    if request.method == 'POST':
        # Get form data
        company_name = request.form.get('company_name')
        role = request.form.get('role')
        job_title = request.form.get('job_title')
        start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d')
        end_date = request.form.get('end_date')
        end_date = datetime.strptime(end_date, '%Y-%m-%d') if end_date else None
        description = request.form.get('description')

        # Check if the user already has a work experience record
        existing_work_experience = WorkExperience.query.filter_by(user_id=user_id).first()

        if existing_work_experience:
            # Update the existing record
            existing_work_experience.company_name = company_name
            existing_work_experience.role = role
            existing_work_experience.job_title = job_title
            existing_work_experience.start_date = start_date
            existing_work_experience.end_date = end_date
            existing_work_experience.description = description
            flash('Work experience updated successfully!', 'success')
        else:
            # Create a new work experience record
            new_work_experience = WorkExperience(
                user_id=user_id,
                company_name=company_name,
                role=role,
                job_title=job_title,
                start_date=start_date,
                end_date=end_date,
                description=description,
            )
            db.session.add(new_work_experience)
            flash('Work experience added successfully!', 'success')

        # Commit the changes to the database
        db.session.commit()

        return redirect(url_for('profile'))

    return render_template('add_work_experience.html')


@app.route('/upload_resume', methods=['GET', 'POST'])
@login_required
def upload_resume():
    if request.method == 'POST':
        # Check if the POST request has the file part
        file = request.files.get('resume')

        if file and allowed_file(file.filename):
            # Secure the file name
            filename = secure_filename(file.filename)
            # Save the file to the upload folder
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Fetch the current user
            user = User.query.filter_by(username=session['username']).first()
            user_id = user.id

            # Check if the user already has a resume
            existing_resume = Resume.query.filter_by(user_id=user_id).first()

            if existing_resume:
                # Delete the old resume file from the server if it exists
                old_resume_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_resume.resume)
                if os.path.exists(old_resume_path):
                    os.remove(old_resume_path)

                # Update the existing resume record
                existing_resume.resume = filename
                flash('Resume updated successfully!', 'success')
            else:
                # If the user doesn't have a resume, create a new record
                new_resume = Resume(user_id=user_id, resume=filename)
                db.session.add(new_resume)
                flash('Resume uploaded successfully!', 'success')

            try:
                # Commit the session to save data to the database
                db.session.commit()
            except Exception as e:
                db.session.rollback()  # Rollback in case of an error
                flash(f"An error occurred: {str(e)}", 'danger')

            return redirect(url_for('upload_resume'))
        else:
            flash('Invalid file type. Please upload a PDF, DOC, DOCX, or TXT file.', 'danger')

    return render_template('upload_resume.html')


@app.route('/profile')
@login_required
def profile():
    # Retrieve the user from the database using the username stored in the session
    user = User.query.filter_by(username=session['username']).first()

    # Check if the user is found
    if user:
        # Fetch the work experience records for the logged-in user
        work_experience = WorkExperience.query.filter_by(user_id=user.id).all()
        user_skills = skills.query.filter_by(user_id=user.id).all()

        # Suggested users
        excluded_ids = {user.id}
        existing_links = Connection.query.filter(
            (Connection.requester_id == user.id) | (Connection.receiver_id == user.id)
        ).all()
        for link in existing_links:
            excluded_ids.add(link.requester_id)
            excluded_ids.add(link.receiver_id)
        suggested_users = User.query.filter(~User.id.in_(excluded_ids)).limit(5).all()

        # Pass the user and work_experience data to the template
        return render_template('profile.html', user=user, work_experience=work_experience, user_skills=user_skills, suggested_users=suggested_users)
    else:
        # If user not found, redirect to sign-in
        return redirect(url_for('signin'))

    
#edit profile
@app.route('/edit_profile/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_profile(user_id):
    user = User.query.get_or_404(user_id)
    if not can_manage_user(user_id):
        abort(403)

    if request.method == 'POST':
        requested_username = request.form['username'].strip()
        requested_email = request.form['email'].strip()

        duplicate_user = User.query.filter(
            User.id != user.id,
            ((User.username == requested_username) | (User.email == requested_email))
        ).first()
        if duplicate_user:
            flash('Username or email already in use.', 'danger')
            return redirect(url_for('edit_profile', user_id=user.id))

        user.username = requested_username
        user.email = requested_email
        user.bio = request.form['bio']
        user.headline = request.form.get('headline')
        user.phone_number = request.form.get('phone_number', None)
        user.location = request.form['location']

        # Handle profile picture upload
        profile_picture = request.files.get('profile_picture')
        if profile_picture and profile_picture.filename:
            filename = secure_filename(profile_picture.filename)
            profile_picture.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.profile_picture = filename

        # Handle cover photo upload
        cover_photo = request.files.get('cover_photo')
        if cover_photo and cover_photo.filename:
            filename = secure_filename(cover_photo.filename)
            cover_photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.cover_photo = filename

        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))


    return render_template('edit_profile.html', user=user)

# Route to display all candidates
@app.route('/candidates')
def candidates_view():
    try:
        # Fetch user data and filter out recruiters
        users = User.query.filter(User.role != 'recruiter').all()  # Exclude recruiters
        return render_template('candidates.html', users=users)
    except Exception as e:
        flash(f'An error occurred: {e}', 'danger')
        return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('signin'))
    return redirect(url_for('feed'))


    
@app.route('/superadmin_dashboard')
@login_required
@role_required('superadmin')
def superadmin_dashboard():
    # Fetch all users and jobs
    users = User.query.all()
    jobs = Job.query.all()

    return render_template('superadmin_dashboard.html', users=users, jobs=jobs)


# Edit User
@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
@role_required('superadmin')
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        user.role = request.form['role']
        user.bio = request.form.get('bio', user.bio)
        user.profile_picture = request.form.get('profile_picture', user.profile_picture)
        db.session.commit()

        return redirect(url_for('superadmin_dashboard'))

    return render_template('edit_user.html', user=user)


# Edit Job
@app.route('/edit_job/<int:job_id>', methods=['GET', 'POST'])
@login_required
@role_required('superadmin')
def edit_job(job_id):
    job = Job.query.get_or_404(job_id)

    if request.method == 'POST':
        job.title = request.form['title']
        job.description = request.form['description']
        job.company = request.form['company']
        job.location = request.form['location']
        job.job_type = request.form['job_type']
        job.job_status = request.form['job_status']
        job.salary = request.form.get('salary', job.salary)
        db.session.commit()

        return redirect(url_for('superadmin_dashboard'))

    return render_template('edit_job.html', job=job)


# Delete User
@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
@role_required('superadmin')
def delete_user(user_id):
    current_user = get_current_user()
    if current_user and current_user.id == user_id:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for('superadmin_dashboard'))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()

    return redirect(url_for('superadmin_dashboard'))






#khali pasun old code
#manage job (remove)
@app.route('/manage_job')
@login_required
@role_required('recruiter', 'superadmin')
def manage_job():
    current_user = get_current_user()
    if not current_user and session.get('role') != 'superadmin':
        session.clear()
        flash('Session expired. Please sign in again.', 'warning')
        return redirect(url_for('signin'))

    if session.get('role') == 'superadmin':
        jobs = Job.query.all()
    else:
        jobs = Job.query.filter_by(recruiter_id=current_user.id).all()
    return render_template('manage_job.html', jobs=jobs)


# Route to delete a job
@app.route('/delete_job/<int:job_id>', methods=['POST'])
@login_required
@role_required('recruiter', 'superadmin')
def delete_job(job_id):
    current_user = get_current_user()
    job = Job.query.get_or_404(job_id)
    if session.get('role') == 'recruiter' and not current_user:
        session.clear()
        return redirect(url_for('signin'))
    if session.get('role') == 'recruiter' and current_user and job.recruiter_id != current_user.id:
        abort(403)
    try:
        # Explicitly delete related job applications before deleting the job
        JobApplication.query.filter_by(job_id=job.id).delete()
        db.session.delete(job)
        db.session.commit()
        flash("Job deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    if session.get('role') == 'superadmin':
        return redirect(url_for('superadmin_dashboard'))
    return redirect(url_for('manage_job'))


#jobseeker dashboard
@app.route('/jobseeker_dashboard')
@login_required
@role_required('jobseeker')
def jobseeker_dashboard():
    return render_jobseeker_dashboard_page()


# admin route
@app.route('/admin_dashboard')
@login_required
@role_required('recruiter')
def admin_dashboard():
    user = get_current_user()
    if not user:
        session.clear()
        flash('Session expired. Please sign in again.', 'warning')
        return redirect(url_for('signin'))

    recruiter_jobs = Job.query.filter_by(recruiter_id=user.id).order_by(Job.created_at.desc()).all()
    recruiter_job_ids = [job.id for job in recruiter_jobs]

    total_applications = 0
    recent_applications = []
    if recruiter_job_ids:
        total_applications = (
            JobApplication.query.filter(JobApplication.job_id.in_(recruiter_job_ids)).count()
        )

        recent_rows = (
            db.session.query(JobApplication, Job, User)
            .join(Job, Job.id == JobApplication.job_id)
            .join(User, User.id == JobApplication.user_id)
            .filter(Job.recruiter_id == user.id)
            .order_by(JobApplication.applied_at.desc())
            .limit(6)
            .all()
        )
        recent_applications = [
            {
                'candidate_name': candidate.username,
                'candidate_email': candidate.email,
                'job_title': job.title,
                'applied_at': application.applied_at,
            }
            for application, job, candidate in recent_rows
        ]

    metrics = {
        'total_jobs': len(recruiter_jobs),
        'open_jobs': sum(1 for job in recruiter_jobs if _normalize(job.job_status) == 'open'),
        'total_applications': total_applications,
        'unread_notifications': Notification.query.filter_by(user_id=user.id, is_read=False).count(),
        'average_salary': (
            float(
                db.session.query(func.avg(Job.salary))
                .filter(Job.recruiter_id == user.id)
                .scalar()
                or 0
            )
        ),
    }

    return render_template(
        'admin_dashboard.html',
        user=user,
        jobs=recruiter_jobs[:6],
        metrics=metrics,
        recent_applications=recent_applications,
    )


@app.route('/add_job', methods=['GET', 'POST'])
def add_job():
    if 'username' not in session or session['role'] != 'recruiter':
        return redirect(url_for('home'))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        company = request.form['company']
        photo = request.files.get('photo')  # Use request.files.get() to safely handle file input
        location = request.form['location']
        job_type = request.form['job_type']
        job_status = request.form['job_status']
        qualifications = request.form.get('qualifications')
        requirements = request.form.get('requirements')
        responsibilities = request.form.get('responsibilities')
        experience = request.form.get('experience', type=int)
        vacancy = request.form.get('vacancy', type=int)
        salary = request.form.get('salary', type=float)

        # Handle photo if it exists
        if photo and photo.filename:  # Ensure a file is uploaded
            filename = secure_filename(photo.filename)  # Sanitize the file name
            upload_folder = app.config['UPLOAD_FOLDER']
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)  # Create folder if it doesn't exist
            photo.save(os.path.join(upload_folder, filename))  # Save the file in the upload folder
        else:
            filename = None  # If no photo is uploaded, set filename to None

        # Fetch the logged-in user
        user = User.query.filter_by(username=session['username']).first()
        if not user:
            flash('User session is invalid. Please sign in again.', 'danger')
            return redirect(url_for('signin'))

        # Create and save the new job
        new_job = Job(
            title=title,
            description=description,
            company=company,
            photo=filename,  # Store the filename or None in the database
            location=location,
            job_type=job_type,
            job_status=job_status,
            qualifications=qualifications,
            requirements=requirements,
            responsibilities=responsibilities,
            experience=experience,
            vacancy=vacancy,
            salary=salary,
            recruiter_id=user.id  # Ensure this is included
        )
        db.session.add(new_job)
        db.session.commit()
        flash('Job added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('create_job.html')

@app.route('/update_job/<int:job_id>', methods=['GET', 'POST'])
def update_job(job_id):
    if 'username' not in session or session['role'] != 'recruiter':
        return redirect(url_for('home'))

    # Fetch the job to be updated
    job = Job.query.get_or_404(job_id)
    current_user = get_current_user()
    if not current_user or job.recruiter_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        company = request.form['company']
        photo = request.files.get('photo')  # Handle file upload
        location = request.form['location']
        job_type = request.form['job_type']
        job_status = request.form['job_status']
        qualifications = request.form.get('qualifications')
        requirements = request.form.get('requirements')
        responsibilities = request.form.get('responsibilities')
        experience = request.form.get('experience', type=int)
        vacancy = request.form.get('vacancy', type=int)
        salary = request.form.get('salary', type=float)

        # Update the job with the new data
        job.title = title
        job.description = description
        job.company = company
        job.location = location
        job.job_type = job_type
        job.job_status = job_status
        job.qualifications = qualifications
        job.requirements = requirements
        job.responsibilities = responsibilities
        job.experience = experience
        job.vacancy = vacancy
        job.salary = salary

        # Handle photo upload (if a new photo is uploaded)
        if photo and photo.filename:
            filename = secure_filename(photo.filename)
            upload_folder = app.config['UPLOAD_FOLDER']
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)  # Create the folder if not exists
            photo.save(os.path.join(upload_folder, filename))  # Save the new photo
            job.photo = filename  # Update the photo field in the job

        # Commit changes to the database
        db.session.commit()
        flash('Job updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))  # Redirect to the dashboard

    return render_template('update_job.html', job=job)


# Helper function to check if the user has applied for a specific job
def has_applied_for_job(job_id):
    current_user = get_current_user()
    if not current_user:
        return False
    user_applied = JobApplication.query.filter_by(job_id=job_id, user_id=current_user.id).first()
    return user_applied is not None

@app.route('/applied_jobs')
@login_required
@role_required('jobseeker')
def applied_jobs():
    user = get_current_user()
    user_id = user.id

    # Query the job applications for the logged-in user using user_id
    job_applications = JobApplication.query.filter_by(user_id=user_id).all()

    # Fetch the corresponding job details for each application
    applied_jobs_details = []
    for application in job_applications:
        applied_jobs_details.append({
            'id': application.job.id,
            'title': application.job.title,
            'company': application.job.company,
            'location': application.job.location,
            'applied_date': application.applied_at.strftime("%d-%m-%Y"),
        })

    # Pass the data to the template
    return render_template('applied_jobs.html', applied_jobs=applied_jobs_details)

@app.route('/apply_job/<int:job_id>', methods=['POST'])
@login_required
@role_required('jobseeker')
def apply_job(job_id):
    user = get_current_user()
    username = user.username
    user_id = user.id

    # Fetch the job using the job ID
    job = Job.query.get_or_404(job_id)

    # Check if the user has already applied for the job
    existing_application = JobApplication.query.filter_by(job_id=job_id, user_id=user_id).first()
    if existing_application:
        flash("You have already applied for this job.", "info")
        return redirect(url_for('job_details', job_id=job_id))

    # Save the job application with user_id
    try:
        # Create job application
        application = JobApplication(
            job_id=job.id, 
            username=username,  # Store the username
            user_id=user_id,    # Store the user_id fetched from the User table
            applied_at=datetime.now()
        )
        db.session.add(application)
        db.session.commit()

        create_notification(
            user_id=job.recruiter_id,
            job_id=job.id,
            message=f"{user.username} has applied for your job: {job.title}",
        )
        create_notification(
            user_id=user.id,
            job_id=job.id,
            message=f"Application submitted: {job.title} at {job.company}",
        )
        db.session.commit()

        recruiter = User.query.get(job.recruiter_id)
        if recruiter and recruiter.email:
            msg = Message(
                subject="New Job Application",
                recipients=[recruiter.email],
                body=f"Hello {recruiter.username},\n\n{user.username} has applied for your job '{job.title}'."
            )
            send_email_safe(msg)

        flash("Your application has been submitted successfully! The recruiter has been notified.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred while applying for the job: {e}", "error")
        return redirect(url_for('job_details', job_id=job_id))

    return redirect(url_for('job_details', job_id=job_id))

@app.route('/withdraw_application/<int:job_id>', methods=['POST'])
@login_required
@role_required('jobseeker')
def withdraw_application(job_id):
    current_user = get_current_user()
    job = Job.query.get_or_404(job_id)

    # Fetch the job application to be withdrawn
    application = JobApplication.query.filter_by(job_id=job_id, user_id=current_user.id).first()

    if application:
        db.session.delete(application)
        create_notification(
            user_id=job.recruiter_id,
            job_id=job.id,
            message=f"{current_user.username} withdrew application for {job.title}",
        )
        create_notification(
            user_id=current_user.id,
            job_id=job.id,
            message=f"Application withdrawn: {job.title} at {job.company}",
        )
        db.session.commit()
        flash("Your application has been withdrawn successfully.", "success")
    else:
        flash("You have not applied for this job.", "info")

    return redirect(url_for('job_details', job_id=job_id))



@app.route('/job_details/<int:job_id>', methods=['GET'])
@login_required
def job_details(job_id):
    # Get the job details using job_id
    job = Job.query.get_or_404(job_id)
    user_applied = has_applied_for_job(job_id) if session.get('role') == 'jobseeker' else False

    # Render page with the correct button depending on user application status
    return render_template('job_detail.html', job=job, user_applied=user_applied)


@app.route('/mark_notification_read/<int:notification_id>', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    current_user = get_current_user()
    if session.get('role') != 'superadmin' and notification.user_id != current_user.id:
        abort(403)
    notification.is_read = True
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success'})
    
    return redirect(url_for('notifications'))

@app.route('/notifications', methods=['GET'])
@login_required
def notifications():
    current_user = get_current_user()
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    return render_template('notifications.html', notifications=notifications)

@app.route('/admin_notifications', methods=['GET'])
@login_required
def admin_notifications():
    current_user = get_current_user()
    if session.get('role') == 'superadmin':
        notifications = Notification.query.order_by(Notification.created_at.desc()).limit(100).all()
    else:
        notifications = Notification.query.filter_by(
            user_id=current_user.id
        ).order_by(Notification.created_at.desc()).limit(100).all()

    return render_template('admin_notifications.html', notifications=notifications)


@app.route('/notifications/mark_all_read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    current_user = get_current_user()
    if session.get('role') == 'superadmin':
        Notification.query.filter_by(is_read=False).update({Notification.is_read: True})
    else:
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update({Notification.is_read: True})
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    # Redirect back to where the user came from, preventing normal users from seeing admin dashboard
    return redirect(request.referrer or url_for('notifications'))


@app.route('/notifications/poll', methods=['GET'])
@login_required
def notification_poll():
    current_user = get_current_user()
    if session.get('role') == 'superadmin':
        base_query = Notification.query
    else:
        base_query = Notification.query.filter_by(user_id=current_user.id)

    unread_count = base_query.filter_by(is_read=False).count()
    latest = base_query.order_by(Notification.created_at.desc()).limit(6).all()
    data = [
        {
            'id': n.id,
            'message': n.message,
            'is_read': n.is_read,
            'notif_type': n.notif_type,
            'target_id': n.target_id,
            'job_id': n.job_id,
            'created_at': n.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        }
        for n in latest
    ]
    return jsonify({'unread_count': unread_count, 'notifications': data})


#about us
@app.route('/about')
def about():
    if 'username' not in session:
        return redirect(url_for('signin'))  # Redirect to sign-in if not logged in
    return render_template('about.html')

#logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/user_detail/<int:user_id>')
def user_detail_with_id(user_id):
    # Fetch user information from the database
    user = User.query.get_or_404(user_id)  # Fetch user by ID or raise 404 if not found
    work_experience = WorkExperience.query.filter_by(user_id=user_id).all()  # Fetch work experiences for the user
    user_skills = skills.query.filter_by(user_id=user_id).all()  # Fetch skills for the user from a related table
    
    current_user = get_current_user()
    suggested_users = []
    if current_user:
        excluded_ids = {current_user.id}
        existing_links = Connection.query.filter(
            (Connection.requester_id == current_user.id) | (Connection.receiver_id == current_user.id)
        ).all()
        for link in existing_links:
            excluded_ids.add(link.requester_id)
            excluded_ids.add(link.receiver_id)
        suggested_users = User.query.filter(~User.id.in_(excluded_ids)).limit(5).all()

    return render_template('user_detail.html', user=user, work_experience=work_experience, skills=user_skills, suggested_users=suggested_users)
    

# Route to render feedback form
@app.route('/feedback', methods=['GET'])
def feedback_form():
    return render_template('feedback.html')

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    name = request.form.get('name')
    email = request.form.get('email')
    feedback_type = request.form.get('feedback_type')
    message = request.form.get('message')

    # Create a new Feedback instance
    feedback_entry = Feedback(
        name=name,
        email=email,
        feedback_type=feedback_type,
        message=message
    )

    try:
        db.session.add(feedback_entry)
        db.session.commit()
        return redirect(url_for('feedback_form'))  # Redirect to feedback report page
    except Exception as e:
        return f"Error: {e}", 500


@app.route('/applied_candidates/<int:user_id>')
@login_required
def applied_candidates(user_id):
    current_user = get_current_user()
    if (
        not current_user
        or (
            session.get('role') not in {'recruiter', 'superadmin'}
            and current_user.id != user_id
        )
    ):
        abort(403)

    try:
        # Define the SQL query with proper parameter binding
        query = text("""
            SELECT 
                ja.id AS application_id, 
                ja.job_id, 
                ja.applied_at,
                ja.user_id, 
                u.username AS user_username,
                u.email AS user_email,
                u.profile_picture AS profile_picture,
                u.phone_number AS user_phone,
                j.title AS job_title,
                j.company AS company_name
            FROM 
                job_application ja
            JOIN 
                `user` u ON ja.user_id = u.id
            LEFT JOIN
                job j ON ja.job_id = j.id
            WHERE 
                ja.user_id = :user_id
            ORDER BY 
                ja.applied_at ASC
            LIMIT 25
        """)

        # Execute the query with parameters
        results = db.session.execute(query, {'user_id': user_id})
        
        # Process the results
        applications = [{
            'application_id': row.application_id,
            'job_id': row.job_id,
            'job_title': row.job_title,
            'user_id':row.user_id,
            'company_name': row.company_name,
            'applied_at': row.applied_at,
            'user_username': row.user_username,
            'profile_picture': row.profile_picture,
            'user_email': row.user_email,
            'user_phone': row.user_phone
        } for row in results]

        return render_template(
            'applied_candidates.html',
            applications=applications,
            current_time=datetime.now()
        )

    except Exception as e:
        current_app.logger.error(f"Error fetching applications for user {user_id}: {str(e)}")
        return render_template(
            'applied_candidates.html',
            applications=[],
            error="Unable to fetch applications. Please try again later."
        )
        
@app.route('/blog')
def blog():
    return render_template('blog.html')

@app.route('/contact_us')
def contact_us():
    return render_template('contact_us.html')


@app.route('/contact', methods=['POST'])
def contact():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    message = request.form.get('message', '').strip()

    if not name or not email or not message:
        flash('Please fill in name, email, and message.', 'warning')
        return redirect(url_for('contact_us'))

    recipient = os.environ.get('CONTACT_RECEIVER_EMAIL') or app.config.get('MAIL_DEFAULT_SENDER')
    if recipient:
        msg = Message(
            subject='New Contact Form Submission',
            recipients=[recipient],
            body=(
                f"Name: {name}\n"
                f"Email: {email}\n"
                f"Phone: {phone or 'N/A'}\n\n"
                f"Message:\n{message}"
            ),
        )
        send_email_safe(msg)

    flash('Thanks for contacting us. We will get back to you soon.', 'success')
    return redirect(url_for('contact_us'))
  
  
#Reports ALL  
# Route for Job Reports
@app.route('/job_reports', methods=['GET'])
@login_required
@role_required('superadmin')
def job_reports():
    # Get query parameters
    title = request.args.get('title')
    company = request.args.get('company')
    location = request.args.get('location')
    job_type = request.args.get('job_type')
    job_status = request.args.get('job_status')
    min_salary = request.args.get('min_salary', type=float)
    max_salary = request.args.get('max_salary', type=float)
    experience = request.args.get('experience', type=int)

    # Build the query
    query = Job.query
    if title:
        query = query.filter(Job.title.ilike(f'%{title}%'))
    if company:
        query = query.filter(Job.company.ilike(f'%{company}%'))
    if location:
        query = query.filter(Job.location.ilike(f'%{location}%'))
    if job_type:
        query = query.filter(Job.job_type.ilike(f'%{job_type}%'))
    if job_status:
        query = query.filter(Job.job_status.ilike(f'%{job_status}%'))
    if min_salary:
        query = query.filter(Job.salary >= min_salary)
    if max_salary:
        query = query.filter(Job.salary <= max_salary)
    if experience:
        query = query.filter(Job.experience == experience)

    # Fetch filtered data
    jobs = query.all()

    # Pass jobs to the template
    return render_template('job_reports.html', jobs=jobs)

# Route for User Reports with query parameters
@app.route('/user_reports', methods=['GET', 'POST'])
@login_required
@role_required('superadmin')
def user_reports():
    # Get query parameters from the form submission
    username = request.args.get('username')
    email = request.args.get('email')
    role = request.args.get('role')
    location = request.args.get('location')
    phone_number = request.args.get('phone_number')

    # Build the query
    query = User.query
    if username:
        query = query.filter(User.username.ilike(f'%{username}%'))
    if email:
        query = query.filter(User.email.ilike(f'%{email}%'))
    if role:
        query = query.filter(User.role.ilike(f'%{role}%'))
    if location:
        query = query.filter(User.location.ilike(f'%{location}%'))
    if phone_number:
        query = query.filter(User.phone_number.ilike(f'%{phone_number}%'))

    # Fetch filtered data
    users = query.all()

    # Pass the data to the template
    return render_template('user_reports.html', users=users)


# Route for main reports page
@app.route('/reports')
@login_required
@role_required('superadmin')
def reports():
    return render_template('reports.html')
       
# from sqlalchemy import func
# @app.route('/reports1')
# def reports1():
#     return render_template('reports1.html')

# @app.route('/reports_table')
# def reports_table():
#     return render_template('reports_table.html')

@app.route('/feedback_report')
@login_required
@role_required('superadmin')
def feedback_report():
    feedbacks = Feedback.query.all()
    return render_template('feedback_report.html', feedbacks=feedbacks)


@reports_bp.before_request
def reports_blueprint_access_guard():
    if 'username' not in session:
        flash('Please sign in to continue.', 'warning')
        return redirect(url_for('signin'))
    if session.get('role') != 'superadmin':
        abort(403)


@reports_bp.route('/total_jobs_per_company', methods=['GET'])
def total_jobs_per_company():
    start_date = request.args.get('start_date', '2025-01-01')  # Default to earliest date
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))
    
    results = db.session.query(Job.company, db.func.count(JobApplication.id)) \
        .join(JobApplication, Job.id == JobApplication.job_id) \
        .filter(JobApplication.applied_at.between(start_date, end_date)) \
        .group_by(Job.company) \
        .order_by(db.func.count(JobApplication.id).desc()) \
        .all()
    
    data = [{"company": r[0], "total_jobs": r[1]} for r in results]
    
    # Extract data for graph
    companies = [item['company'] for item in data]
    job_counts = [item['total_jobs'] for item in data]
    
    return render_template("total_jobs_per_company.html", data=data, start_date=start_date, end_date=end_date, companies=companies, job_counts=job_counts)
#done till here


@reports_bp.route('/total_jobs_per_location', methods=['GET'])
def total_jobs_per_location():
    start_date = request.args.get('start_date', '2025-01-01')  # Default to earliest date
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))
    
    # Query to get job count per location within the date range
    results = db.session.query(Job.location, db.func.count(JobApplication.id)) \
        .join(JobApplication, Job.id == JobApplication.job_id) \
        .filter(JobApplication.applied_at.between(start_date, end_date)) \
        .group_by(Job.location) \
        .order_by(db.func.count(JobApplication.id).desc()) \
        .all()

    # Prepare data for the table and graph
    data = [{"location": r[0], "total_jobs": r[1]} for r in results]

    # Check if data is available
    if data:
        locations = [item['location'] for item in data]
        job_counts = [item['total_jobs'] for item in data]
    else:
        locations = []
        job_counts = []

    return render_template("total_jobs_per_location.html", data=data, start_date=start_date, end_date=end_date, locations=locations, job_counts=job_counts)
#done till here


@reports_bp.route('/total_jobs_per_type', methods=['GET'])
def total_jobs_per_type():
    start_date = request.args.get('start_date', '2025-01-01')  # Default to earliest date
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))
    
    # Query to get job count per job type within the date range
    results = db.session.query(Job.job_type, db.func.count(JobApplication.id)) \
        .join(JobApplication, Job.id == JobApplication.job_id) \
        .filter(JobApplication.applied_at.between(start_date, end_date)) \
        .group_by(Job.job_type) \
        .order_by(db.func.count(JobApplication.id).desc()) \
        .all()

    # Prepare data for the table and graph
    data = [{"job_type": r[0], "total_jobs": r[1]} for r in results]

    # Check if data is available
    if data:
        job_types = [item['job_type'] for item in data]
        job_counts = [item['total_jobs'] for item in data]
    else:
        job_types = []
        job_counts = []


    return render_template("total_jobs_per_type.html", data=data, start_date=start_date, end_date=end_date, job_types=job_types, job_counts=job_counts)
#done till here


@reports_bp.route('/total_jobs_per_status', methods=['GET'])
def total_jobs_per_status():
    start_date = request.args.get('start_date', '2025-01-01')  # Default to earliest date
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))
    
    # Query to get job count per job status within the date range
    results = db.session.query(Job.job_status, db.func.count(JobApplication.id)) \
        .join(JobApplication, Job.id == JobApplication.job_id) \
        .filter(JobApplication.applied_at.between(start_date, end_date)) \
        .group_by(Job.job_status) \
        .order_by(db.func.count(JobApplication.id).desc()) \
        .all()

    # Prepare data for the table and graph
    data = [{"job_status": r[0], "total_jobs": r[1]} for r in results]

    # Check if data is available
    if data:
        job_statuses = [item['job_status'] for item in data]
        job_counts = [item['total_jobs'] for item in data]
    else:
        job_statuses = []
        job_counts = []

    return render_template("total_jobs_per_status.html", data=data, start_date=start_date, end_date=end_date, job_statuses=job_statuses, job_counts=job_counts)
#done till here


@reports_bp.route('/total_applications_per_job', methods=['GET'])
def total_applications_per_job():
    start_date = request.args.get('start_date', '2025-01-01')  # Default to earliest date
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))

    # Query to get the count of applications per job title
    results = db.session.query(Job.title, db.func.count(JobApplication.id)) \
        .join(JobApplication, Job.id == JobApplication.job_id) \
        .filter(JobApplication.applied_at.between(start_date, end_date)) \
        .group_by(Job.title) \
        .order_by(db.func.count(JobApplication.id).desc()) \
        .all()

    # Prepare data for the table and graph
    data = [{"job_title": r[0], "total_applications": r[1]} for r in results]

    # Extract lists for chart rendering
    job_titles = [item['job_title'] for item in data]
    application_counts = [item['total_applications'] for item in data]

    return render_template("total_applications_per_job.html", data=data, start_date=start_date, end_date=end_date, job_titles=job_titles, application_counts=application_counts)
#done till here


@reports_bp.route('/total_applications_per_user', methods=['GET'])
def total_applications_per_user():
    start_date = request.args.get('start_date', '2025-01-01')  # Default start date
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))  # Default end date

    # Query to count applications per user
    results = db.session.query(User.username, db.func.count(JobApplication.id)) \
        .join(JobApplication, User.id == JobApplication.user_id) \
        .filter(JobApplication.applied_at.between(start_date, end_date)) \
        .group_by(User.username) \
        .order_by(db.func.count(JobApplication.id).desc()) \
        .all()

    # Convert query result to list of dictionaries
    data = [{"username": r[0], "total_applications": r[1]} for r in results]

    # Extract usernames and application counts for chart
    usernames = [item['username'] for item in data]
    application_counts = [item['total_applications'] for item in data]

    return render_template("total_applications_per_user.html", data=data, start_date=start_date, end_date=end_date, usernames=usernames, application_counts=application_counts)


 # Route for Total Jobs Posted in a Given Date Range
@reports_bp.route('/total_jobs_posted', methods=['GET'])
def total_jobs_posted():
    # Get date range parameters from request (default: all-time)
    start_date = request.args.get('start_date', '2024-01-01')
    end_date = request.args.get('end_date', datetime.today().strftime('%Y-%m-%d'))

    # Convert string to datetime for query filtering
    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d')
        end_date = datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        return "Invalid date format. Please use YYYY-MM-DD.", 400

    # Debugging: Print date range to console
    print(f"Fetching jobs between {start_date} and {end_date}")
        
    jobs = db.session.query(Job.id, Job.title, Job.company, Job.created_at, User.username) \
    .outerjoin(User, Job.recruiter_id == User.id) \
    .filter(Job.created_at >= start_date, Job.created_at <= end_date) \
    .order_by(Job.created_at.desc()) \
    .all()

    # Debugging: Print fetched jobs
    print(f"Found {len(jobs)} jobs")

    # Convert data to JSON format for rendering
    job_data = [
        {
            "job_id": job[0],
            "job_title": job[1],
            "company_name": job[2],  # Assuming 'company' is a string field in Job
            "recruiter": job[4],  # Fetching recruiter username
            "date_posted": job[3].strftime('%Y-%m-%d')
        }
        for job in jobs
    ]

    return render_template(
        "total_jobs_posted.html",
        jobs=job_data,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d')
    )

# Register Blueprint with Prefix
app.register_blueprint(reports_bp, url_prefix="/reports")

AUTO_CREATE_TABLES = os.environ.get('AUTO_CREATE_TABLES', 'true').lower() == 'true'
if AUTO_CREATE_TABLES:
    with app.app_context():
        db.create_all()


@app.route('/manage_applicants/<int:job_id>')
@login_required
@role_required('recruiter')
def manage_applicants(job_id):
    user = get_current_user()
    job = Job.query.get_or_404(job_id)
    if job.recruiter_id != user.id:
        abort(403)
    
    status_filter = request.args.get('status', 'all')
    query = JobApplication.query.filter_by(job_id=job_id)
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    applications = query.order_by(JobApplication.applied_at.desc()).all()
    
    return render_template('applied_candidates.html', job=job, applications=applications, current_status=status_filter)

@app.route('/update_application_status/<int:app_id>', methods=['POST'])
@login_required
@role_required('recruiter')
def update_application_status(app_id):
    user = get_current_user()
    application = JobApplication.query.get_or_404(app_id)
    if application.job.recruiter_id != user.id:
        abort(403)
        
    new_status = request.form.get('status')
    notes = request.form.get('notes')
    
    if new_status:
        application.status = new_status
    if notes is not None:
        application.recruiter_notes = notes
        
    db.session.commit()
    
    # Notify user about status change
    create_notification(application.user_id, f"Your application for {application.job.title} has been updated to: {new_status}", job_id=application.job_id, notif_type='application', target_id=application.job_id)
    db.session.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'status': 'success', 'new_status': new_status})
        
    flash('Application status updated successfully.', 'success')
    return redirect(url_for('manage_applicants', job_id=application.job_id))

if __name__ == "__main__":
    app.run(
        host=os.environ.get('FLASK_RUN_HOST', '0.0.0.0'),
        port=int(os.environ.get('PORT', 5050)),
        debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    )
