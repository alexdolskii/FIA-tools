# Review and clarification workflow

## Read evidence

Read Excel values and actual styles, not only an Excel screenshot. For PDF inspect pages as well as text. For photos establish row/column labels and orientation before transcription; never guess coordinates from a rotated or mirrored view.

Record exact observed labels and locations. Preserve the source. Keep reconstructed mappings as internal working data until approved; never export a guessed reconstruction as a finished plate.

For images check separately: text/dose units; row/column labels; fills and legends versus lighting artifacts; boldness versus resolution artifacts; crossed-out wells/handwriting/arrows; legend scope; channel order versus wavelength/color labels; presence of a fourth channel.

Describe uncertainty by location, e.g. “C8 boldness is unclear”, without invented confidence scores. Ask for a clearer crop or text only when necessary.

## Check a completed scheme

Accept a finished, potentially correct workbook without assuming it needs conversion. Inspect all 96 positions and run the bundled read-only validator on the supplied XLSX; use the verified target reader when available. Do not save, re-export or replace the workbook in check-only mode. Different cosmetic formatting is not an error if the required contract is satisfied.

Report the checked scope and a verdict: no errors found, errors found, or incomplete verification requiring clarification. Separate errors with exact locations, unresolved information, design warnings and unverified software compatibility. If the workbook passes the available checks, say so and state that no correction is required within that scope. Do not manufacture changes or ask the user to reconfirm clear data. Unknown consumer versions do not prevent baseline structural checking.

A screenshot, PDF or text may support a content review but cannot establish the underlying XLSX cell types, styles, formulas or reader compatibility. A valid workbook does not establish experimental validity or agreement with raw images. Request only the missing evidence needed for the user's intended level of verification.

## Ask unresolved questions

Tie questions to concrete cells/wells. Possible topics:

1. Used, unused and explicitly excluded wells.
2. Exact conditions, concentrations, units and ambiguous abbreviations.
3. Comparison blocks and their controls.
4. Physical C1–C4 channels for each staining pattern.
5. Method and marker per channel; Ignore versus a recorded Fiber channel.
6. Pool matching conditions across stainings for nuclei or retain separate groups.
7. Exact pair/triple Foci intersections, if any.
8. Panel positions and within-panel order.
9. Consumer/version and experiment name.

Do not ask about unsupported thresholds, microscopy examples or biological interpretation simply to fill a questionnaire. Ask about statistical intent when it affects control validation. Keep replication warnings separate from format errors.

## Present changes

For corrections show source location, observed value/style, proposed change and reason. Group identical changes by unambiguous well ranges. Include technical conversions, e.g. theme-to-RGB and Order/Group to PLOT LAYOUT.

For new schemes show well mapping, channel patterns, controls and layout. Reuse explicit instructions as prior agreement; ask only about new choices and the remaining plan.

If blocked, state the missing input. Do not infer agreement from silence or a generic request to inspect. A clear later instruction may override the standing preference for discussion for that task; do not silently assume it does.

## Populations and completion

Count physical wells, separately for nuclei and every method/marker. Past examples are never default assignments.

Flag one well per condition for well-level comparisons. Do not substitute images/nuclei as independent biological replicates, duplicate wells, pool experiments or merge stainings without agreement. Two wells do not establish adequate power.

After agreement author one XLSX per experiment, verify all 96 positions, render it, and run the bundled validator and available target reader. Give a short change summary and the file. Keep semantic agreement, structure, consumer compatibility and actual-image verification as separate claims.
