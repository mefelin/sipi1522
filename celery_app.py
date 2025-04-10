# celery_app.py
from celery import Celery
from datetime import datetime
from app import app, db, User, Article, ArticleRead, generate_article


def make_celery(flask_app):
    celery = Celery(
        flask_app.import_name,
        backend='redis://localhost:6379/0',  # Убедитесь, что Redis запущен
        broker='redis://localhost:6379/0'
    )
    celery.conf.update(flask_app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return super().__call__(*args, **kwargs)

    celery.Task = ContextTask
    return celery


# Создаём объект Celery на основе Flask-приложения
celery = make_celery(app)

# Настройка beat_schedule (пример: каждые 30 секунд)
celery.conf.beat_schedule = {
    'generate-article-every-30-seconds': {
        'task': 'generate_and_publish_article',
        'schedule': 30.0,
        'args': ('Новости',)  # именно кортеж (один аргумент)
    },
}


@celery.task(name="generate_and_publish_article")
def generate_and_publish_article(topic="Общее"):
    # Находим (или создаём) администратора
    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(username="system")
        admin.set_password("system")
        admin.is_admin = True
        db.session.add(admin)
        db.session.commit()

    content = generate_article(topic)
    title = f"Автоматическая статья по теме {topic} - {datetime.utcnow().strftime('%d.%m.%Y %H:%M')}"
    new_article = Article(title=title, content=content, topic=topic, author=admin)
    db.session.add(new_article)
    db.session.commit()

    # Создаём уведомления для подписчиков админа
    for follower in admin.followers:
        article_read = ArticleRead(user_id=follower.id, article_id=new_article.id, status='unread')
        db.session.add(article_read)
    db.session.commit()

    return f"Статья '{title}' создана."
