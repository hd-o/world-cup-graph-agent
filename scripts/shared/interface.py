import json
import questionary
import sys
import typer
from collections.abc import Callable
from rich.console import Console
from rich.panel import Panel
from rich.text import Text


console = Console()


def require_terminal() -> None:
    if not sys.stdin.isatty():
        raise ValueError("Non-interactive use requires explicit inputs and approvals")


def ask_text(message: str, default: str = "") -> str:
    require_terminal()
    answer = questionary.text(message, default=default).ask()
    if answer is None:
        raise typer.Abort()
    return answer


def ask_password(message: str) -> str:
    require_terminal()
    answer = questionary.password(message).ask()
    if answer is None:
        raise typer.Abort()
    return answer


def confirm_action(message: str, execution: bool = False) -> None:
    if execution:
        return
    require_terminal()
    if not questionary.confirm(message, default=False).ask():
        raise typer.Abort()


def choose_action(actions: list[str]) -> str:
    require_terminal()
    answer = questionary.select("Choose an operation", choices=[*actions, "Exit"]).ask()
    if answer is None or answer == "Exit":
        raise typer.Abort()
    return answer


def show_inventory(title: str, payload: object) -> None:
    content = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    )
    console.print(Panel(Text(content), title=Text(title)))


def run_cli(operation: Callable[[], object]) -> None:
    try:
        operation()
    except typer.Exit, typer.Abort:
        raise
    except KeyboardInterrupt, EOFError:
        console.print("Cancelled; completed steps are not rolled back.")
        raise typer.Exit(130) from None
    except Exception as error:
        # SDK exceptions can contain credentials, URLs, or request bodies.
        # Display only the class rather than rendering sensitive traceback locals.
        console.print(Text(f"Operation stopped ({type(error).__name__})."))
        console.print(
            "Check --help, required routing and protected inputs, toolchain, "
            "and cloud authentication. Non-terminal use requires explicit approvals."
        )
        console.print("Inspect status before retrying; no automatic rollback occurred.")
        raise typer.Exit(1) from None
