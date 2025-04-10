# app.py
from flask import Flask, render_template, request, redirect, url_for, session, g, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secret_key'  # Секрет для сессий

# Настройка SQLite
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(BASE_DIR, 'data.db')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# --- МОДЕЛИ ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    # Связь "автор -> статьи"
    articles = db.relationship('Article', backref='author', lazy=True)

    # Подписки (следуем за другими пользователями)
    followed = db.relationship(
        'User',
        secondary='follow',
        primaryjoin='User.id==Follow.follower_id',
        secondaryjoin='User.id==Follow.followed_id',
        backref='followers'
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    views = db.Column(db.Integer, default=0)
    topic = db.Column(db.String(100), nullable=False, default='Общее')  # Можно не использовать, но не мешает
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    likes = db.relationship('Like', backref='article', cascade='all, delete-orphan', lazy=True)
    comments = db.relationship('Comment', backref='article', cascade='all, delete-orphan', lazy=True)


class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


# Храним статус прочтения статьи подписчиком (для уведомлений)
class ArticleRead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    status = db.Column(db.String(10), default='unread')  # 'unread' / 'read'


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    user = db.relationship('User', backref='comments')


with app.app_context():
    db.create_all()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


def login_required(f):
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


def admin_required(f):
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


@app.before_request
def before_request():
    g.user = current_user()
    if g.user:
        g.unread_count = ArticleRead.query.filter_by(user_id=g.user.id, status='unread').count()
    else:
        g.unread_count = 0


# --- МАРШРУТЫ ---

@app.route('/')
def index():
    articles = Article.query.order_by(Article.id.desc()).all()
    return render_template('index.html', articles=articles)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        if not username or not password:
            return "Заполните все поля"
        if User.query.filter_by(username=username).first():
            return "Пользователь с таким именем уже существует."
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        session['user_id'] = new_user.id
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            return redirect(url_for('index'))
        else:
            return "Неправильные имя пользователя или пароль."
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))


@app.route('/create_article', methods=['GET', 'POST'])
@login_required
def create_article():
    if request.method == 'POST':
        title = request.form['title'].strip()
        content = request.form['content'].strip()
        topic = request.form.get('topic', 'Общее').strip()
        if not title or not content:
            return "Заполните заголовок и содержание."
        user = current_user()
        new_article = Article(title=title, content=content, topic=topic, author=user)
        db.session.add(new_article)
        db.session.commit()
        # Создаем уведомления для всех подписчиков автора
        for follower in user.followers:
            article_read = ArticleRead(user_id=follower.id, article_id=new_article.id, status='unread')
            db.session.add(article_read)
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('article.html', create_mode=True)


@app.route('/article/<int:article_id>')
def article_page(article_id):
    article = Article.query.get_or_404(article_id)
    article.views += 1
    db.session.commit()
    user = current_user()
    if user:
        record = ArticleRead.query.filter_by(user_id=user.id, article_id=article.id, status='unread').first()
        if record:
            record.status = 'read'
            db.session.commit()
    liked = None
    if user:
        liked = Like.query.filter_by(user_id=user.id, article_id=article.id).first()
    comments = Comment.query.filter_by(article_id=article.id).order_by(Comment.id.desc()).all()
    return render_template('article.html', article=article, liked=liked, comments=comments)


@app.route('/article/<int:article_id>/comment', methods=['POST'])
@login_required
def add_comment(article_id):
    article = Article.query.get_or_404(article_id)
    content = request.form['content'].strip()
    if not content:
        return "Комментарий не может быть пустым."
    new_comment = Comment(content=content, user_id=g.user.id, article_id=article.id)
    db.session.add(new_comment)
    db.session.commit()
    return redirect(url_for('article_page', article_id=article.id))


@app.route('/like/<int:article_id>', methods=['POST'])
@login_required
def like_article(article_id):
    user = current_user()
    article = Article.query.get_or_404(article_id)
    existing_like = Like.query.filter_by(user_id=user.id, article_id=article.id).first()
    if existing_like:
        db.session.delete(existing_like)
    else:
        new_like = Like(user_id=user.id, article_id=article.id)
        db.session.add(new_like)
    db.session.commit()
    return redirect(url_for('article_page', article_id=article.id))


@app.route('/follow/<int:user_id>', methods=['POST'])
@login_required
def follow_user(user_id):
    follower = current_user()
    followed = User.query.get_or_404(user_id)
    if follower.id == followed.id:
        return "Нельзя подписаться на самого себя."
    follow_record = Follow.query.filter_by(follower_id=follower.id, followed_id=followed.id).first()
    if follow_record:
        db.session.delete(follow_record)
    else:
        new_follow = Follow(follower_id=follower.id, followed_id=followed.id)
        db.session.add(new_follow)
    db.session.commit()
    return redirect(url_for('profile', user_id=followed.id))


@app.route('/notifications')
@login_required
def notifications():
    user = current_user()
    unread_records = ArticleRead.query.filter_by(user_id=user.id, status='unread').all()
    articles = [Article.query.get(rec.article_id) for rec in unread_records if Article.query.get(rec.article_id)]
    articles.sort(key=lambda a: a.created_at, reverse=True)
    return render_template('notifications.html', articles=articles)


@app.route('/topic/<string:topic_name>')
def articles_by_topic(topic_name):
    # Если вы не используете эту страницу, можно убрать
    articles = Article.query.filter_by(topic=topic_name).order_by(Article.id.desc()).all()
    return render_template('topic_list.html', topic=topic_name, articles=articles)


@app.route('/profile/<int:user_id>')
def profile(user_id):
    user_obj = User.query.get_or_404(user_id)
    follower = current_user()
    follow_record = None
    if follower:
        follow_record = Follow.query.filter_by(follower_id=follower.id, followed_id=user_obj.id).first()
    return render_template('profile.html', user_obj=user_obj, follow_record=follow_record)


@app.route('/admin')
@admin_required
def admin_panel():
    all_articles = Article.query.all()
    return render_template('admin.html', articles=all_articles)


@app.route('/admin/delete_article/<int:article_id>', methods=['POST'])
@admin_required
def admin_delete_article(article_id):
    article = Article.query.get_or_404(article_id)
    db.session.delete(article)
    db.session.commit()
    return redirect(url_for('admin_panel'))


def generate_article(topic):
    """Фиктивная функция генерации текста (нейронная сеть).
       В реальном проекте здесь можно вызывать GPT-2/GPT-3."""
    return f"Это автоматически сгенерированная статья по теме {topic}. Тестовый контент."


if __name__ == '__main__':
    app.run(debug=True)
