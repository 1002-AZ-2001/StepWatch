"""StepWatch : documentation locale des interventions Windows."""

import logging

__version__ = "0.2.0"

# Messages written to the diagnostic file only when diagnostics.setup() runs.
logging.getLogger(__name__).addHandler(logging.NullHandler())
