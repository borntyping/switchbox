default: black mypy pytest

black:
  poetry run black switchbox/
  poetry run black --check switchbox/

mypy:
  poetry run mypy switchbox/

pytest:
  poetry run pytest
