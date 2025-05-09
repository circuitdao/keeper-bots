import logging

class LevelFilter(logging.Filter):
    def __init__(self, min_level=None, max_level=None):
        super().__init__()
        # Convert string level names to integers
        self.min_level = self._convert_level(min_level)
        self.max_level = self._convert_level(max_level)

    def _convert_level(self, level):
        if level is None:
            return None
        if isinstance(level, int):
            return level
        if isinstance(level, str):
            try:
                # Convert level name to integer
                # CRITICAL -> 50
                # ERROR -> 40
                # WARN -> 30
                # INFO -> 20
                # DEBUG -> 10
                # NOTSET -> 0
                return logging.getLevelName(level.upper())
            except ValueError:
                raise ValueError(f"Invalid log level name: {level}")
        raise ValueError(f"Level must be int or str, got {type(level)}")

    def filter(self, record):
        level = record.levelno
        if self.min_level is not None and level < self.min_level:
            return False
        if self.max_level is not None and level > self.max_level:
            return False
        return True
