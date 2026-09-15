import os
from pathlib import Path

from dotenv import load_dotenv

# Load the repo-root .env (TINFOIL_API_KEY, OPENAI_API_KEY) regardless of cwd, and
# hardcode the Tinfoil inference endpoint for the openai-api/tinfoil/* provider.
_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT.parent / ".env")
os.environ.setdefault("TINFOIL_BASE_URL", "https://inference.tinfoil.sh/v1")

BENCH = _ROOT / "benchmarks"

AILUMINATE_CSV = BENCH / "ailuminate" / "demo_en_us_1200.csv"
HARMBENCH_CSV = BENCH / "harmbench" / "behaviors_text_all.csv"
