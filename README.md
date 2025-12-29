# python-venv-mgr

Windows-friendly virtual environment manager backed by a base Python interpreter.

## Usage

```python
from pathlib import Path
from python_venv_mgr import VirtualEnvManager

manager = VirtualEnvManager(
    base_interpreter=Path("C:/python-embed/python.exe"),
    base_dir=Path("D:/venvs"),
)

venv_path = manager.create_venv(
    "analytics",
    requirements=["pandas==2.2.2", "numpy==1.26.4"],
)

installed = manager.list_installed_packages(venv_path)
print(installed)

matches = manager.find_venvs_by_requirements(["pandas==2.2.2", "numpy==1.26.4"])
print(matches)

manager.delete_venv("analytics")
```
