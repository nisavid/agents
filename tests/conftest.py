"""Keep tests and their subprocesses from changing validated runtime trees."""

import os
import sys


os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
