"""Deephyper analytics - multi study documentation

usage:

::

    $ deephyper-analytics parse agebo_test1/agebo_test_382DS/deephyper.log
    $ deephyper-analytics parse agebo_test2/agebo_test_23HDS/deephyper.log
    $ deephyper-analytics notebook --type nas --output mynotebook.ipynb agebo_test1_2019-05-07_14.json agebo_test1_2019-05-07_14.json

"""

import os
from deephyper.core.plot.jn_loader import NbEdit

HERE = os.path.dirname(os.path.abspath(__file__))


def multi_analytics(path_to_data_file, output_path="dh-analytics-multi.ipynb"):
    editor = NbEdit(
        os.path.join(HERE, "stub/multi_analytics.ipynb"),
        path_to_save=output_path,
    )

    path_list = list()
    label_list = list()
    for i, el in enumerate(path_to_data_file):
        if ":" in el:
            label, p = tuple(el.split(":"))
        else:
            label, p = str(i), el
        path_list.append(p)
        label_list.append(label)

    text = "\n"
    for label, p in zip(label_list, path_list):
        text += f" - {label}:{p}\n"

    editor.edit(0, "{{path_to_data_file}}", text)

    editor.edit(1, "{{path_to_data_file}}", f"{str(path_list)}")

    editor.edit(1, "{{labels}}", f"{str(label_list)}")

    editor.write()

    editor.execute()


def add_subparser(subparsers):
    subparser_name = "multi"
    function_to_call = main

    parser_parse = subparsers.add_parser(
        subparser_name,
        help="Tool to generate analytics from multiple NAS experiment (jupyter notebook).",
    )
    parser_parse.add_argument(
        "path",
        type=str,
        nargs="+",
        help=f'Json files generated by "deephyper-analytics parse".',
    )

    return subparser_name, function_to_call


def main(path, *args, **kwargs):
    multi_analytics(path_to_data_file=path)
