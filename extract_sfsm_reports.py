from __future__ import print_function

"""Create SF/SM/ESF1 field reports from Abaqus ODB files without a GUI.

Run with the Abaqus Python environment, for example:

    abaqus viewer noGUI=extract_sfsm_reports.py -- --odb model.odb
    abaqus viewer noGUI=extract_sfsm_reports.py -- --input-dir C:\\path\\to\\odbs

The first command processes one ODB.  The second command processes every ODB
in the input directory (non-recursively).  By default reports are written next
to the ODB files.
"""

import argparse
import os
import re
import sys
import traceback

from abaqus import session
from abaqusConstants import ALL, INTEGRATION_POINT, NODAL, OFF
import displayGroupOdbToolset as dgo


DEFAULT_NODE_SET = "PART-1-1.START"
DEFAULT_STEPS = ("Step-6", "Step-7")
DEFAULT_FRAME_INDEX = 1
REPORT_VARIABLES = (
    ("ESF1", INTEGRATION_POINT),
    ("SF", INTEGRATION_POINT),
    ("SM", INTEGRATION_POINT),
)


def parse_arguments():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description=(
            "Write ESF1/SF/SM nodal reports for one ODB or every ODB in a "
            "directory. Must be run with 'abaqus viewer noGUI=...'."
        )
    )
    parser.add_argument(
        "--input-dir",
        default=script_dir,
        help="Directory searched for ODB files (default: script directory).",
    )
    parser.add_argument(
        "--odb",
        action="append",
        default=[],
        help=(
            "Process this ODB only. May be repeated. A relative path is "
            "resolved against --input-dir. If omitted, all ODBs in "
            "--input-dir are processed."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Report directory (default: --input-dir).",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="Report filename for a single --odb run only.",
    )
    parser.add_argument(
        "--node-set",
        default=DEFAULT_NODE_SET,
        help="Qualified assembly node set used by the display group.",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        default=list(DEFAULT_STEPS),
        help="ODB step names to include (default: Step-6 Step-7).",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=DEFAULT_FRAME_INDEX,
        help="Zero-based frame index in each selected step (default: 1).",
    )
    args = parser.parse_args()

    if args.frame_index < 0:
        parser.error("--frame-index must be zero or greater")
    if args.output_name and len(args.odb) != 1:
        parser.error("--output-name requires exactly one --odb")
    return args


def find_odb_files(input_dir, requested):
    input_dir = os.path.abspath(input_dir)
    if not os.path.isdir(input_dir):
        raise ValueError("Input directory does not exist: {0}".format(input_dir))

    if requested:
        paths = []
        for item in requested:
            path = item if os.path.isabs(item) else os.path.join(input_dir, item)
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                raise ValueError("ODB file does not exist: {0}".format(path))
            if not path.lower().endswith(".odb"):
                raise ValueError("Not an ODB file: {0}".format(path))
            paths.append(path)
    else:
        paths = [
            os.path.join(input_dir, name)
            for name in os.listdir(input_dir)
            if name.lower().endswith(".odb")
            and os.path.isfile(os.path.join(input_dir, name))
        ]

    paths.sort(key=lambda value: value.lower())
    if not paths:
        raise ValueError("No ODB files found in: {0}".format(input_dir))
    return paths


def default_report_name(odb_path):
    """Use the original 10inPLET naming when recognizable; otherwise use the ODB stem."""
    stem = os.path.splitext(os.path.basename(odb_path))[0]
    match = re.match(
        r"^GdM_(?P<size>[^_]+)Prod_.*_PLETanchor(?:_(?P<case>.+))?New$",
        stem,
        re.IGNORECASE,
    )
    if match:
        case_name = match.group("case") or "0deg"
        return "{0}PLET_{1}.rpt".format(match.group("size"), case_name)
    return stem + ".rpt"


def get_viewport(odb):
    viewport_names = list(session.viewports.keys())
    if viewport_names:
        viewport = session.viewports[viewport_names[0]]
    else:
        viewport = session.Viewport(
            name="Viewport: 1", origin=(0.0, 0.0), width=150.0, height=100.0
        )
    viewport.setValues(displayedObject=odb)
    return viewport


def validate_requested_data(odb, step_names, frame_index):
    available_steps = list(odb.steps.keys())
    for step_name in step_names:
        if step_name not in available_steps:
            raise ValueError(
                "Step '{0}' is missing. Available steps: {1}".format(
                    step_name, ", ".join(available_steps)
                )
            )

        frames = odb.steps[step_name].frames
        if frame_index >= len(frames):
            raise ValueError(
                "Step '{0}' has {1} frame(s); frame index {2} is unavailable.".format(
                    step_name, len(frames), frame_index
                )
            )

        field_names = frames[frame_index].fieldOutputs.keys()
        missing = [name for name in ("ESF1", "SF", "SM") if name not in field_names]
        if missing:
            raise ValueError(
                "Step '{0}', frame {1} is missing field output(s): {2}".format(
                    step_name, frame_index, ", ".join(missing)
                )
            )
    return available_steps


def write_report(odb_path, report_path, node_set, step_names, frame_index):
    odb = None
    try:
        print("Opening: {0}".format(odb_path))
        odb = session.openOdb(name=odb_path, readOnly=True)
        available_steps = validate_requested_data(odb, step_names, frame_index)

        viewport = get_viewport(odb)
        leaf = dgo.LeafFromNodeSets(nodeSets=(node_set,))
        viewport.odbDisplay.displayGroup.replace(leaf=leaf)

        active_frames = tuple((name, (frame_index,)) for name in step_names)
        odb_display_name = viewport.odbDisplay.name
        session.odbData[odb_display_name].setValues(activeFrames=active_frames)

        # step/frame are required even with stepFrame=ALL.  The last active
        # step mirrors the CAE-generated replay command used for the example.
        reference_step_index = available_steps.index(step_names[-1])
        session.writeFieldReport(
            fileName=report_path,
            append=OFF,
            sortItem="Node Label",
            odb=odb,
            step=reference_step_index,
            frame=frame_index,
            outputPosition=NODAL,
            variable=REPORT_VARIABLES,
            stepFrame=ALL,
        )
        print("Wrote:   {0}".format(report_path))
    finally:
        if odb is not None:
            odb.close()


def build_jobs(odb_paths, output_dir, output_name):
    jobs = []
    report_paths_seen = set()
    for odb_path in odb_paths:
        report_name = output_name or default_report_name(odb_path)
        report_path = os.path.abspath(os.path.join(output_dir, report_name))
        normalized = os.path.normcase(report_path)
        if normalized in report_paths_seen:
            raise ValueError(
                "More than one ODB maps to report path: {0}".format(report_path)
            )
        report_paths_seen.add(normalized)
        jobs.append((odb_path, report_path))
    return jobs


def main():
    args = parse_arguments()
    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir or input_dir)
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    odb_paths = find_odb_files(input_dir, args.odb)
    jobs = build_jobs(odb_paths, output_dir, args.output_name)
    print("Found {0} ODB file(s).".format(len(jobs)))

    failures = []
    for odb_path, report_path in jobs:
        try:
            write_report(
                odb_path=odb_path,
                report_path=report_path,
                node_set=args.node_set,
                step_names=args.steps,
                frame_index=args.frame_index,
            )
        except Exception as exc:
            failures.append((odb_path, str(exc)))
            print("FAILED:  {0}".format(odb_path))
            print("         {0}".format(exc))
            traceback.print_exc()

    print("Completed: {0} succeeded, {1} failed.".format(
        len(jobs) - len(failures), len(failures)
    ))
    if failures:
        print("Failed ODB files:")
        for odb_path, message in failures:
            print("  {0}: {1}".format(odb_path, message))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
