"""python -m ctt.tui"""
import sys

from ctt.tui.app import main

sys.exit(main(sys.argv[1:]))
