"""CLI interface for system-tender."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import __version__
from .config import (
    DEFAULT_CONFIG_DIR,
    find_task,
    init_config_dir,
    list_tasks,
    load_global_config,
    load_task_config,
)
from .engine import run_task, save_run
from .models import GlobalConfig, OutputFormat, TaskConfig, ToolName


def _setup_logging(verbose: bool, task_name: str | None = None, run_id: str | None = None):
    """Configure logging for this run."""
    try:
        from .logger import setup_logging
        return setup_logging(
            task_name=task_name,
            run_id=run_id,
            verbose=verbose,
        )
    except Exception:
        # Fallback if logger module has issues
        import logging
        logging.basicConfig(
            level=logging.DEBUG if verbose else logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        return logging.getLogger("system-tender")


@click.group()
@click.version_option(__version__, prog_name="system-tender")
@click.option("--config-dir", type=click.Path(path_type=Path), default=None,
              help="Override config directory")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, config_dir: Path | None, verbose: bool):
    """system-tender: Smart cron powered by Claude.

    Like a tender on a train — keeps the engine running.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config_dir"] = config_dir


@main.command()
@click.argument("task_name", required=False)
@click.option("--prompt", "-p", type=str, help="Ad-hoc prompt (no task file needed)")
@click.option("--task-file", "-f", type=click.Path(exists=True, path_type=Path),
              help="Run from a specific task file")
@click.option("--model", "-m", type=str, help="Override model")
@click.option("--timeout", "-t", type=int, help="Override timeout (seconds)")
@click.option("--json-output", is_flag=True, help="Output full JSON result")
@click.pass_context
def run(
    ctx: click.Context,
    task_name: str | None,
    prompt: str | None,
    task_file: Path | None,
    model: str | None,
    timeout: int | None,
    json_output: bool,
):
    """Run a maintenance task.

    Examples:

      tender run brew-update

      tender run --prompt "Check disk usage and report anything over 80%"

      tender run --task-file ./my-task.toml
    """
    verbose = ctx.obj["verbose"]
    config_dir = ctx.obj.get("config_dir")
    global_config = load_global_config(config_dir)

    # Determine task config
    if task_file:
        task = load_task_config(task_file)
    elif task_name:
        path = find_task(task_name, global_config)
        if not path:
            click.echo(f"Task not found: {task_name}", err=True)
            click.echo(f"Looked in: {global_config.tasks_dir}", err=True)
            sys.exit(1)
        task = load_task_config(path)
    elif prompt:
        # Ad-hoc task
        task = TaskConfig(
            name="adhoc",
            description="Ad-hoc task from command line",
            prompt=prompt,
            allowed_tools=[ToolName.SHELL, ToolName.FILE_READ],
        )
    else:
        click.echo("Provide a task name, --prompt, or --task-file", err=True)
        sys.exit(1)

    # Apply overrides
    if model:
        task.model = model
    if timeout:
        task.timeout = timeout

    logger = _setup_logging(verbose, task_name=task.name)

    # Run it
    result = run_task(task, global_config, prompt_override=prompt if task_name else None)

    # Save run history
    try:
        run_path = save_run(result, global_config)
        logger.debug("Run saved to %s", run_path)
    except Exception as e:
        logger.warning("Failed to save run history: %s", e)

    # Output
    if json_output:
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(result.to_summary())
        if result.output:
            click.echo()
            click.echo(result.output)

    sys.exit(0 if result.success else 1)


@main.command("list")
@click.pass_context
def list_cmd(ctx: click.Context):
    """List configured tasks."""
    config_dir = ctx.obj.get("config_dir")
    global_config = load_global_config(config_dir)

    tasks = list_tasks(global_config)
    if not tasks:
        click.echo(f"No tasks found in {global_config.tasks_dir}")
        click.echo("Run 'tender init' to create example tasks.")
        return

    for task in tasks:
        tools = ", ".join(t.value for t in task.allowed_tools)
        schedule = task.schedule or "manual"
        click.echo(f"  {task.name:<20} {task.description[:50]:<50} [{tools}] ({schedule})")


@main.command()
@click.pass_context
def init(ctx: click.Context):
    """Initialize config directory with examples."""
    config_dir = ctx.obj.get("config_dir")
    path = init_config_dir(config_dir)
    click.echo(f"Initialized: {path}")
    click.echo(f"  Config:  {path / 'config.toml'}")
    click.echo(f"  Tasks:   {path / 'tasks/'}")
    click.echo(f"  Logs:    {path / 'logs/'}")
    click.echo(f"  Runs:    {path / 'runs/'}")


@main.command()
@click.option("--last", "-n", type=int, default=10, help="Number of recent runs")
@click.pass_context
def history(ctx: click.Context, last: int):
    """Show recent run history."""
    config_dir = ctx.obj.get("config_dir")
    global_config = load_global_config(config_dir)
    runs_dir = global_config.runs_dir

    if not runs_dir.exists():
        click.echo("No run history yet.")
        return

    files = sorted(runs_dir.glob("*.json"), reverse=True)[:last]
    if not files:
        click.echo("No run history yet.")
        return

    for f in files:
        try:
            data = json.loads(f.read_text())
            status = "OK" if data.get("success") else "FAIL"
            name = data.get("task_name", "?")
            run_id = data.get("run_id", "?")
            duration = data.get("duration_ms", 0)
            tokens = data.get("input_tokens", 0) + data.get("output_tokens", 0)
            click.echo(f"  [{status}] {name:<20} {run_id}  {duration/1000:.1f}s  {tokens} tokens  {f.stem[:15]}")
        except Exception:
            continue


@main.command("generate-schedule")
@click.argument("task_name")
@click.option("--type", "sched_type", type=click.Choice(["launchd", "systemd", "cron", "auto"]),
              default="auto", help="Scheduler type")
@click.option("--schedule", "-s", type=str, help="Override cron schedule (e.g. '0 6 * * *')")
@click.option("--install", is_flag=True, help="Install the schedule (launchd only for now)")
@click.pass_context
def generate_schedule(ctx: click.Context, task_name: str, sched_type: str, schedule: str | None, install: bool):
    """Generate scheduler config for a task."""
    config_dir = ctx.obj.get("config_dir")
    global_config = load_global_config(config_dir)

    path = find_task(task_name, global_config)
    if not path:
        click.echo(f"Task not found: {task_name}", err=True)
        sys.exit(1)

    task = load_task_config(path)
    cron_schedule = schedule or task.schedule

    if not cron_schedule:
        click.echo("No schedule defined. Use --schedule or set schedule in task config.", err=True)
        sys.exit(1)

    try:
        from .scheduler import (
            detect_scheduler,
            generate_crontab_entry,
            generate_launchd_plist,
            generate_systemd_units,
            install_launchd,
        )
    except ImportError as e:
        click.echo(f"Scheduler module not available: {e}", err=True)
        sys.exit(1)

    if sched_type == "auto":
        sched_type = detect_scheduler()
        click.echo(f"Detected scheduler: {sched_type}")

    if sched_type == "launchd":
        if install:
            plist_path = install_launchd(task.name, cron_schedule, task.env or None)
            click.echo(f"Installed: {plist_path}")
            click.echo(f"Load with: launchctl load {plist_path}")
        else:
            click.echo(generate_launchd_plist(task.name, cron_schedule, task.env or None))

    elif sched_type == "systemd":
        service, timer = generate_systemd_units(task.name, cron_schedule, task.env or None)
        click.echo("# --- service unit ---")
        click.echo(service)
        click.echo("\n# --- timer unit ---")
        click.echo(timer)

    elif sched_type == "cron":
        click.echo(generate_crontab_entry(task.name, cron_schedule, task.env or None))


if __name__ == "__main__":
    main()
