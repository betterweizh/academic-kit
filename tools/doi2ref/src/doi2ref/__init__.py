"""Turn DOIs into formatted citations, BibTeX, RIS or CSL JSON."""

import logging

__version__ = '0.1.1'

# Used as a library, doi2ref logs nothing unless the program using it
# sets up logging; the window writes to a log file (doi2ref.logs).
_package_logger = logging.getLogger(__name__)
_package_logger.addHandler(logging.NullHandler())
