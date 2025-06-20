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

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Ensure necessary folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# --- Database Models (Restored to V1 Logic) ---
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
    reviews = db.relationship('Review', backref='course', lazy='dynamic')

    @property
    def average_rating(self):
        reviews = self.reviews.all()
        if not reviews:
            return 0
        return sum(r.rating for r in reviews) / len(reviews)

class Lesson(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(150), nullable=False)
    # RESTORED FIELDS FOR V1 CHAT LOGIC
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

# --- Prompts (Restored from V1) ---
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
You are a helpful teaching assistant. A student has interrupted the lesson to ask a question.
Using ONLY the provided lesson context below, answer the student's question.
If the answer is not in the context, politely state that you can only answer questions about the material covered so far.
Do not use any outside knowledge. Keep your answer concise.
Lesson Context: --- {} ---
Student's Question: --- {} ---
"""
}

# --- Helper Functions (Restored from V1) ---
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

def get_tutor_response(history, new_prompt):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        chat = model.start_chat(history=history)
        response = chat.send_message(new_prompt)
        return response.text if response.text else "Let's try that another way."
    except Exception as e:
        print(f"Error getting tutor response: {e}")
        return "I seem to be having a little trouble thinking. Could you try again?"

# --- Authentication Routes (Unchanged) ---
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

# --- Main Application Routes (Unchanged) ---
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

# --- Chapter and Course Management Routes (Adapted for V1 Data) ---
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
    # This now points to a simpler create_chapter page
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
        
    parsed_data = parse_lesson_script(script)
    if not parsed_data:
        flash('The AI could not understand the lesson structure. Please check your tags and try again.', 'danger')
        return redirect(url_for('add_chapter_page', course_id=course.id))

    last_chapter = Lesson.query.filter_by(course_id=course.id).order_by(Lesson.chapter_number.desc()).first()
    new_chapter_number = (last_chapter.chapter_number + 1) if last_chapter else 1

    new_lesson = Lesson(
        title=title,
        raw_script=script,
        parsed_json=json.dumps(parsed_data),
        course_id=course.id,
        chapter_number=new_chapter_number
    )
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
    
    parsed_data = parse_lesson_script(lesson.raw_script)
    if not parsed_data:
        flash('The AI could not understand the lesson structure. Please check your tags and try again.', 'danger')
        return redirect(url_for('edit_chapter_page', lesson_id=lesson.id))
        
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
    # Re-order subsequent chapters
    subsequent_chapters = Lesson.query.filter(
        Lesson.course_id == course_id,
        Lesson.chapter_number > deleted_chapter_number
    ).order_by(Lesson.chapter_number).all()
    for chapter in subsequent_chapters:
        chapter.chapter_number -= 1
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

# --- CHAT ROUTE (Restored from V1) ---
@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    lesson_id = data['lesson_id']
    lesson = Lesson.query.get_or_404(lesson_id)
    lesson_data = json.loads(lesson.parsed_json)
    
    step_index = data.get('step_index', 0)
    user_input = data.get('user_input')
    chat_history = data.get('chat_history', [])
    request_type = data.get('request_type', 'LESSON_FLOW')
    
    response_data = {}

    if request_type == 'QNA':
        context_list = [step.get('text', '') for i, step in enumerate(lesson_data['steps']) if i < step_index and step.get('type') == 'CONTENT']
        lesson_context = "\n".join(context_list) or "No context available yet."
        qna_prompt = TUTOR_PROMPT_TEMPLATE['QNA'].format(lesson_context, user_input)
        response_data['tutor_text'] = get_tutor_response(chat_history, qna_prompt)
        response_data['is_qna_response'] = True
        response_data['next_step'] = step_index
        return jsonify(response_data)
        
    if step_index > 0:
        prev_step_index = step_index - 1
        prev_step = lesson_data['steps'][prev_step_index]
        if prev_step.get('type') in ['QUESTION_MCQ', 'QUESTION_SA']:
            is_correct = False
            if prev_step.get('type') == 'QUESTION_MCQ':
                if user_input and user_input.strip().upper() == prev_step.get('correct_answer', '').strip().upper():
                    is_correct = True
            elif prev_step.get('type') == 'QUESTION_SA':
                keywords = prev_step.get('keywords', [])
                grader_prompt = GRADER_PROMPT.format(", ".join(keywords), user_input)
                grader_response = get_tutor_response([], grader_prompt)
                if "CORRECT" in grader_response.upper():
                    is_correct = True
            
            if is_correct:
                response_data['feedback'] = "Correct! Great job."
            else:
                relevant_content = "\n".join([s.get('text', '') for i, s in enumerate(lesson_data['steps']) if i < prev_step_index and s.get('type') == 'CONTENT']) or "Let's review."
                retry_prompt = TUTOR_PROMPT_TEMPLATE['RETRY'].format(relevant_content)
                response_data['tutor_text'] = get_tutor_response([], retry_prompt)
                response_data['next_step'] = prev_step_index 
                return jsonify(response_data)

    if step_index >= len(lesson_data['steps']):
        response_data['is_lesson_end'] = True
        response_data['tutor_text'] = "Congratulations! You have completed this chapter."
        # Update progress for enrolled user
        if current_user.is_authenticated:
            enrollment = Enrollment.query.filter_by(user_id=current_user.id, course_id=lesson.course_id).first()
            if enrollment and enrollment.last_completed_chapter_number < lesson.chapter_number:
                enrollment.last_completed_chapter_number = lesson.chapter_number
                # Check for course completion
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
        prompt_template = TUTOR_PROMPT_TEMPLATE['CONTENT']
        if response_data.get('feedback'):
             prompt_template = TUTOR_PROMPT_TEMPLATE['FEEDBACK_AND_PROCEED']
        response_data['tutor_text'] = get_tutor_response(chat_history, prompt_template.format(current_step.get('text', '')))
    
    elif step_type == 'MEDIA':
        if not current_step.get('alt_text') or not current_step.get('media_url'):
            response_data['next_step'] = step_index + 1
            return jsonify(response_data) 
        response_data['tutor_text'] = get_tutor_response(chat_history, TUTOR_PROMPT_TEMPLATE['MEDIA'].format(current_step.get('alt_text', '')))
        response_data['media_url'] = current_step.get('media_url')
        
    elif step_type in ['QUESTION_MCQ', 'QUESTION_SA']:
        response_data['question'] = current_step
    
    response_data['next_step'] = step_index + 1
    return jsonify(response_data)

# --- Enrollment, Review, and Certificate Routes ---
@app.route('/course/<string:course_id>/enroll', methods=['POST'])
@login_required
def enroll_in_course(course_id):
    course = Course.query.get_or_404(course_id)
    if not course.is_published and course.creator.id != current_user.id: abort(404)
    if course.creator.id == current_user.id:
        flash("You cannot enroll in a course you've created.", "warning")
        return redirect(url_for('explore'))
    if current_user.is_enrolled(course):
        flash("You are already enrolled in this course.", "info")
        return redirect(url_for('course_player', course_id=course.id))
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


if __name__ == '__main__':
    app.run(debug=True)
