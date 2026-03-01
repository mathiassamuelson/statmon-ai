"""System prompt builder for the Anthropic API.

Loads the prompt template from prompt.txt and injects dynamic node information.
"""

import os


def _load_template() -> str:
    template_path = os.path.join(os.path.dirname(__file__), "prompt.txt")
    with open(template_path) as f:
        return f.read()


def build_system_prompt(nodes: list[dict]) -> str:
    """Build the full system prompt with dynamic node list.

    Args:
        nodes: List of node info dicts with 'name' and 'tools' keys.

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

    template = _load_template()
    return template.replace("{nodes_section}", nodes_section)
