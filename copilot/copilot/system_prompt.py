"""System prompt builder for the Anthropic API.

Loads the prompt template from prompt.txt (or a custom path) and injects
dynamic node information.
"""

import os


def _load_template(prompt_path: str | None = None) -> str:
    path = prompt_path or os.path.join(os.path.dirname(__file__), "prompt.txt")
    path = os.path.expanduser(path)
    with open(path) as f:
        return f.read()


def build_system_prompt(
    nodes: list[dict], prompt_path: str | None = None
) -> str:
    """Build the full system prompt with dynamic node list.

    Args:
        nodes: List of node info dicts with 'name' and 'tools' keys.
        prompt_path: Optional path to a custom prompt template file.
            Defaults to the bundled prompt.txt.

    Returns:
        The complete system prompt string.
    """
    if nodes:
        lines = [f"You have access to {len(nodes)} DNS node(s) in this site:\n"]
        for node in nodes:
            tools_str = ", ".join(node.get("tools", []))
            lines.append(f"- **{node['name']}** — Tools: {tools_str}")
        lines.append("\nWhen investigating site-wide issues, always query all nodes.")
        nodes_section = "\n".join(lines)
    else:
        nodes_section = "No nodes are currently connected."

    template = _load_template(prompt_path)
    return template.replace("{nodes_section}", nodes_section)
