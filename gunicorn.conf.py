"""
Gunicorn Configuration for Linux Central Server / Linux Counter Deployment.
NOTE: Gunicorn does not run on Windows. On Windows counter machines, use waitress:
      waitress-serve --port=5000 wsgi:app
"""
import multiprocessing

bind = "127.0.0.1:5000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 120
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"

# Security: do not forward raw tracebacks in production
capture_output = True
preload_app = True
