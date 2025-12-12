"""Service for parsing and managing VSCode tasks.json."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskInput:
    """Definition of an input prompt."""
    id: str
    type: str = "promptString"
    description: str = ""
    default: str = ""


@dataclass
class Task:
    """A task from tasks.json."""
    label: str
    command: str
    type: str = "shell"
    group: str | None = None


class TasksService:
    """Parses and manages VSCode tasks.json."""

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path)
        self.tasks_file = self.project_path / ".vscode" / "tasks.json"
        self._tasks: list[Task] = []
        self._inputs: dict[str, TaskInput] = {}

    def has_tasks_file(self) -> bool:
        """Check if tasks.json exists."""
        return self.tasks_file.exists()

    def load(self) -> bool:
        """Load and parse tasks.json. Returns True if successful."""
        if not self.tasks_file.exists():
            self._tasks = []
            self._inputs = {}
            return False

        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                # Remove comments (JSON with comments - JSONC)
                content = self._strip_jsonc_comments(f.read())
                data = json.loads(content)

            self._parse_tasks(data)
            self._parse_inputs(data)
            return True

        except (json.JSONDecodeError, OSError, KeyError) as e:
            print(f"Error parsing tasks.json: {e}")
            self._tasks = []
            self._inputs = {}
            return False

    def _strip_jsonc_comments(self, content: str) -> str:
        """Remove // and /* */ comments from JSON content."""
        # Remove single-line comments
        content = re.sub(r'//.*?$', '', content, flags=re.MULTILINE)
        # Remove multi-line comments
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        return content

    def _parse_tasks(self, data: dict):
        """Parse tasks from data."""
        self._tasks = []
        tasks_data = data.get("tasks", [])

        for task_data in tasks_data:
            if not isinstance(task_data, dict):
                continue

            label = task_data.get("label", "")
            if not label:
                continue

            # Get command - can be string or in args
            command = task_data.get("command", "")
            args = task_data.get("args", [])
            if args and isinstance(args, list):
                args_str = " ".join(str(a) for a in args)
                command = f"{command} {args_str}".strip()

            if not command:
                continue

            task = Task(
                label=label,
                command=command,
                type=task_data.get("type", "shell"),
                group=self._get_group(task_data.get("group")),
            )
            self._tasks.append(task)

    def _get_group(self, group_data) -> str | None:
        """Extract group name from group field."""
        if group_data is None:
            return None
        if isinstance(group_data, str):
            return group_data
        if isinstance(group_data, dict):
            return group_data.get("kind")
        return None

    def _parse_inputs(self, data: dict):
        """Parse inputs from data."""
        self._inputs = {}
        inputs_data = data.get("inputs", [])

        for input_data in inputs_data:
            if not isinstance(input_data, dict):
                continue

            input_id = input_data.get("id", "")
            if not input_id:
                continue

            task_input = TaskInput(
                id=input_id,
                type=input_data.get("type", "promptString"),
                description=input_data.get("description", ""),
                default=input_data.get("default", ""),
            )
            self._inputs[input_id] = task_input

    def get_tasks(self) -> list[Task]:
        """Get list of tasks."""
        return self._tasks.copy()

    def get_inputs(self) -> dict[str, TaskInput]:
        """Get input definitions."""
        return self._inputs.copy()

    def substitute_variables(self, command: str, context: dict | None = None) -> str:
        """Replace ${var} placeholders in command."""
        context = context or {}

        # Built-in variables
        variables = {
            "workspaceFolder": str(self.project_path),
            "workspaceFolderBasename": self.project_path.name,
        }
        variables.update(context)

        def replace_var(match):
            var_name = match.group(1)
            return variables.get(var_name, match.group(0))

        # Replace ${varName} patterns (but not ${input:id})
        result = re.sub(r'\$\{(?!input:)(\w+)\}', replace_var, command)
        return result

    def get_required_inputs(self, command: str) -> list[str]:
        """Get list of input IDs required by command."""
        pattern = r'\$\{input:(\w+)\}'
        return re.findall(pattern, command)

    def substitute_inputs(self, command: str, input_values: dict[str, str]) -> str:
        """Replace ${input:id} placeholders with values."""
        def replace_input(match):
            input_id = match.group(1)
            return input_values.get(input_id, "")

        return re.sub(r'\$\{input:(\w+)\}', replace_input, command)
