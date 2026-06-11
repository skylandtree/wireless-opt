from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netopt.seed import seed_database


if __name__ == "__main__":
    seed_database(force=True)
    print("Mock wireless optimization data has been generated.")
