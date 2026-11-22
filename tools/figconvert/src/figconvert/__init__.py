"""Convert vector and raster figures to raster images."""

import logging

__version__ = '0.1.1'

# Used as a library, figconvert logs nothing unless the program using
# it sets up logging; the window writes to a log file (figconvert.logs).
_package_logger = logging.getLogger(__name__)
_package_logger.addHandler(logging.NullHandler())
