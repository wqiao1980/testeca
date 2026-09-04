Abaqus SF/SM/ESF1 Selected-Steps Report - User Instructions
========================================================

Required files
--------------

Keep these two Python files together in the same folder:

1. extract_sfsm_reports.py
2. extract_sfsm_reports_selected_steps.py

Run the commands below from an Abaqus Command Prompt.


List the available steps
------------------------

This command lists the ordered steps in every ODB in the current folder. It
does not create reports.

abaqus python extract_sfsm_reports_selected_steps.py --list-steps

To list the steps in ODBs located in another folder:

abaqus python "C:\python_aba\takeoutSFSM\extract_sfsm_reports_selected_steps.py" --input-dir "C:\Data\P1-2\00_NewFA\12inTRF" --list-steps


Output selected steps by name
-----------------------------

List each requested step after --steps. Put quotation marks around a step name
if it contains spaces.

abaqus python extract_sfsm_reports_selected_steps.py --steps "Step-2" "Step-5" "Step-8"


Output a range using step positions
-----------------------------------

Step positions are 1-based and the range is inclusive. The following example
outputs steps 3, 4, 5, 6, and 7:

abaqus python extract_sfsm_reports_selected_steps.py --step-range 3 7

If an ODB has fewer steps than the requested ending position, the script uses
the last available step and prints a note.


Output a range using step names
-------------------------------

The first and last named steps are both included:

abaqus python extract_sfsm_reports_selected_steps.py --step-range "Preload" "Operation"


Process the 12inTRF folder
--------------------------

Example: output steps 3 through 7 from every ODB in 12inTRF:

abaqus python "C:\python_aba\takeoutSFSM\extract_sfsm_reports_selected_steps.py" --input-dir "C:\Data\P1-2\00_NewFA\12inTRF" --step-range 3 7

Example: output selected named steps from every ODB in 12inTRF:

abaqus python "C:\python_aba\takeoutSFSM\extract_sfsm_reports_selected_steps.py" --input-dir "C:\Data\P1-2\00_NewFA\12inTRF" --steps "Step-2" "Step-5"


Process one ODB only
--------------------

Add --odb followed by the ODB filename:

abaqus python extract_sfsm_reports_selected_steps.py --odb "model.odb" --step-range 3 7


Default behavior and output
---------------------------

- If neither --steps nor --step-range is supplied, every step is processed.
- The last frame containing ESF1, SF, and SM is used in each selected step.
- A step without all three fields is skipped and the reason is printed.
- The default node set is PART-1-1.START.
- Each report keeps the ODB basename and changes only the extension:

  model.odb -> model.rpt

- Reports are written into the input ODB folder unless --output-dir is used.
- Existing reports with the same names are overwritten.


Optional arguments
------------------

Use another node set:

--node-set "INSTANCE-NAME.NODE-SET-NAME"

Use a particular zero-based frame index instead of the last usable frame:

--frame-index 1

Write reports to another folder:

--output-dir "C:\path\to\reports"

Assign a custom report name when processing exactly one ODB:

--output-name "custom_report.rpt"


Important command syntax
------------------------

Correct:

--odb "model.odb"

Incorrect:

--model.odb
