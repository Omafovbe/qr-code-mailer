"""
Gunicorn configuration file for Flask application.
Usage: gunicorn -c gunicorn_config.py wsgi:app
"""

import multiprocessing

# Server socket
bind = "0.0.0.0:8090"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "qrcodemail"

# Server mechanics
daemon = False
pidfile = None
max_requests = 0
max_requests_jitter = 0

# SSL (uncomment and update paths if needed for HTTPS)
# keyfile = "/path/to/keyfile"
# certfile = "/path/to/certfile"
# ssl_version = "TLSv1_2"
