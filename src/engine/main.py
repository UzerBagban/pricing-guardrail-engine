"""Pricing Guardrail Engine Main Entry Point.

Starts the autonomous pricing guardrail & margin defense engine.
Per README:
  python -m src.engine.main
"""

from src.engine.orchestrator import main

if __name__ == "__main__":
    main()
