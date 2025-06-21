import os
import json
import uuid
import google.generativeai as genai
from flask import Flask, request, render_template, jsonify, url_for, flash, redirect, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import datetime

# --- Initialization ---
load_dotenv()
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "a-strong-default-secret-key-for-dev")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coursewell.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    courses = db.relationship('Course', backref='creator', lazy=True, cascade="all, delete-orphan")
    enrollments = db.relationship('Enrollment', back_populates='user', lazy='dynamic', cascade="all, delete-orphan")
    reviews = db.relationship('Review', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_enrolled(self, course):
        return self.enrollments.filter_by(course_id=course.id).count() > 0

class Course(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(150), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    enrollees = db.relationship('Enrollment', back_populates='course', lazy='dynamic', cascade="all, delete-orphan")
    lessons = db.relationship('Lesson', backref='course', lazy=True, cascade="all, delete-orphan", order_by="Lesson.chapter_number")
    description = db.Column(db.Text, nullable=True)
    thumbnail_url = db.Column(db.String(255), nullable=True)
    reviews = db.relationship('Review', backref='course', lazy='dynamic')
    shareable_link_id = db.Column(db.String(36), unique=True, nullable=True)

    @property
    def average_rating(self):
        reviews = self.reviews.all()
        if not reviews: return 0
        return sum(r.rating for r in reviews) / len(reviews)

class Lesson(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(150), nullable=False)
    raw_script = db.Column(db.Text, nullable=False)
    parsed_json = db.Column(db.Text, nullable=False)
    course_id = db.Column(db.String(36), db.ForeignKey('course.id'), nullable=False)
    chapter_number = db.Column(db.Integer, nullable=False)

class Enrollment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_id = db.Column(db.String(36), db.ForeignKey('course.id'), nullable=False)
    last_completed_chapter_number = db.Column(db.Integer, default=0, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True, default=None)
    user = db.relationship('User', back_populates='enrollments')
    course = db.relationship('Course', back_populates='enrollees')
    __table_args__ = (db.UniqueConstraint('user_id', 'course_id', name='_user_course_uc'),)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    course_id = db.Column(db.String(36), db.ForeignKey('course.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'course_id', name='_user_course_review_uc'),)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Prompts ---
PARSER_PROMPT = """
You are a precise curriculum parsing agent. Your task is to convert a teacher's lesson script into a structured JSON object. You MUST follow these rules exactly.
1. The final JSON object MUST have a single top-level key: "steps".
2. For explanatory text, create a "CONTENT" step with a "text" key.
3. For image tags like [IMAGE: alt="A picture."], create a "MEDIA" step with an "alt_text" key. **Do NOT include a filename or URL.**
4. For multiple-choice questions like [QUESTION: ... OPTIONS: A)... ANSWER: B], create a "QUESTION_MCQ" step with "question", "options" (as a key-value object), and "correct_answer" keys.
5. For short-answer questions like [QUESTION_SA: ... KEYWORDS: word1, word2, ...], create a "QUESTION_SA" step with "question" and "keywords" (as an array of strings) keys.
Parse the following script:
"""
GRADER_PROMPT = """
You are an impartial grading assistant. Your task is to determine if a student's answer contains a set of key concepts. Your response MUST be a single word: "CORRECT" or "INCORRECT".
Required Keywords: {}
Student's Answer: {}
"""
TUTOR_PROMPT_TEMPLATE = {
    "CONTENT": "Your role is to rephrase the following text from a lesson plan into a natural, conversational format for a student. Stick ONLY to the information in the text. End the turn naturally. Here is the text: --- {} ---",
    "MEDIA": "An image with the description '{}' has just been shown. Briefly call the student's attention to it and transition to the next piece of information.",
    "RETRY": "You are a Socratic tutor. The student answered incorrectly. The lesson text with the answer is: --- {} ---. Based ONLY on this text, provide a short, simple hint or a leading question to help them. Do not invent new analogies.",
    "FEEDBACK_AND_PROCEED": "The student just answered a question correctly. Your response should start with a positive confirmation (like 'Exactly!' or 'Great job!') and then seamlessly transition into teaching the following new concept. Here is the new concept: --- {} ---",
    "QNA": """
You are an intelligent teaching assistant. A student has a question.
Your knowledge is strictly limited to the following "Full Lesson Script".
The script contains text and image tags like [IMAGE: alt="description"].

Your Task:
1. Read the student's question.
2. Analyze the "Full Lesson Script" to find the answer.
3. **If the student is asking to see an image mentioned in the script (e.g., "show me the capybara", "can I see the image?"), your response MUST be ONLY the following machine-readable tag: `[RETRIEVE_IMAGE: "description"]`, where "description" is the exact alt text from the corresponding [IMAGE] tag in the script.**
4. For any other question, answer it normally based on the script's text content. If you cannot answer, say so politely.

---
Full Lesson Script:
{lesson_script}
---
Student's Question: {user_question}
""",
    "QUESTION": "Okay, time for a quick question to check your understanding: {}"
}

# --- Helper Functions ---
def parse_lesson_script(script_text):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        response = model.generate_content(PARSER_PROMPT + script_text)
        cleaned_response = response.text.strip().replace("```json", "").replace("```", "").strip()
        parsed_json = json.loads(cleaned_response)
        for step in parsed_json.get('steps', []):
            if step.get('type') == 'QUESTION_SA' and 'keywords' in step:
                kw = step['keywords']
                if isinstance(kw, str): step['keywords'] = [k.strip() for k in kw.split(',')]
                step['keywords'] = [str(k) for k in step['keywords']]
        return parsed_json if isinstance(parsed_json, dict) and 'steps' in parsed_json else None
    except Exception as e:
        print(f"Error during parsing: {e}")
        return None

def get_tutor_response(full_prompt):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        response = model.generate_content(full_prompt)
        return response.text if response.text else "Let's try that another way."
    except Exception as e:
        print(f"Error getting tutor response: {e}")
        return "I seem to be having a little trouble thinking. Could you try again?"

# --- Auth Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'warning')
            return redirect(url_for('register'))
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('login'))
        login_user(user, remember=True)
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# --- App Routes ---
@app.route('/')
def index():
    return redirect(url_for('explore'))

@app.route('/dashboard')
@login_required
def dashboard():
    enrollments = Enrollment.query.filter_by(user_id=current_user.id).join(Course).order_by(Course.title).all()
    return render_template('dashboard.html', enrollments=enrollments)

@app.route('/creator')
@login_required
def creator_dashboard():
    created_courses = Course.query.filter_by(user_id=current_user.id).order_by(Course.title).all()
    return render_template('creator_dashboard.html', created_courses=created_courses)

# --- Course & Chapter Management ---
@app.route('/create_course', methods=['POST'])
@login_required
def create_course():
    title = request.form.get('title')
    if not title:
        flash('A title is required to create a course.', 'warning')
        return redirect(url_for('creator_dashboard'))
    new_course = Course(title=title, user_id=current_user.id)
    db.session.add(new_course)
    db.session.commit()
    flash('Course created! You can now manage its chapters.', 'success')
    return redirect(url_for('manage_course', course_id=new_course.id))

@app.route('/course/<string:course_id>/manage')
@login_required
def manage_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    return render_template('manage_course.html', course=course)

@app.route('/course/<string:course_id>/publish', methods=['POST'])
@login_required
def toggle_publish_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    if not course.lessons:
        flash('You must add at least one chapter to publish a course.', 'warning')
        return redirect(url_for('manage_course', course_id=course.id))
    course.is_published = not course.is_published
    db.session.commit()
    flash(f"'{course.title}' is now {'published' if course.is_published else 'a private draft'}.", 'success' if course.is_published else 'info')
    return redirect(url_for('manage_course', course_id=course.id))

@app.route('/course/<string:course_id>/add_chapter', methods=['GET'])
@login_required
def add_chapter_page(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    return render_template('create_chapter.html', course=course)

@app.route('/course/<string:course_id>/save_chapter', methods=['POST'])
@login_required
def save_chapter(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    
    script = request.form['script']
    title = request.form['title']
    if not title or not script:
        flash('Both a title and script are required.', 'warning')
        return redirect(url_for('add_chapter_page', course_id=course.id))
    
    files = request.files.getlist('media')
    media_urls = []
    for uploaded_file in files:
        if uploaded_file.filename != '':
            filename = str(uuid.uuid4()) + os.path.splitext(uploaded_file.filename)[1]
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)
            media_urls.append(url_for('static', filename=f'uploads/{filename}'))
    
    parsed_data = parse_lesson_script(script)
    if not parsed_data:
        flash('The AI could not understand the lesson structure. Please check your tags and try again.', 'danger')
        return redirect(url_for('add_chapter_page', course_id=course.id))

    media_url_iterator = iter(media_urls)
    for step in parsed_data.get('steps', []):
        if step.get('type') == 'MEDIA':
            try: step['media_url'] = next(media_url_iterator)
            except StopIteration: break

    last_chapter = Lesson.query.filter_by(course_id=course.id).order_by(Lesson.chapter_number.desc()).first()
    new_chapter_number = (last_chapter.chapter_number + 1) if last_chapter else 1

    new_lesson = Lesson(title=title, raw_script=script, parsed_json=json.dumps(parsed_data), course_id=course.id, chapter_number=new_chapter_number)
    db.session.add(new_lesson)
    db.session.commit()
    
    flash('Chapter added successfully!', 'success')
    return redirect(url_for('manage_course', course_id=course.id))

@app.route('/edit_chapter/<string:lesson_id>', methods=['GET'])
@login_required
def edit_chapter_page(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    if lesson.course.creator.id != current_user.id: abort(403)
    return render_template('edit_chapter.html', lesson=lesson)

@app.route('/update_chapter/<string:lesson_id>', methods=['POST'])
@login_required
def update_chapter(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    if lesson.course.creator.id != current_user.id: abort(403)
    
    lesson.title = request.form['title']
    lesson.raw_script = request.form['script']
    
    files = request.files.getlist('media')
    media_urls = []
    for uploaded_file in files:
        if uploaded_file.filename != '':
            filename = str(uuid.uuid4()) + os.path.splitext(uploaded_file.filename)[1]
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)
            media_urls.append(url_for('static', filename=f'uploads/{filename}'))
    
    parsed_data = parse_lesson_script(lesson.raw_script)
    if not parsed_data:
        flash('The AI could not understand the lesson structure.', 'danger')
        return redirect(url_for('edit_chapter_page', lesson_id=lesson.id))
        
    media_url_iterator = iter(media_urls)
    for step in parsed_data.get('steps', []):
        if step.get('type') == 'MEDIA':
            try: step['media_url'] = next(media_url_iterator)
            except StopIteration: break
                
    lesson.parsed_json = json.dumps(parsed_data)
    db.session.commit()
    
    flash('Chapter updated successfully!', 'success')
    return redirect(url_for('manage_course', course_id=lesson.course_id))

@app.route('/delete_chapter/<string:lesson_id>', methods=['POST'])
@login_required
def delete_chapter(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    if lesson.course.creator.id != current_user.id: abort(403)
    course_id = lesson.course_id
    deleted_chapter_number = lesson.chapter_number
    db.session.delete(lesson)
    subsequent_chapters = Lesson.query.filter(Lesson.course_id == course_id, Lesson.chapter_number > deleted_chapter_number).order_by(Lesson.chapter_number).all()
    for chapter in subsequent_chapters: chapter.chapter_number -= 1
    db.session.commit()
    flash('Chapter deleted successfully.', 'success')
    return redirect(url_for('manage_course', course_id=course_id))

@app.route('/course/<string:course_id>/reorder_chapters', methods=['POST'])
@login_required
def reorder_chapters(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    ordered_ids = request.json.get('order', [])
    for index, chapter_id in enumerate(ordered_ids):
        lesson = Lesson.query.get(chapter_id)
        if lesson and lesson.course_id == course.id:
            lesson.chapter_number = index + 1
    db.session.commit()
    return jsonify({'success': True, 'message': 'Chapter order updated successfully.'})

# --- Student-Facing Routes ---
@app.route('/explore')
def explore():
    courses = Course.query.filter_by(is_published=True).order_by(Course.title).all()
    return render_template('explore.html', courses=courses)

@app.route('/course/<string:course_id>')
def course_player(course_id):
    course = Course.query.get_or_404(course_id)
    if not course.is_published and (not current_user.is_authenticated or course.creator.id != current_user.id): abort(404)
    if not course.lessons:
        if current_user.is_authenticated and current_user.id == course.user_id:
            flash('This course has no chapters yet. Add one to enable the preview.', 'info')
            return redirect(url_for('manage_course', course_id=course.id))
        return "This course has no content yet.", 404
    
    chapter_to_start = 1
    if current_user.is_authenticated:
        enrollment = Enrollment.query.filter_by(user_id=current_user.id, course_id=course.id).first()
        if enrollment:
            chapter_to_start = enrollment.last_completed_chapter_number + 1
            if chapter_to_start > len(course.lessons): chapter_to_start = len(course.lessons)
            
    return redirect(url_for('student_chapter_view', course_id=course.id, chapter_number=chapter_to_start))

@app.route('/course/<string:course_id>/<int:chapter_number>')
def student_chapter_view(course_id, chapter_number):
    course = Course.query.get_or_404(course_id)
    lesson = None
    if 1 <= chapter_number <= len(course.lessons):
        lesson = course.lessons[chapter_number - 1]
    if not lesson: abort(404)
    
    enrollment = None
    if current_user.is_authenticated:
        enrollment = Enrollment.query.filter_by(user_id=current_user.id, course_id=course.id).first()
        
    return render_template('course_player.html', course=course, current_lesson=lesson, enrollment=enrollment)

# --- CHAT ROUTE (Definitive, Fixed Version) ---
@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    lesson_id = data['lesson_id']
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson_data = json.loads(lesson.parsed_json)
    
    step_index = data.get('step_index', 0)
    user_input = data.get('user_input')
    request_type = data.get('request_type', 'LESSON_FLOW')
    
    response_data = {}

    if request_type == 'QNA':
        qna_prompt = TUTOR_PROMPT_TEMPLATE['QNA'].format(
            lesson_script=lesson.raw_script,
            user_question=user_input
        )
        ai_response = get_tutor_response(qna_prompt)

        if ai_response.strip().startswith('[RETRIEVE_IMAGE:'):
            try:
                alt_text_to_find = ai_response.split('"')[1]
                found_url = None
                for step in lesson_data.get('steps', []):
                    if step.get('type') == 'MEDIA' and step.get('alt_text') == alt_text_to_find:
                        found_url = step.get('media_url')
                        break
                
                if found_url:
                    response_data['tutor_text'] = f"Of course, here is the image of '{alt_text_to_find}':"
                    response_data['media_url'] = found_url
                else:
                    response_data['tutor_text'] = "I found a mention of that image, but I couldn't retrieve the picture. Sorry about that."
            except IndexError:
                 response_data['tutor_text'] = "I had a little trouble retrieving that image. Please try asking in a different way."
        else:
            response_data['tutor_text'] = ai_response

        response_data['is_qna_response'] = True
        response_data['next_step'] = step_index
        return jsonify(response_data)
        
    if step_index > 0:
        prev_step_index = step_index - 1
        if prev_step_index < len(lesson_data['steps']):
            prev_step = lesson_data['steps'][prev_step_index]
            if prev_step.get('type') in ['QUESTION_MCQ', 'QUESTION_SA']:
                is_correct = False
                if prev_step.get('type') == 'QUESTION_MCQ':
                    if user_input and user_input.strip().upper() == prev_step.get('correct_answer', '').strip().upper():
                        is_correct = True
                elif prev_step.get('type') == 'QUESTION_SA':
                    keywords = prev_step.get('keywords', [])
                    grader_prompt = GRADER_PROMPT.format(", ".join(keywords), user_input)
                    grader_response = get_tutor_response(grader_prompt)
                    if "CORRECT" in grader_response.upper():
                        is_correct = True
                
                if is_correct:
                    response_data['feedback'] = "Correct! Great job."
                else: 
                    relevant_content = "\n".join([s.get('text', '') for i, s in enumerate(lesson_data['steps']) if i < prev_step_index and s.get('type') == 'CONTENT']) or "Let's review."
                    retry_prompt = TUTOR_PROMPT_TEMPLATE['RETRY'].format(relevant_content)
                    response_data['tutor_text'] = get_tutor_response(retry_prompt)
                    response_data['next_step'] = prev_step_index 
                    return jsonify(response_data)

    if step_index >= len(lesson_data['steps']):
        response_data['is_lesson_end'] = True
        response_data['tutor_text'] = "Congratulations! You have completed this chapter."
        if current_user.is_authenticated:
            enrollment = Enrollment.query.filter_by(user_id=current_user.id, course_id=lesson.course_id).first()
            if enrollment and enrollment.last_completed_chapter_number < lesson.chapter_number:
                enrollment.last_completed_chapter_number = lesson.chapter_number
                if enrollment.last_completed_chapter_number == len(lesson.course.lessons):
                    enrollment.completed_at = datetime.datetime.utcnow()
                    response_data['certificate_url'] = url_for('certificate_view', course_id=lesson.course_id)
                db.session.commit()
        
        next_chapter = Lesson.query.filter_by(course_id=lesson.course_id, chapter_number=lesson.chapter_number + 1).first()
        if next_chapter and not response_data.get('certificate_url'):
            response_data['next_chapter_url'] = url_for('student_chapter_view', course_id=lesson.course_id, chapter_number=next_chapter.chapter_number)
        
        response_data['next_step'] = step_index
        return jsonify(response_data)

    current_step = lesson_data['steps'][step_index]
    step_type = current_step.get('type')
        
    if step_type == 'CONTENT':
        prompt_template = TUTOR_PROMPT_TEMPLATE['FEEDBACK_AND_PROCEED'] if response_data.get('feedback') else TUTOR_PROMPT_TEMPLATE['CONTENT']
        full_prompt = prompt_template.format(current_step.get('text', ''))
        response_data['tutor_text'] = get_tutor_response(full_prompt)
    
    elif step_type == 'MEDIA':
        if not current_step.get('media_url'):
            print(f"Warning: MEDIA step at index {step_index} is missing a media_url. Auto-advancing.")
            response_data['next_step'] = step_index + 1
            # Return an empty object to signal the frontend to immediately call again for the next step.
            return jsonify(response_data)
            
        full_prompt = TUTOR_PROMPT_TEMPLATE['MEDIA'].format(current_step.get('alt_text', ''))
        response_data['tutor_text'] = get_tutor_response(full_prompt)
        response_data['media_url'] = current_step.get('media_url')
        
    elif step_type in ['QUESTION_MCQ', 'QUESTION_SA']:
        response_data['question'] = current_step
        question_text = current_step.get('question', 'a question')
        question_prompt = TUTOR_PROMPT_TEMPLATE['QUESTION'].format(question_text)
        response_data['tutor_text'] = get_tutor_response(question_prompt)
    
    response_data['next_step'] = step_index + 1
    return jsonify(response_data)

# --- Other Routes ---
# In app.py

@app.route('/course/<string:course_id>/enroll', methods=['POST'])
@login_required
def enroll_in_course(course_id):
    course = Course.query.get_or_404(course_id)
    share_id = request.form.get('share_id') # Get the share_id from the form

    # --- PERMISSION CHECK ---
    # Allow enrollment if:
    # 1. The course is published, OR
    # 2. The user is the creator, OR
    # 3. The user accessed via a valid share link.
    is_public = course.is_published
    is_creator = (current_user.is_authenticated and course.creator.id == current_user.id)
    has_share_link = (share_id is not None and share_id == course.shareable_link_id)

    if not (is_public or is_creator or has_share_link):
        abort(404) # If none of the access conditions are met, deny access.

    # --- DUPLICATION CHECK ---
    if is_creator:
        flash("You cannot enroll in a course you've created.", "warning")
        return redirect(url_for('course_detail_page', course_id=course.id))

    if current_user.is_enrolled(course):
        flash("You are already enrolled in this course.", "info")
        return redirect(url_for('course_player', course_id=course.id))
    
    # --- ENROLLMENT LOGIC ---
    new_enrollment = Enrollment(user=current_user, course=course)
    db.session.add(new_enrollment)
    db.session.commit()
    flash(f"You have successfully enrolled in '{course.title}'!", 'success')
    return redirect(url_for('course_player', course_id=course.id))

@app.route('/course/<string:course_id>/reviews')
def reviews_page(course_id):
    course = Course.query.get_or_404(course_id)
    reviews = course.reviews.order_by(Review.created_at.desc()).all()
    return render_template('reviews.html', course=course, reviews=reviews)

@app.route('/course/<string:course_id>/review', methods=['POST'])
@login_required
def submit_review(course_id):
    course = Course.query.get_or_404(course_id)
    enrollment = Enrollment.query.filter_by(user_id=current_user.id, course_id=course_id).first()
    if not enrollment or not enrollment.completed_at:
        flash("You must complete a course before reviewing it.", "warning")
        return redirect(url_for('explore'))
    if Review.query.filter_by(user_id=current_user.id, course_id=course_id).first():
        flash("You have already reviewed this course.", "warning")
        return redirect(url_for('reviews_page', course_id=course.id))
    rating = request.form.get('rating')
    comment = request.form.get('comment')
    if not rating:
        flash("A star rating is required.", "warning")
        return redirect(url_for('certificate_view', course_id=course.id))
    new_review = Review(rating=int(rating), comment=comment, course_id=course.id, user_id=current_user.id)
    db.session.add(new_review)
    db.session.commit()
    flash("Thank you for your feedback!", "success")
    return redirect(url_for('reviews_page', course_id=course.id))

@app.route('/course/<string:course_id>/certificate')
@login_required
def certificate_view(course_id):
    enrollment = Enrollment.query.filter_by(user_id=current_user.id, course_id=course_id).first_or_404()
    if not enrollment.completed_at:
        flash("You have not completed this course yet.", "warning")
        return redirect(url_for('course_player', course_id=course.id))
    existing_review = Review.query.filter_by(user_id=current_user.id, course_id=course.id).first()
    return render_template('certificate.html', enrollment=enrollment, existing_review=existing_review)

# In app.py

@app.route('/course/<string:course_id>/update_details', methods=['POST'])
@login_required
def update_course_details(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id:
        abort(403)
        
    course.description = request.form.get('description')
    
    if 'thumbnail' in request.files:
        file = request.files['thumbnail']
        if file.filename != '':
            filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            course.thumbnail_url = url_for('static', filename=f'uploads/{filename}')

    db.session.commit()
    flash('Course details updated successfully!', 'success')
    return redirect(url_for('manage_course', course_id=course.id))

# In app.py

@app.route('/course/<string:course_id>/details')
def course_detail_page(course_id):
    course = Course.query.get_or_404(course_id)
    # Check if the course is private and if the user has access
    if not course.is_published and (not current_user.is_authenticated or course.creator.id != current_user.id):
        # We will enhance this later for private links
        abort(404)
    return render_template('course_detail.html', course=course)

# In app.py

@app.route('/course/<string:course_id>/update_publish_status', methods=['POST'])
@login_required
def update_publish_status(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    
    status = request.form.get('publish_status')
    course.is_published = (status == 'public')
    db.session.commit()
    
    flash('Publishing status updated!', 'success')
    return redirect(url_for('manage_course', course_id=course.id))

@app.route('/course/<string:course_id>/generate_link', methods=['POST'])
@login_required
def generate_share_link(course_id):
    course = Course.query.get_or_404(course_id)
    if course.creator.id != current_user.id: abort(403)
    
    if not course.shareable_link_id:
        course.shareable_link_id = str(uuid.uuid4())
        db.session.commit()
        
    return redirect(url_for('manage_course', course_id=course.id))

@app.route('/share/<string:link_id>')
def shared_course_view(link_id):
    course = Course.query.filter_by(shareable_link_id=link_id).first_or_404()
    # This link grants access to the detail page regardless of login status
    return render_template('course_detail.html', course=course)


if __name__ == '__main__':
    app.run(debug=True)