from __future__ import print_function

"""Extract SF/SM/ESF1 reports for user-selected Abaqus ODB steps.

This script reuses extract_sfsm_reports.py in the same directory.

Examples:

    rem List the steps in each ODB without writing reports
    abaqus python extract_sfsm_reports_selected_steps.py --list-steps

    rem Select individual steps by exact name
    abaqus python extract_sfsm_reports_selected_steps.py --steps Step-2 Step-5

    rem Select steps 3 through 7 by their 1-based order in each ODB
    abaqus python extract_sfsm_reports_selected_steps.py --step-range 3 7

    rem Select an inclusive range using the first and last step names
    abaqus python extract_sfsm_reports_selected_steps.py --step-range Preload Operation

If neither --steps nor --step-range is supplied, all steps are processed.
By default the last frame containing ESF1, SF, and SM is used in each step.
"""

import argparse
import os
import sys
import traceback

from odbAccess import openOdb

import extract_sfsm_reports as core


def parse_arguments():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description=(
            "Write ESF1/SF/SM nodal reports for selected steps in one ODB or "
            "every ODB in a directory."
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
            "Process this ODB only. May be repeated. Relative paths are "
            "resolved against --input-dir."
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
        default=core.DEFAULT_NODE_SET,
        help="Qualified instance node set (default: PART-1-1.START).",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=core.DEFAULT_FRAME_INDEX,
        help=(
            "Zero-based frame index. Use -1 for the last frame containing all "
            "requested fields (default: -1)."
        ),
    )
    parser.add_argument(
        "--list-steps",
        action="store_true",
        help="List each ODB's ordered steps and exit without writing reports.",
    )

    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--steps",
        nargs="+",
        default=None,
        metavar="STEP",
        help="One or more exact step names to include.",
    )
    selection.add_argument(
        "--step-range",
        nargs=2,
        default=None,
        metavar=("START", "END"),
        help=(
            "Inclusive range. Use 1-based step numbers (for example 3 7) or "
            "the exact first and last step names."
        ),
    )

    args = parser.parse_args()
    if args.frame_index < -1:
        parser.error("--frame-index must be -1 or zero or greater")
    if args.output_name and len(args.odb) != 1:
        parser.error("--output-name requires exactly one --odb")
    return args


def available_step_names(odb_path):
    odb = None
    try:
        odb = openOdb(path=odb_path, readOnly=True)
        return list(odb.steps.keys())
    finally:
        if odb is not None:
            odb.close()


def find_name_case_insensitive(available_steps, requested_name):
    requested_upper = requested_name.upper()
    for step_name in available_steps:
        if step_name.upper() == requested_upper:
            return step_name
    return None


def integer_value(text):
    try:
        return int(text)
    except ValueError:
        return None


def select_named_steps(available_steps, requested_steps):
    selected = []
    notes = []
    for requested_name in requested_steps:
        actual_name = find_name_case_insensitive(available_steps, requested_name)
        if actual_name is None:
            notes.append("requested step '{0}' is not present".format(requested_name))
        elif actual_name not in selected:
            selected.append(actual_name)
    return selected, notes


def select_step_range(available_steps, endpoints):
    start_text, end_text = endpoints
    start_number = integer_value(start_text)
    end_number = integer_value(end_text)

    if (start_number is None) != (end_number is None):
        raise ValueError(
            "--step-range endpoints must both be step numbers or both be step names"
        )

    if start_number is not None:
        if start_number < 1 or end_number < 1:
            raise ValueError("numeric step ranges are 1-based and must be positive")
        if start_number > end_number:
            raise ValueError("the start of --step-range must not exceed the end")
        if start_number > len(available_steps):
            return [], [
                "range starts at {0}, but this ODB contains only {1} step(s)".format(
                    start_number, len(available_steps)
                )
            ]

        actual_end = min(end_number, len(available_steps))
        selected = available_steps[start_number - 1 : actual_end]
        notes = []
        if end_number > len(available_steps):
            notes.append(
                "range end {0} was limited to the last available step ({1})".format(
                    end_number, len(available_steps)
                )
            )
        return selected, notes

    start_name = find_name_case_insensitive(available_steps, start_text)
    end_name = find_name_case_insensitive(available_steps, end_text)
    missing = []
    if start_name is None:
        missing.append("start step '{0}' is not present".format(start_text))
    if end_name is None:
        missing.append("end step '{0}' is not present".format(end_text))
    if missing:
        return [], missing

    start_index = available_steps.index(start_name)
    end_index = available_steps.index(end_name)
    if start_index > end_index:
        raise ValueError(
            "start step '{0}' occurs after end step '{1}'".format(
                start_name, end_name
            )
        )
    return available_steps[start_index : end_index + 1], []


def select_steps(available_steps, requested_steps, step_range):
    if requested_steps is not None:
        return select_named_steps(available_steps, requested_steps)
    if step_range is not None:
        return select_step_range(available_steps, step_range)
    return list(available_steps), []


def print_step_list(odb_path, step_names):
    print(odb_path)
    if not step_names:
        print("  (no analysis steps)")
        return
    for index, step_name in enumerate(step_names, 1):
        print("  {0:>3}: {1}".format(index, step_name))


def main():
    args = parse_arguments()
    input_dir = os.path.abspath(args.input_dir)
    odb_paths = core.find_odb_files(input_dir, args.odb)

    if args.list_steps:
        for odb_path in odb_paths:
            print_step_list(odb_path, available_step_names(odb_path))
        return 0

    output_dir = os.path.abspath(args.output_dir or input_dir)
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    jobs = core.build_jobs(odb_paths, output_dir, args.output_name)

    failures = []
    for odb_path, report_path in jobs:
        try:
            available_steps = available_step_names(odb_path)
            selected_steps, notes = select_steps(
                available_steps, args.steps, args.step_range
            )
            if not selected_steps:
                raise ValueError(
                    "No requested steps are available. Available steps: {0}".format(
                        ", ".join(available_steps)
                    )
                )

            print("Selected steps for {0}:".format(os.path.basename(odb_path)))
            for step_name in selected_steps:
                print("  {0}".format(step_name))
            for note in notes:
                print("  Note: {0}.".format(note))

            core.write_report(
                odb_path=odb_path,
                report_path=report_path,
                node_set_name=args.node_set,
                step_names=selected_steps,
                frame_index=args.frame_index,
            )
        except Exception as exc:
            failures.append((odb_path, str(exc)))
            print("FAILED:  {0}".format(odb_path))
            print("         {0}".format(exc))
            traceback.print_exc()

    print(
        "Completed: {0} succeeded, {1} failed.".format(
            len(jobs) - len(failures), len(failures)
        )
    )
    if failures:
        print("Failed ODB files:")
        for odb_path, message in failures:
            print("  {0}: {1}".format(odb_path, message))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
