# Contributing to drip-sdk-python

Thank you for your interest in contributing to the Drip Python SDK!

## Development Setup

1. Clone the repository:
```bash
git clone https://github.com/MichaelLevin5908/drip-sdk-python.git
cd drip-sdk-python
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -e ".[dev]"
```

4. Run tests:
```bash
pytest
```

## Project Structure

```
src/drip/
├── __init__.py        # Full SDK exports
├── client.py          # Full Drip & AsyncDrip classes
├── core.py            # Core SDK (lightweight)
├── models.py          # Data models
├── errors.py          # Exception classes
├── resilience.py      # Retry, circuit breaker, rate limiter
├── stream.py          # StreamMeter for batching
├── utils.py           # Utility functions
├── middleware/        # Framework middleware
│   ├── fastapi.py
│   └── flask.py
└── integrations/      # Third-party integrations
    └── langchain.py
```

## Making Changes

1. Create a new branch:
```bash
git checkout -b feature/your-feature-name
```

2. Make your changes and ensure:
   - All tests pass (`pytest`)
   - Type checking passes (`mypy src`)
   - Linting passes (`ruff check src tests`)

3. Commit your changes with a descriptive message

4. Open a Pull Request

## Code Style

- Use type hints for all functions
- No `Any` types - use `object` or specific types with type guards
- Add docstrings for public APIs
- Follow existing patterns in the codebase
- Use `ruff` for formatting and linting

## Testing

- Write tests for new features
- Test both success and error paths
- Tests use pytest
- Aim for good coverage

## Questions?

Open an issue or reach out to the maintainers.
