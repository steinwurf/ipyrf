#! /usr/bin/env python
# encoding: utf-8

import os
import hashlib
import os.path

APPNAME = "ipyrf"
VERSION = "1.5.0"


def options(opt):
    opts = opt.add_option_group("Test")
    opts.add_option(
        "--run_tests",
        action="store_true",
        default=False,
        dest="run_tests",
        help="Run the test suite.",
    )
    opts.add_option(
        "--filter",
        default=None,
        dest="filter",
        help='Select tests based on their name. E.g. "test_ipyrf"',
    )


def prepare_release(ctx):
    """Prepare a release."""
    # Rewrite versions
    with ctx.rewrite_file(filename="pyproject.toml") as f:
        pattern = r"version = \".+\""
        replacement = 'version = "{}"'.format(VERSION)
        f.regex_replace(pattern=pattern, replacement=replacement)


def build(ctx):
    if ctx.options.run_tests:
        pip_install, venv = _create_venv(ctx=ctx, location="test")

        if pip_install:
            venv.run("python -m pip install -e .")

        cmd_options = ""

        if ctx.options.filter:
            cmd_options += f"-k '{ctx.options.filter}'"

        venv.run(f"pytest -x {cmd_options} test")


def _create_venv(ctx, location):
    requirements_txt = os.path.join(location, "requirements.txt")
    requirements_in = os.path.join(location, "requirements.in")

    if not os.path.isfile(requirements_txt):
        with ctx.create_virtualenv() as venv:
            venv.run("python -m pip install pip-tools")
            venv.run(
                "pip-compile {} --output-file {}".format(
                    requirements_in, requirements_txt
                )
            )
    # Hash the requirements.txt
    sha1 = hashlib.sha1(
        (open(requirements_txt, "r").read()).encode("utf-8")
    ).hexdigest()[:6]

    # venv name
    name = f"venv-{location}-{sha1}"

    if os.path.isdir(name):
        # If directly already exits we should already have installed everything
        pip_install = False
    else:
        pip_install = True

    # Crate the venv
    venv = ctx.create_virtualenv(name=name, overwrite=False)

    if pip_install:
        venv.env["PIP_IGNORE_INSTALLED"] = ""
        venv.run("python -m pip install -r {}".format(requirements_txt))

    return pip_install, venv
