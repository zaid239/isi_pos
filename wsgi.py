"""
WSGI Entrypoint.
Used by waitress (Windows on-premise counters) and gunicorn (Linux central server).
"""
import os
from dotenv import load_dotenv

# Load local environment variables from .env
load_dotenv()

from app import create_app

app = create_app()

if __name__ == "__main__":
    # For standalone local development
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=True)
