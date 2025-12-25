"""Load environment variables from .env file."""
from pathlib import Path
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

# Load .env file if it exists
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)
else:
    # Also try loading from parent directory (for flexibility)
    parent_env = ROOT.parent / ".env"
    if parent_env.exists():
        load_dotenv(parent_env, override=False)

