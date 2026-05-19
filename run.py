"""
Production WSGI server entry point for Windows.
Uses Waitress (pure-Python, Windows-compatible) on port 8090.

Run with:
    qrmail\Scripts\python run.py

For Linux/Mac deployments, use gunicorn instead:
    gunicorn -c gunicorn_config.py wsgi:app
"""
from waitress import serve
from app import app

if __name__ == "__main__":
    print("Starting Waitress server on http://127.0.0.1:8090")
    serve(app, host="127.0.0.1", port=8090)
