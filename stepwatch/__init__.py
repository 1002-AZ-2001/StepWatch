"""StepWatch : documentation locale des interventions informatiques."""

import logging

__version__ = "1.2.0"

# Messages written to the diagnostic file only when diagnostics.setup() runs.
logging.getLogger(__name__).addHandler(logging.NullHandler())
