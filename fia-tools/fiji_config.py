"""Shared Fiji endpoint for every FIA command that initializes ImageJ."""

# Pin Fiji for reproducible initialization across all FIA commands.
FIJI_ENDPOINT = "sc.fiji:fiji:2.14.0"

# To use Fiji without a pinned version, replace the assignment above with:
# FIJI_ENDPOINT = "sc.fiji:fiji"
