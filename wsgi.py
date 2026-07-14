"""WSGI entry for production hosts (Render / PythonAnywhere / etc.)."""
from webapp.app import create_app

app = create_app()
