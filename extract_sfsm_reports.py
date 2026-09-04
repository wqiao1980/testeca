from __future__ import print_function

"""Extract ESF1, SF, and SM from Abaqus ODB files without GUI-only APIs.

Run with the Abaqus Python environment, for example:

    abaqus python extract_sfsm_reports.py --odb model.odb
    abaqus python extract_sfsm_reports.py --input-dir C:\\path\\to\\odbs

The first command processes one ODB.  The second command processes every ODB
in the input directory (non-recursively).  By default reports are written next
to the ODB files. Integration-point results are extrapolated to element nodes
and the contributions at each requested node are averaged. By default every
ODB step is included, using its last frame that contains ESF1, SF, and SM.
"""

import argparse
import datetime
import math
import os
import sys
import traceback

from abaqusConstants import ELEMENT_NODAL, ON
from odbAccess import openOdb


DEFAULT_NODE_SET = "PART-1-1.START"
DEFAULT_FRAME_INDEX = -1
FIELD_NAMES = ("ESF1", "SF", "SM")
COLUMN_SPECS = (
    ("ESF1", 0, "ESF1"),
    ("SF", 0, "SF.SF1"),
    ("SF", 1, "SF.SF2"),
    ("SF", 2, "SF.SF3"),
    ("SM", 0, "SM.SM1"),
    ("SM", 1, "SM.SM2"),
    ("SM", 2, "SM.SM3"),
)


def parse_arguments():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description=(
            "Write ESF1/SF/SM nodal reports for one ODB or every ODB in a "
            "directory. Uses odbAccess only; no CAE window is opened."
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
        help="Qualified instance node set, for example PART-1-1.START.",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        default=None,
        help="ODB step names to include (default: every step in each ODB).",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=DEFAULT_FRAME_INDEX,
        help=(
            "Zero-based frame index in each step. Use -1 for the last frame "
            "containing all requested fields (default: -1)."
        ),
    )
    args = parser.parse_args()

    if args.frame_index < -1:
        parser.error("--frame-index must be -1 or zero or greater")
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
    """Keep the ODB basename and replace only its extension with .rpt."""
    stem = os.path.splitext(os.path.basename(odb_path))[0]
    return stem + ".rpt"


def repository_key(repository, requested_name):
    """Find an Abaqus repository key without depending on letter case."""
    if requested_name in repository:
        return requested_name
    requested_upper = requested_name.upper()
    for key in repository.keys():
        if key.upper() == requested_upper:
            return key
    return None


def resolve_instance_node_set(odb, qualified_name):
    if "." not in qualified_name:
        raise ValueError(
            "--node-set must include instance and set names, for example "
            "PART-1-1.START"
        )

    instance_name, set_name = qualified_name.split(".", 1)
    instance_key = repository_key(odb.rootAssembly.instances, instance_name)
    if instance_key is None:
        raise ValueError(
            "Instance '{0}' is missing. Available instances: {1}".format(
                instance_name, ", ".join(odb.rootAssembly.instances.keys())
            )
        )

    instance = odb.rootAssembly.instances[instance_key]
    set_key = repository_key(instance.nodeSets, set_name)
    if set_key is None:
        raise ValueError(
            "Node set '{0}' is missing from instance '{1}'. Available sets: {2}".format(
                set_name, instance_key, ", ".join(instance.nodeSets.keys())
            )
        )

    node_set = instance.nodeSets[set_key]
    node_labels = sorted(node.label for node in node_set.nodes)
    if not node_labels:
        raise ValueError("Node set '{0}' is empty.".format(qualified_name))
    return instance_key, set_key, node_labels


def select_requested_frames(odb, requested_steps, frame_index):
    """Return usable (step name, frame index, frame) tuples and skip messages."""
    available_steps = list(odb.steps.keys())
    step_names = available_steps if requested_steps is None else requested_steps

    for step_name in step_names:
        if step_name not in available_steps:
            raise ValueError(
                "Step '{0}' is missing. Available steps: {1}".format(
                    step_name, ", ".join(available_steps)
                )
            )

    selected = []
    skipped = []
    for step_name in step_names:
        frames = odb.steps[step_name].frames
        if len(frames) == 0:
            skipped.append((step_name, "the step contains no frames"))
            continue

        if frame_index == -1:
            candidate_indices = range(len(frames) - 1, -1, -1)
        elif frame_index >= len(frames):
            skipped.append(
                (
                    step_name,
                    "frame index {0} is unavailable; the step has {1} frame(s)".format(
                        frame_index, len(frames)
                    ),
                )
            )
            continue
        else:
            candidate_indices = (frame_index,)

        selected_frame = None
        last_missing = []
        for candidate_index in candidate_indices:
            frame = frames[candidate_index]
            field_names = frame.fieldOutputs.keys()
            last_missing = [name for name in FIELD_NAMES if name not in field_names]
            if not last_missing:
                selected_frame = (step_name, candidate_index, frame)
                break

        if selected_frame is None:
            if frame_index == -1:
                reason = "no frame contains all requested fields: {0}".format(
                    ", ".join(FIELD_NAMES)
                )
            else:
                reason = "frame {0} is missing field output(s): {1}".format(
                    frame_index, ", ".join(last_missing)
                )
            skipped.append((step_name, reason))
        else:
            selected.append(selected_frame)

    if not selected:
        details = "; ".join(
            "{0}: {1}".format(step_name, reason) for step_name, reason in skipped
        )
        raise ValueError(
            "No usable steps were found for ESF1, SF, and SM. {0}".format(details)
        )
    return selected, skipped


def field_value_data(value):
    try:
        data = value.data
    except Exception:
        data = value.dataDouble

    if isinstance(data, (int, float)):
        return (float(data),)
    return tuple(float(item) for item in data)


def average_element_nodal_field(field_output, instance_name, node_labels):
    """Extrapolate to element nodes, then average contributions by node label."""
    target_labels = set(node_labels)
    contributions = dict((label, []) for label in node_labels)
    element_nodal = field_output.getSubset(position=ELEMENT_NODAL, readOnly=ON)

    for value in element_nodal.values:
        if value.nodeLabel not in target_labels:
            continue
        value_instance = getattr(value, "instance", None)
        if value_instance is not None and value_instance.name.upper() != instance_name.upper():
            continue
        contributions[value.nodeLabel].append(field_value_data(value))

    averaged = {}
    counts = {}
    for node_label in node_labels:
        values = contributions[node_label]
        if not values:
            raise ValueError(
                "No element-nodal values were found at node {0} for field '{1}'.".format(
                    node_label, field_output.name
                )
            )
        component_count = len(values[0])
        for value in values:
            if len(value) != component_count:
                raise ValueError(
                    "Inconsistent component count for field '{0}' at node {1}.".format(
                        field_output.name, node_label
                    )
                )
        averaged[node_label] = tuple(
            sum(value[index] for value in values) / float(len(values))
            for index in range(component_count)
        )
        counts[node_label] = len(values)
    return averaged, counts


def extract_frame_data(frame, instance_name, node_labels):
    fields = {}
    contribution_counts = {}
    for field_name in FIELD_NAMES:
        averaged, counts = average_element_nodal_field(
            frame.fieldOutputs[field_name], instance_name, node_labels
        )
        fields[field_name] = averaged
        contribution_counts[field_name] = counts

    rows = []
    for node_label in node_labels:
        values = []
        for field_name, component_index, unused_title in COLUMN_SPECS:
            field_values = fields[field_name][node_label]
            if component_index >= len(field_values):
                raise ValueError(
                    "Field '{0}' at node {1} has {2} component(s); component {3} "
                    "was requested.".format(
                        field_name,
                        node_label,
                        len(field_values),
                        component_index + 1,
                    )
                )
            values.append(field_values[component_index])
        rows.append((node_label, values))
    return rows, contribution_counts


def engineering_format(value):
    """Six-significant-digit engineering notation similar to a CAE report."""
    if math.isnan(value) or math.isinf(value):
        return str(value)
    if value == 0.0:
        return "0.00000"

    exponent = int(math.floor(math.log10(abs(value)) / 3.0) * 3)
    mantissa = value / (10.0 ** exponent)
    digits_before_decimal = int(math.floor(math.log10(abs(mantissa)))) + 1
    decimal_places = max(0, 6 - digits_before_decimal)
    mantissa_text = ("{0:.%df}" % decimal_places).format(mantissa)

    if abs(float(mantissa_text)) >= 1000.0:
        exponent += 3
        mantissa /= 1000.0
        digits_before_decimal = int(math.floor(math.log10(abs(mantissa)))) + 1
        decimal_places = max(0, 6 - digits_before_decimal)
        mantissa_text = ("{0:.%df}" % decimal_places).format(mantissa)

    return "{0}E{1:+03d}".format(mantissa_text, exponent)


def write_table(report_file, rows):
    titles = [spec[2] for spec in COLUMN_SPECS]
    header = "{0:>16}".format("Node Label")
    header += "".join("{0:>16}".format(title) for title in titles)
    locations = "{0:>16}".format("")
    locations += "".join("{0:>16}".format("@Loc 1") for unused in titles)

    report_file.write(header + "\n")
    report_file.write(locations + "\n")
    report_file.write("-" * len(header) + "\n")
    for node_label, values in rows:
        line = "{0:>16d}".format(node_label)
        line += "".join("{0:>16}".format(engineering_format(value)) for value in values)
        report_file.write(line + "\n")

    columns = list(zip(*(values for unused_label, values in rows)))
    minimums = [min(column) for column in columns]
    maximums = [max(column) for column in columns]
    totals = [sum(column) for column in columns]
    min_nodes = [rows[list(column).index(min(column))][0] for column in columns]
    max_nodes = [rows[list(column).index(max(column))][0] for column in columns]

    report_file.write("\n\n")
    report_file.write("{0:>16}".format("Minimum"))
    report_file.write("".join("{0:>16}".format(engineering_format(v)) for v in minimums))
    report_file.write("\n")
    report_file.write("{0:>16}".format("At Node"))
    report_file.write("".join("{0:>16d}".format(v) for v in min_nodes))
    report_file.write("\n\n")
    report_file.write("{0:>16}".format("Maximum"))
    report_file.write("".join("{0:>16}".format(engineering_format(v)) for v in maximums))
    report_file.write("\n")
    report_file.write("{0:>16}".format("At Node"))
    report_file.write("".join("{0:>16d}".format(v) for v in max_nodes))
    report_file.write("\n\n")
    report_file.write("{0:>16}".format("Total"))
    report_file.write("".join("{0:>16}".format(engineering_format(v)) for v in totals))
    report_file.write("\n\n\n")


def write_report(odb_path, report_path, node_set_name, step_names, frame_index):
    odb = None
    try:
        print("Opening: {0}".format(odb_path))
        odb = openOdb(path=odb_path, readOnly=True)
        selected_frames, skipped_steps = select_requested_frames(
            odb, step_names, frame_index
        )
        instance_name, set_name, node_labels = resolve_instance_node_set(
            odb, node_set_name
        )

        extracted_frames = []
        max_contributions = 0
        for step_name, selected_frame_index, frame in selected_frames:
            rows, contribution_counts = extract_frame_data(
                frame, instance_name, node_labels
            )
            for field_counts in contribution_counts.values():
                max_contributions = max(max_contributions, max(field_counts.values()))
            extracted_frames.append((step_name, selected_frame_index, frame, rows))

        with open(report_path, "w") as report_file:
            report_file.write("*" * 80 + "\n")
            report_file.write(
                "Field Output Report, written {0}\n\n".format(
                    datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y")
                )
            )
            for step_name, selected_frame_index, frame, rows in extracted_frames:
                report_file.write("Source 1\n")
                report_file.write("---------\n\n")
                report_file.write("   ODB: {0}\n".format(odb_path.replace("\\", "/")))
                report_file.write("   Step: {0}\n".format(step_name))
                report_file.write("   Frame: {0}\n\n".format(frame.description))
                report_file.write("Loc 1 : Nodal values from source 1\n\n")
                report_file.write('Output sorted by column "Node Label".\n\n')
                report_file.write(
                    "Field Output reported at nodes for region: {0}.{1}\n".format(
                        instance_name, set_name
                    )
                )
                report_file.write(
                    "   Computation algorithm: EXTRAPOLATE_COMPUTE_AVERAGE\n"
                )
                report_file.write("   Averaged at nodes\n\n")
                write_table(report_file, rows)

        print("Wrote:   {0}".format(report_path))
        if frame_index == -1:
            frame_message = "the last usable frame in each step"
        else:
            frame_message = "frame index {0} in each step".format(frame_index)
        print(
            "Included {0} step(s), using {1}.".format(
                len(extracted_frames), frame_message
            )
        )
        for skipped_step, reason in skipped_steps:
            print("Skipped step '{0}': {1}.".format(skipped_step, reason))
        if max_contributions > 1:
            print(
                "Note: up to {0} element-nodal contributions were arithmetically "
                "averaged at a requested node.".format(max_contributions)
            )
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
    print("Extraction mode: odbAccess (no CAE/Viewer display-group API).")
    print("Found {0} ODB file(s).".format(len(jobs)))

    failures = []
    for odb_path, report_path in jobs:
        try:
            write_report(
                odb_path=odb_path,
                report_path=report_path,
                node_set_name=args.node_set,
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
