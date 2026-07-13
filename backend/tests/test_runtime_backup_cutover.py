from argparse import Namespace

import pytest

from scripts.cutover_memoir_narrative import _validate_run_args


def test_final_cutover_check_cannot_be_scoped_to_one_project():
    args = Namespace(stage="check", project_id="project-1", execute=False)

    with pytest.raises(RuntimeError, match="all active memoir projects"):
        _validate_run_args(args)


def test_final_cutover_check_rejects_write_mode():
    args = Namespace(stage="check", project_id=None, execute=True)

    with pytest.raises(RuntimeError, match="read-only"):
        _validate_run_args(args)
