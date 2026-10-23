# Shared 96-well scheme contract

Use one canonical workbook, intended for FIA and a future compatible UMA. Consumer compatibility is separate from structure. See compatibility.json for the exact verified FIA source.

## Fixed locations

| Content | Location | Rule |
| --- | --- | --- |
| Physical column headers | Plate Map!B1:M1 | Integers 1–12 |
| Physical row headers | Plate Map!A2:A9 | A–H |
| Conditions | Plate Map!B2:M9 | Literal text; blank means unannotated |
| Comparison blocks | Condition-cell fill | Direct solid RGB, one color per block |
| Control | Condition-cell font | Bold, not inferred from its name |
| Plot layout | Plate Map!B15:M21 | One horizontal strip per block |
| Channel column headers | Channels!C5:N5 | Integers 1–12 |
| Channel assignments | Channels!C6:N37 | Four vertical cells per physical well |
| Row/channel labels | Channels!A6:B37 | Merged row labels A–H permitted in A; C1–C4 in B |
| Intersection headers | Channels!C43:N43 | Integers 1–12 |
| Requested intersections | Channels!C44:N51 | Literal Foci combinations per well |

Keep `Plate Map` and `Channels` exact. `Guide` and the editable choice list in Channels!P9:P32 are supporting material, not independent inputs.

For zero-based physical row r and one-based well column c:
- condition: row r+2, column c+1;
- Ck assignment: row 6+4r+(k-1), column c+2;
- intersection: row 44+r, column c+2.

Do not merge input cells, use formulas/errors as annotations, or encode input roles through conditional formatting. Convert theme colors to direct RGB only as an agreed change. Different fills may represent deliberately separate blocks; do not merge without approval.

## Conditions, controls and panels

Repeat a condition across wells with the same full label, fill and bold setting. The FIA baseline requires a full label to have one block/control role throughout the plate. If generic labels recur in different blocks, propose disambiguated names and ask before changing them.

Inferential comparisons need one explicitly identified control and at least one treatment per block. Descriptive reports may retain a missing/ambiguous control with a warning. Never designate it from its name alone.

Place each block once in PLOT LAYOUT as a contiguous horizontal strip. Include every condition in that block exactly once, with matching literal labels, direct RGB fills and bold flags. Separate neighboring strips on the same row with at least one blank column. Blocks may sit side by side, vertically or in a grid; each block's own strip is horizontal. Do not insert notes, ranks or formulas in empty input cells.

An old Order/Group list is evidence for a proposed layout, not an input region in this schema. Discuss its conversion and any loss of distinctions before writing.

## Channels and methods

Write `Method: marker name`, except for the exact token `Ignore`.

| Fixed method | Meaning in the FIA baseline |
| --- | --- |
| Nuclei | Exactly one segmentation channel per annotated well |
| Foci | Thresholded prepared foci and explicitly requested intersections |
| Nuclear intensity | Original-channel MAX intensity within nucleus masks |
| Field signal | Whole-field thresholded SUM32 intensity, area and objects |
| Fiber | Recorded channel, not measured by FIA |
| Ignore | Existing physical channel deliberately unused |
| Blank | Absent trailing channel, or unused well; never unknown data |

Existing channels are contiguous from C1. Never renumber after Ignore. Three-channel files have blank C4. Ask when channel count is unknown. `Nuclei: Nuclear stain` is allowed if the name is unknown but channel/role are confirmed; do not invent DAPI/Hoechst.

One channel has one method. A method/name identity cannot occur twice within a well. A marker can occupy different original channels across wells. Identity is case-sensitive after documented whitespace/Unicode normalization. A rename changes identity; do not repair spelling/case silently.

Choose methods by experimental intent, not marker name. This schema does not support placing Foci and Nuclear intensity together in one channel cell.

Each annotated well needs one Nuclei assignment. Unannotated wells have all channel/intersection cells blank. Do not fill the whole plate with a shared channel pattern when only some wells are used.

## Explicit intersections

Examples: `C2+C4`; `C2+C3+C4`; `C2+C4; C2+C3+C4`.

Use pairs/triples of distinct existing Foci channels. `+` means common mask intersection; semicolons separate requests. A triple never requests pairs implicitly. Blank requests none. Equivalent channel permutations are mathematically equivalent, but rewriting notation still belongs to the agreed plan. Multiple Foci channels do not imply an intersection request.

## Populations and provenance

Common nuclei use all annotated eligible wells; each marker uses only its method/name subset. Unassigned staining is not zero. Failed-image exclusions and report filters operate downstream, not as newly invented plate annotations.

Do not infer thresholds from a scheme or its photograph. Do not add thresholds, acquisition settings, doses or replicate labels to input grids unless supported by the consumer and approved.

The workbook alone cannot establish image count, actual source channels, imaging quality or biological independence. Report physical well counts separately from those claims.

FIA expects one `FIA*.xlsx` beside original images. A future UMA reader may differ. One common schema does not resolve incompatible prefixes. Verify discovery rules before claiming automatic use by both programs.
