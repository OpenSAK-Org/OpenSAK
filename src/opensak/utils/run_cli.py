"""
src/opensak/utils/run_cli.py — Entry point for the `opensak` CLI command.
"""


def main() -> None:
    # A headless command (`opensak import …`, #937) or the GUI.
    from opensak.cli import entry
    entry()
