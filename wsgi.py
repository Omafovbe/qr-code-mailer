"""
WSGI entry point for Gunicorn.
Run with: gunicorn -c gunicorn_config.py wsgi:app
"""
from app import app

if __name__ == "__main__":
    app.run()
