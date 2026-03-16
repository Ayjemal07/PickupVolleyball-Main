from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv
import os
from app.models import db, login_manager

from flask_mail import Mail

mail = Mail()

# Load environment variables
load_dotenv()

# Initialize the database
migrate = Migrate()

def create_app():
    app = Flask(__name__)

    # Configuration
    app.config.from_object('config.Config')
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(24).hex())
    app.config['UPLOAD_FOLDER'] = 'static/images'



    # Set pool_recycle to prevent connection timeout issues
    app.config['SQLALCHEMY_POOL_RECYCLE'] = 7200  # Recycle connections every 1 hour

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    # # Register Blueprints
    from .views import main
    from .authentication.auth_routes import auth

    app.register_blueprint(auth)
    app.register_blueprint(main)  # Register last to avoid route conflicts
    mail.init_app(app)


    return app
