"""Shared Fiji endpoint for every FIA command that initializes ImageJ."""

import os

# Pin Fiji for reproducible initialization across all FIA commands.
FIJI_ENDPOINT = "sc.fiji:fiji:2.14.0"


def configure_runtime():
    """Called before ImageJ initialization; preserve all numerical/JVM heap options."""
    if os.environ.get('FIA_RUN_ID') and os.environ.get('TMPDIR'):
        import scyjava
        scyjava.config.add_option('-Djava.io.tmpdir=' + os.environ['TMPDIR'])


configure_runtime()

# To use Fiji without a pinned version, replace the assignment above with:
# FIJI_ENDPOINT = "sc.fiji:fiji"
