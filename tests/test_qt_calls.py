# Calls that read fine and run only as far as Qt: PyQt6 refuses them, and the error comes from a click or a timer,
# when it's too late for anyone to see it (logs/errors.log).

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
PACKAGES = ("browser", "tools")


def _calls(attr):
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(), str(path))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr:
                    yield path.relative_to(ROOT), node


def test_no_single_shot_timer_is_given_a_context_object():
    """C++'s QTimer::singleShot(msec, context, slot) isn't in PyQt6: a third argument raises TypeError when the call
    runs. It broke the page's right-click menu, and GitHub's repos before that. A QTimer the object owns does it."""
    wrong = [f"{path}:{node.lineno}" for path, node in _calls("singleShot")
             if len(node.args) >= 3 and "TimerType" not in ast.unparse(node.args[1])]
    assert not wrong, f"QTimer.singleShot with a context object (PyQt6 has none): {wrong}"
