"""Launcher - run installer from source without package issues"""
import sys
from pathlib import Path

# Add src parent to path so src.main works
root = Path(__file__).parent
sys.path.insert(0, str(root))

from src.main import main

if __name__ == "__main__":
    main()
